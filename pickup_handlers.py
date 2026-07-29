"""
pickup_handlers.py
~~~~~~~~~~~~~~~~~~~
User-facing Apple pickup commands — extracted from Tracker-alert's
handlers.py, which also handles every other site's /add flow, the
plan/trial/item-limit system, bulk-add, etc. Only what's needed here:

- /trackpickup, /mypickups, /untrackpickup — unchanged from Tracker-alert
  (these were already Apple-only and self-contained; no plan/limit checks).
- /add — a MINIMAL, apple.com-only version. Tracker-alert's real /add is a
  multi-step FSM flow tied into access.py's plan/trial/item-limit system,
  supports bulk-add, and has an Amazon-specific target-price sub-flow —
  none of which exists in this repo. This version just validates the URL
  is an apple.com product, resolves a name, and inserts it into
  database.products with site="apple" — the only thing
  worker.run_apple_official_pickup_cycle actually needs.
"""

import html
import logging
from urllib.parse import urlparse

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from bs4 import BeautifulSoup

from checkers import detect_site, fetch_page, apple as apple_checker
from config import get_site_label
from database import (
    get_user_lang, is_site_locked, add_pickup_tracking, list_pickup_tracking,
    remove_pickup_tracking, add_product,
)
from translations import t

logger = logging.getLogger(__name__)

router = Router()


def _auto_name(url: str, site: str) -> str:
    """Derive a short display name from a URL when no better name is available."""
    try:
        path = urlparse(url).path.rstrip("/")
        slug = path.split("/")[-1][:40] if path else "product"
    except Exception:
        slug = "product"
    return f"{get_site_label(site)}: {slug}"


# ---------------------------------------------------------------------------
# /add — minimal, apple.com-only (see module docstring)
# ---------------------------------------------------------------------------

@router.message(Command("add"))
async def cmd_add(message: Message, command: CommandObject):
    user_id = message.from_user.id
    lang = get_user_lang(user_id)

    if not command.args:
        await message.answer(
            "Usage: <code>/add &lt;apple_url&gt; [name]</code>\n"
            "This repo only tracks apple.com product pages (feeds the "
            "official-store pickup auto-check).",
            parse_mode="HTML",
        )
        return

    parts = command.args.strip().split(maxsplit=1)
    url = parts[0]
    name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None

    if not url.startswith(("http://", "https://")) or detect_site(url) != "apple":
        await message.answer(
            "⚠️ This repo only supports apple.com product URLs.", parse_mode="HTML"
        )
        return

    if not name:
        try:
            resp = await fetch_page(url, render_js=apple_checker.NEEDS_JS, timeout=30.0, site="apple")
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            name = apple_checker._extract_product_name(soup)
        except Exception as exc:
            logger.warning(f"[add] product page fetch failed for {url!r}: {exc}")
            name = None
        if not name:
            name = _auto_name(url, "apple")

    ok, msg = add_product(user_id, name, url, "apple")
    if ok:
        await message.answer(
            t("product_added", lang, name=name, site=get_site_label("apple"), url=url), parse_mode="HTML"
        )
    else:
        await message.answer(f"⚠️ {msg}")


# ---------------------------------------------------------------------------
# /trackpickup — personal pickup-availability tracking (database.
# pickup_tracking + worker.run_pickup_check_cycle). Separate from /add
# above — own table, own check cycle, no item limit.
# ---------------------------------------------------------------------------

@router.message(Command("trackpickup"))
async def cmd_trackpickup(message: Message, command: CommandObject):
    user_id = message.from_user.id
    lang = get_user_lang(user_id)

    try:
        if not command.args:
            await message.answer(t("trackpickup_usage", lang), parse_mode="HTML")
            return

        parts = command.args.strip().split()
        if len(parts) < 2:
            await message.answer(t("trackpickup_usage", lang), parse_mode="HTML")
            return

        url, pincodes = parts[0], parts[1:]

        if not url.startswith(("http://", "https://")) or detect_site(url) != "apple":
            await message.answer(t("trackpickup_invalid_url", lang), parse_mode="HTML")
            return

        if is_site_locked("apple", user_id):
            await message.answer(
                t("store_locked", lang, site=get_site_label("apple")), parse_mode="HTML"
            )
            return

        for pincode in pincodes:
            if not pincode.isdigit() or len(pincode) != 6:
                await message.answer(
                    t("trackpickup_invalid_pincode", lang, pincode=pincode), parse_mode="HTML"
                )
                return

        try:
            resp = await fetch_page(url, render_js=apple_checker.NEEDS_JS, timeout=30.0)
            resp.raise_for_status()
            html_text = resp.text
        except Exception as exc:
            logger.error(f"[trackpickup] product page fetch failed for {url!r}: {exc}")
            await message.answer(t("trackpickup_sku_failed", lang), parse_mode="HTML")
            return

        soup = BeautifulSoup(html_text, "html.parser")
        sku = apple_checker._extract_sku(soup, html_text)
        if not sku:
            await message.answer(t("trackpickup_sku_failed", lang), parse_mode="HTML")
            return

        name = apple_checker._extract_product_name(soup) or _auto_name(url, "apple")

        ok, msg = add_pickup_tracking(user_id, name, url, sku, pincodes)
        if ok:
            await message.answer(
                t("trackpickup_added", lang, name=name, pincodes=", ".join(pincodes)),
                parse_mode="HTML",
            )
        else:
            await message.answer(f"⚠️ {msg}")
    except Exception as exc:
        logger.error(f"[trackpickup] unexpected error for user {user_id}: {exc}", exc_info=True)
        try:
            await message.answer(t("unexpected_error", lang), parse_mode="HTML")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# /mypickups — on-demand, right-now pickup-availability check for every
# product tracked via /trackpickup. Reuses checkers.apple.check_pickup_row
# directly, same as the background cycle.
# ---------------------------------------------------------------------------

def _format_store_list(stores: list[dict]) -> str:
    parts = []
    for s in stores:
        name = html.escape(s.get("store_name") or "(unnamed store)")
        location = s.get("location")
        parts.append(f"{name} ({html.escape(location)})" if location else name)
    return ", ".join(parts)


def _format_mypickups_results(rows_with_results: list[tuple[dict, dict]], lang: str) -> str:
    lines = [t("mypickups_header", lang), ""]
    for row, results in rows_with_results:
        lines.append(f"📦 <b>{html.escape(row['name'])}</b>")
        for pincode in row["pincodes"]:
            if pincode not in results:
                lines.append(t("mypickups_line_check_failed", lang, pincode=pincode))
                continue
            stores = results[pincode]
            if stores:
                lines.append(
                    t("mypickups_line_available", lang, pincode=pincode, stores=_format_store_list(stores))
                )
            else:
                lines.append(t("mypickups_line_unavailable", lang, pincode=pincode))
        lines.append("")
    return "\n".join(lines).rstrip()


@router.message(Command("mypickups"))
async def cmd_mypickups(message: Message):
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    try:
        rows = list_pickup_tracking(user_id)
        if not rows:
            await message.answer(t("mypickups_empty", lang), parse_mode="HTML")
            return

        progress = await message.answer(
            t("mypickups_checking", lang, count=len(rows)), parse_mode="HTML"
        )

        # Sequential, not concurrent (2026-07-29) — concurrent row checks
        # here could exceed playwright_scraper's own MAX_CONCURRENT_
        # CHECKS=2 browser-slot ceiling, producing more "check failed"
        # results under load than with fewer items tracked. checkers.
        # apple's _playwright_fallback_lock (shared with the background
        # stagger loop) already guarantees at most one Playwright-backed
        # check runs system-wide at a time; going sequential here too
        # avoids piling up requests behind that lock for no benefit,
        # since this command's own rows would just serialize on it anyway.
        rows_with_results: list[tuple[dict, dict]] = []
        for row in rows:
            try:
                results = await apple_checker.check_pickup_row(message.bot, row)
            except Exception as exc:
                logger.error(
                    f"[mypickups] check failed for tracking #{row['id']}: {exc}", exc_info=True
                )
                results = {}
            rows_with_results.append((row, results))

        await progress.edit_text(
            _format_mypickups_results(rows_with_results, lang), parse_mode="HTML"
        )
    except Exception as exc:
        logger.error(f"[mypickups] unexpected error for user {user_id}: {exc}", exc_info=True)
        try:
            await message.answer(t("unexpected_error", lang), parse_mode="HTML")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# /untrackpickup — stop tracking a pickup item.
# ---------------------------------------------------------------------------

@router.message(Command("untrackpickup"))
async def cmd_untrackpickup(message: Message):
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    rows = list_pickup_tracking(user_id)

    if not rows:
        await message.answer(t("untrackpickup_empty", lang))
        return

    buttons = [
        [
            InlineKeyboardButton(
                text=f"🗑 {r['name']} ({', '.join(r['pincodes'])})",
                callback_data=f"untrackpickup:{r['id']}",
            )
        ]
        for r in rows
    ]
    buttons.append(
        [InlineKeyboardButton(text="❌ Cancel", callback_data="untrackpickup:cancel")]
    )

    await message.answer(
        t("untrackpickup_prompt", lang),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("untrackpickup:"))
async def callback_untrackpickup(call: CallbackQuery):
    payload = call.data.split(":", 1)[1]

    if payload == "cancel":
        await call.message.edit_text("❌ Removal cancelled.")
        await call.answer()
        return

    try:
        tracking_id = int(payload)
    except ValueError:
        await call.answer("Invalid selection.", show_alert=True)
        return

    deleted = remove_pickup_tracking(call.from_user.id, tracking_id)
    if deleted:
        await call.message.edit_text(
            f"✅ Pickup tracking <b>#{tracking_id}</b> has been removed.",
            parse_mode="HTML",
        )
    else:
        await call.message.edit_text(
            "⚠️ Could not remove that tracked item. It may have already been removed."
        )
    await call.answer()
