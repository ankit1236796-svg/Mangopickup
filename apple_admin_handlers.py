"""
apple_admin_handlers.py
~~~~~~~~~~~~~~~~~~~~~~~~
Apple pickup diagnostic/admin commands — extracted from Tracker-alert's
admin_handlers.py, which also handles plans, approvals, WhatsApp, other
sites' debug commands, etc. Only the Apple-pickup-related commands are
here: /debugpickup, /debugpickupraw, /debugpickupflow,
/debugpickupavailability, /debugpickupmessage, /debugpickupmessagestress,
/debugpickupstatus, /debugpickupevents, /debugzipcodevalidation.

Router is filtered to ADMIN_USER_ID only, same as the original. Several
commands additionally hardcode the same admin id — kept as-is from the
original for parity, harmless since it's this same repo's own admin.
"""

import asyncio
import json
import logging
import time
from collections import Counter

import httpx
from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from bs4 import BeautifulSoup

from checkers import fetch_page, apple
from config import ADMIN_USER_ID
from database import (
    is_site_locked,
    get_all_pickup_tracking,
    list_channel_forward_pickup,
    get_forwarding_pause_info,
    get_recent_pickup_alert_events,
)

logger = logging.getLogger(__name__)

router = Router()
router.message.filter(F.from_user.id == ADMIN_USER_ID)

# Hardcoded admin id kept from the original Tracker-alert commands (all of
# these existing debug commands additionally checked this exact id on top
# of the router's own ADMIN_USER_ID filter).
_DEBUG_PICKUP_ADMIN_ID = 5004721766
_DEBUG_PICKUP_RAW_ADMIN_ID = 5004721766
_DEBUG_PICKUP_FLOW_ADMIN_ID = 5004721766
_DEBUG_PICKUP_AVAILABILITY_ADMIN_ID = 5004721766
_DEBUG_PICKUP_MESSAGE_ADMIN_ID = 5004721766
_DEBUG_PICKUP_MESSAGE_STRESS_MAX_ATTEMPTS = 20
_DEBUG_PICKUP_STATUS_ADMIN_ID = 5004721766
_DEBUG_PICKUP_EVENTS_ADMIN_ID = 5004721766
_DEBUG_ZIPCODE_VALIDATION_ADMIN_ID = 5004721766


async def _debug_send(message: Message, text: str) -> None:
    """Send debug-command output as plain text (parse_mode=None), never the
    bot's default HTML parse mode — the extracted page text and the URLs
    these commands echo back can contain <, >, & which Telegram's HTML
    entity parser rejects."""
    try:
        await message.answer(text, parse_mode=None)
    except Exception as exc:
        logger.error(f"[debug] failed to send a debug output message: {exc}")
        try:
            await message.answer(f"⚠️ Failed to send a debug output message: {exc}", parse_mode=None)
        except Exception:
            pass


@router.message(Command("debugpickup"))
async def cmd_debugpickup(message: Message, command: CommandObject):
    if message.from_user.id != _DEBUG_PICKUP_ADMIN_ID:
        return
    if not command.args:
        await message.answer(
            "Usage: <code>/debugpickup &lt;apple_url&gt; &lt;pincode&gt;</code>", parse_mode="HTML"
        )
        return

    parts = command.args.strip().split()
    if len(parts) < 2:
        await message.answer(
            "Usage: <code>/debugpickup &lt;apple_url&gt; &lt;pincode&gt;</code>", parse_mode="HTML"
        )
        return
    url, pincode = parts[0], parts[1]

    await _debug_send(message, f"🔍 Fetching product page (render={apple.NEEDS_JS}): {url}")
    try:
        resp = await fetch_page(url, render_js=apple.NEEDS_JS, timeout=30.0)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        await _debug_send(message, f"⚠️ Product page fetch failed: {exc}")
        return

    soup = BeautifulSoup(html, "html.parser")
    sku = apple._extract_sku(soup, html)
    if not sku:
        await _debug_send(
            message,
            "⚠️ Could not extract a SKU/part number from this page (checked JSON-LD "
            "sku/offers.sku, inline partNumber, inline sku) — cannot call the "
            "fulfillment-messages API without one.",
        )
        return
    await _debug_send(message, f"✅ Extracted SKU: {sku!r}")

    target = apple._build_fulfillment_target(sku, pincode)
    await _debug_send(
        message,
        f"🔍 Calling fulfillment-messages API for pincode {pincode} — direct "
        f"httpx GET with real Cookie/User-Agent headers from APPLE_COOKIES/"
        f"APPLE_USER_AGENT env vars, no Scrape.do/Zyte involved "
        f"(see checkers/apple.py's _fetch_pickup_availability):\n{target}",
    )

    data, method, diagnostics = await apple._fetch_pickup_availability(sku, pincode, url)

    diag_lines = ["Diagnostics:"]
    for method_label, err in diagnostics:
        diag_lines.append(f"  • {method_label}: {'✅ succeeded' if err is None else f'❌ {err}'}")
    await _debug_send(message, "\n".join(diag_lines))

    if data is None:
        await _debug_send(
            message,
            "⚠️ fulfillment-messages call failed — see the reason above and "
            "Railway logs for the exact exception/status/body. If this is a "
            "401/403 or non-JSON response, APPLE_COOKIES has likely expired "
            "and needs a refresh.",
        )
        return

    await _debug_send(message, f"✅ Succeeded via method={method!r}")

    raw_json = json.dumps(data, indent=2)
    await _debug_send(message, f"Raw JSON response ({len(raw_json)} chars, sending in full):")
    _CHUNK_SIZE = 4000
    for i in range(0, len(raw_json), _CHUNK_SIZE):
        await _debug_send(message, raw_json[i:i + _CHUNK_SIZE])

    stores = (data.get("body") or {}).get("content", {}).get("pickupMessage", {}).get("stores", [])
    if not stores:
        await _debug_send(
            message,
            f"No stores returned for pincode {pincode} — common for most Indian "
            f"pincodes given Apple's sparse retail network (see checkers/apple.py's "
            f"design note); not necessarily a bug.",
        )
        return

    lines = [f"— {len(stores)} store(s) found for pincode {pincode} —"]
    for store in stores:
        part_info = (store.get("partsAvailability") or {}).get(sku, {})
        pickup_display = part_info.get("pickupDisplay", "(missing)")
        store_name = store.get("storeName", "(unknown store)")
        lines.append(f"{store_name}: pickupDisplay={pickup_display!r}")
    await _debug_send(message, "\n".join(lines))

    verdict = apple._evaluate_pickup_availability(data, sku)
    await _debug_send(
        message,
        f"Verdict via the existing _evaluate_pickup_availability (True = confirmed "
        f"pickup-available somewhere, None = inconclusive/unavailable): {verdict!r}",
    )


@router.message(Command("debugpickupraw"))
async def cmd_debugpickupraw(message: Message, command: CommandObject):
    """
    Cookie string comes from either of two places:
      1. Inline as a 3rd command argument — fine for short cookie jars, but
         Telegram truncates message TEXT at 4096 chars, and a real Apple
         session's Cookie header can get close to that.
      2. REPLY to a message with a .txt (or any text) file attached,
         containing just the cookie string, with a 2-arg command
         (<apple_url> <pincode>, no inline cookie string).
    """
    if message.from_user.id != _DEBUG_PICKUP_RAW_ADMIN_ID:
        return
    usage = (
        "Usage: <code>/debugpickupraw &lt;apple_url&gt; &lt;pincode&gt; &lt;cookie_string&gt;</code>\n"
        "cookie_string is a real browser's full raw Cookie header value "
        "(name1=value1; name2=value2; ...), pasted verbatim — this bypasses "
        "the DB/env session entirely for this one call only.\n\n"
        "Cookie string too long to safely fit in one Telegram message? "
        "Instead attach it as a .txt file and REPLY to that message with "
        "<code>/debugpickupraw &lt;apple_url&gt; &lt;pincode&gt;</code> "
        "(2 args, no inline cookie string) — the file's contents are used "
        "as the cookie string instead."
    )
    if not command.args:
        await message.answer(usage, parse_mode="HTML")
        return

    parts = command.args.strip().split(maxsplit=2)
    if len(parts) < 2:
        await message.answer(usage, parse_mode="HTML")
        return
    url, pincode = parts[0], parts[1]

    if len(parts) >= 3:
        cookie_string = parts[2]
    else:
        document = message.reply_to_message.document if message.reply_to_message else None
        if document is None:
            await _debug_send(
                message,
                "⚠️ No inline cookie string given (3rd argument) and no file found in "
                "the replied-to message. Either paste the cookie string as a 3rd "
                "argument, or reply to a message with a .txt file attached.",
            )
            return
        try:
            buffer = await message.bot.download(document)
            cookie_string = buffer.read().decode("utf-8").replace("\r", "").replace("\n", "").strip()
        except Exception as exc:
            await _debug_send(message, f"⚠️ Could not download/read the attached file: {exc}")
            return
        if not cookie_string:
            await _debug_send(message, "⚠️ The attached file is empty.")
            return
        await _debug_send(message, f"✅ Read cookie string from attached file ({len(cookie_string)} chars).")

    await _debug_send(message, f"🔍 Fetching product page (render={apple.NEEDS_JS}) to extract SKU: {url}")
    try:
        resp = await fetch_page(url, render_js=apple.NEEDS_JS, timeout=30.0)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        await _debug_send(message, f"⚠️ Product page fetch failed: {exc}")
        return

    soup = BeautifulSoup(html, "html.parser")
    sku = apple._extract_sku(soup, html)
    if not sku:
        await _debug_send(message, "⚠️ Could not extract a SKU/part number from this page.")
        return
    await _debug_send(message, f"✅ Extracted SKU: {sku!r}")

    target = apple._build_fulfillment_target(sku, pincode)
    await _debug_send(
        message,
        f"🔍 Calling fulfillment-messages with your MANUALLY-PASTED cookie string "
        f"(cookie_length={len(cookie_string)}) — every other header (User-Agent, "
        f"Referer, sec-ch-ua*, etc.) is identical to a normal /debugpickup call, "
        f"only the cookies differ:\n{target}",
    )

    data, err = await apple._cookie_auth_fetch(
        target, log_tag="debug-raw", context=f"RAW OVERRIDE fulfillment-messages pincode={pincode!r}",
        timeout=apple._FULFILLMENT_TIMEOUT, referer=url, cookie_override=cookie_string,
    )

    if data is None:
        await _debug_send(
            message,
            f"⚠️ Still failed with your manually-pasted cookies: {err}\n\n"
            f"This points AWAY from the cookies as the sole cause — a cookie "
            f"string you confirmed works in a real browser also failed here.",
        )
        return

    await _debug_send(
        message,
        "✅ Succeeded with your manually-pasted cookies — this confirms the "
        "Playwright-harvested DB session's cookies specifically are the "
        "problem, not headers/params/replay logic.",
    )
    raw_json = json.dumps(data, indent=2)
    await _debug_send(message, f"Raw JSON response ({len(raw_json)} chars):")
    _CHUNK_SIZE = 4000
    for i in range(0, len(raw_json), _CHUNK_SIZE):
        await _debug_send(message, raw_json[i:i + _CHUNK_SIZE])


@router.message(Command("debugpickupflow"))
async def cmd_debugpickupflow(message: Message, command: CommandObject):
    if message.from_user.id != _DEBUG_PICKUP_FLOW_ADMIN_ID:
        return

    from config import PLAYWRIGHT_SCRAPER_URL

    if not command.args:
        await message.answer("Usage: <code>/debugpickupflow &lt;apple_url&gt; &lt;pincode&gt;</code>", parse_mode="HTML")
        return

    parts = command.args.strip().split()
    if len(parts) < 2:
        await message.answer("Usage: <code>/debugpickupflow &lt;apple_url&gt; &lt;pincode&gt;</code>", parse_mode="HTML")
        return
    url, pincode = parts[0], parts[1]

    if not PLAYWRIGHT_SCRAPER_URL:
        await _debug_send(message, "⚠️ PLAYWRIGHT_SCRAPER_URL is not set on this service — nothing to call.")
        return

    await _debug_send(
        message,
        f"🔍 Running the full pickup-check flow on {url} for pincode {pincode}: "
        f"click \"Check availability\" → fill zipCode → click Continue → "
        f"wait for results (playwright_scraper: {PLAYWRIGHT_SCRAPER_URL})…",
    )

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{PLAYWRIGHT_SCRAPER_URL}/debug-pickup-flow", json={"url": url, "pincode": pincode},
            )
    except Exception as exc:
        await _debug_send(message, f"⚠️ Request to playwright_scraper failed: {exc}")
        return

    if resp.status_code != 200:
        await _debug_send(message, f"⚠️ playwright_scraper returned HTTP {resp.status_code}: {resp.text[:500]!r}")
        return

    try:
        data = resp.json()
    except Exception as exc:
        await _debug_send(message, f"⚠️ Non-JSON response from playwright_scraper: {exc}")
        return

    await _debug_send(
        message,
        f"Page title: {data.get('page_title')!r}\n"
        f"diagnostics: {data.get('diagnostics')}",
    )

    _CHUNK_SIZE = 3500
    overlay_html = data.get("overlay_html")
    if overlay_html:
        text = f"overlay_html ({len(overlay_html)} chars):\n{overlay_html}"
        for i in range(0, len(text), _CHUNK_SIZE):
            await _debug_send(message, text[i:i + _CHUNK_SIZE])
    else:
        await _debug_send(
            message,
            "⚠️ overlay_html is empty — the overlay never appeared or the flow "
            "failed before reaching it. Check diagnostics above for which step failed.",
        )

    for label, key in (
        ("pickup_details_clickables", "pickup_details_clickables"),
        ("pincode_like_inputs", "pincode_like_inputs"),
    ):
        items = data.get(key) or []
        if not items:
            await _debug_send(message, f"{label}: 0 found")
            continue
        text = f"{label} ({len(items)}):\n{json.dumps(items, indent=2)}"
        for i in range(0, len(text), _CHUNK_SIZE):
            await _debug_send(message, text[i:i + _CHUNK_SIZE])


@router.message(Command("debugpickupavailability"))
async def cmd_debugpickupavailability(message: Message, command: CommandObject):
    if message.from_user.id != _DEBUG_PICKUP_AVAILABILITY_ADMIN_ID:
        return

    if not command.args:
        await message.answer(
            "Usage: <code>/debugpickupavailability &lt;apple_url&gt; &lt;pincode&gt; [sku]</code>",
            parse_mode="HTML",
        )
        return

    parts = command.args.strip().split()
    if len(parts) < 2:
        await message.answer(
            "Usage: <code>/debugpickupavailability &lt;apple_url&gt; &lt;pincode&gt; [sku]</code>",
            parse_mode="HTML",
        )
        return
    url, pincode = parts[0], parts[1]
    sku = parts[2] if len(parts) > 2 else None

    await _debug_send(
        message,
        f"🔍 Running the PRODUCTION pickup-availability checker (same cached "
        f"function check_pickup_row uses) on {url} for pincode {pincode}"
        + (f" (sku={sku!r} — will try direct_http first, Playwright fallback)" if sku else " (no sku given — Playwright only, same as before the direct_http fallback existed)")
        + "…",
    )

    available, matching_stores, error, meta = await apple._fetch_pickup_availability_via_page_render(url, pincode, sku)

    _CHUNK_SIZE = 3500

    await _debug_send(
        message,
        f"method_used: {meta['method_used']!r} — which path actually produced this "
        f"result: 'direct_http' (the new /shop/retail/pickup-message endpoint) or "
        f"'playwright' (the Xvfb/browser fallback)\n"
        f"direct_http_attempted: {meta['direct_http_attempted']!r}"
        + (f" — failed with: {meta['direct_http_error']}" if meta['direct_http_error'] else "")
        + f"\nserved_from_cache: {meta['served_from_cache']!r} — True means this exact "
        f"(url, pincode) was already checked within the last "
        f"{apple._PAGE_RENDER_CACHE_TTL_SECONDS}s and this call reused that result "
        f"WITHOUT launching a new check; call this command twice in a row "
        f"for the same url/pincode to see it flip from False to True\n"
        f"git_commit_sha: {meta['git_commit_sha']!r} — compare this against "
        f"the commit you expect to be deployed, so a result that looks like an "
        f"old bug can't be mistaken for a fix that didn't work (or vice versa)\n"
        f"available: {available!r}"
        + (f"\nerror: {error}" if error else ""),
    )
    diagnostics_text = f"diagnostics:\n{json.dumps(meta['diagnostics'], indent=2)}"
    for i in range(0, len(diagnostics_text), _CHUNK_SIZE):
        await _debug_send(message, diagnostics_text[i:i + _CHUNK_SIZE])

    if matching_stores:
        text = f"matching_stores ({len(matching_stores)}):\n{json.dumps(matching_stores, indent=2)}"
        for i in range(0, len(text), _CHUNK_SIZE):
            await _debug_send(message, text[i:i + _CHUNK_SIZE])
    else:
        await _debug_send(
            message,
            "matching_stores: none — either genuinely not available at any "
            "nearby store, no stores near this pincode at all, or the "
            "check failed before reaching that point. Check diagnostics/error "
            "above (used_endpoint/parse_error/response_wait_timed_out) for which.",
        )


@router.message(Command("debugpickupmessage"))
async def cmd_debugpickupmessage(message: Message, command: CommandObject):
    if message.from_user.id != _DEBUG_PICKUP_MESSAGE_ADMIN_ID:
        return

    if not command.args:
        await message.answer(
            "Usage: <code>/debugpickupmessage &lt;sku&gt; &lt;location&gt; [country=in]</code>",
            parse_mode="HTML",
        )
        return

    parts = command.args.strip().split()
    if len(parts) < 2:
        await message.answer(
            "Usage: <code>/debugpickupmessage &lt;sku&gt; &lt;location&gt; [country=in]</code>",
            parse_mode="HTML",
        )
        return
    sku, location = parts[0], parts[1]
    country = parts[2] if len(parts) > 2 else "in"

    url = f"https://www.apple.com/{country}/shop/retail/pickup-message"
    params = {"parts.0": sku, "location": location}

    await _debug_send(
        message,
        f"🔍 EXPERIMENTAL — testing /shop/retail/pickup-message directly: "
        f"ZERO cookies, ZERO session, ZERO custom headers (not even a "
        f"User-Agent override), matching what two independent open-source "
        f"implementations reportedly do.\n"
        f"url: {url}\nparams: {params}",
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
    except Exception as exc:
        await _debug_send(message, f"⚠️ Request failed: {type(exc).__name__}: {exc}")
        return

    _CHUNK_SIZE = 3500
    await _debug_send(message, f"status_code: {resp.status_code}\nfinal_url: {resp.url}")

    body_text = resp.text
    if body_text:
        text = f"body ({len(body_text)} chars):\n{body_text}"
        for i in range(0, len(text), _CHUNK_SIZE):
            await _debug_send(message, text[i:i + _CHUNK_SIZE])
    else:
        await _debug_send(message, "body: (empty)")


@router.message(Command("debugpickupmessagestress"))
async def cmd_debugpickupmessagestress(message: Message, command: CommandObject):
    """Repeatedly calls the same /shop/retail/pickup-message endpoint
    /debugpickupmessage tests, N times in a row, to see whether it stays
    consistently unauthenticated/200 or eventually gets rate-limited/blocked."""
    if message.from_user.id != _DEBUG_PICKUP_MESSAGE_ADMIN_ID:
        return

    if not command.args:
        await message.answer(
            "Usage: <code>/debugpickupmessagestress &lt;sku&gt; &lt;location&gt; "
            "[country=in] [attempts=8] [delay_seconds=3]</code>",
            parse_mode="HTML",
        )
        return

    parts = command.args.strip().split()
    if len(parts) < 2:
        await message.answer(
            "Usage: <code>/debugpickupmessagestress &lt;sku&gt; &lt;location&gt; "
            "[country=in] [attempts=8] [delay_seconds=3]</code>",
            parse_mode="HTML",
        )
        return
    sku, location = parts[0], parts[1]
    country = parts[2] if len(parts) > 2 else "in"

    try:
        attempts = int(parts[3]) if len(parts) > 3 else 8
    except ValueError:
        await message.answer("⚠️ attempts must be an integer.")
        return
    if attempts < 1 or attempts > _DEBUG_PICKUP_MESSAGE_STRESS_MAX_ATTEMPTS:
        await message.answer(
            f"⚠️ attempts must be between 1 and {_DEBUG_PICKUP_MESSAGE_STRESS_MAX_ATTEMPTS}.",
        )
        return

    try:
        delay_seconds = float(parts[4]) if len(parts) > 4 else 3.0
    except ValueError:
        await message.answer("⚠️ delay_seconds must be a number.")
        return
    if delay_seconds < 0:
        await message.answer("⚠️ delay_seconds must be >= 0.")
        return

    url = f"https://www.apple.com/{country}/shop/retail/pickup-message"
    params = {"parts.0": sku, "location": location}

    await _debug_send(
        message,
        f"🔍 EXPERIMENTAL STRESS TEST — calling /shop/retail/pickup-message "
        f"{attempts} times in a row (delay {delay_seconds}s between calls), "
        f"ZERO cookies/session/custom headers each time.\n"
        f"url: {url}\nparams: {params}",
    )

    results = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(attempts):
            attempt_start = time.monotonic()
            try:
                resp = await client.get(url, params=params)
                elapsed = time.monotonic() - attempt_start
                results.append({
                    "attempt": i + 1,
                    "status_code": resp.status_code,
                    "elapsed_seconds": round(elapsed, 2),
                    "body_len": len(resp.text),
                    "error": None,
                })
            except Exception as exc:
                elapsed = time.monotonic() - attempt_start
                results.append({
                    "attempt": i + 1,
                    "status_code": None,
                    "elapsed_seconds": round(elapsed, 2),
                    "body_len": None,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            if i < attempts - 1 and delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

    lines = ["— per-attempt results —"]
    for r in results:
        if r["error"] is not None:
            lines.append(f"#{r['attempt']}: ERROR after {r['elapsed_seconds']}s — {r['error']}")
        else:
            lines.append(
                f"#{r['attempt']}: status={r['status_code']} "
                f"body_len={r['body_len']} elapsed={r['elapsed_seconds']}s",
            )

    status_counts = Counter(r["status_code"] for r in results if r["error"] is None)
    error_count = sum(1 for r in results if r["error"] is not None)
    status_summary = ", ".join(f"{code}: {count}" for code, count in status_counts.items()) or "(none)"

    lines.append("")
    lines.append("— summary —")
    lines.append(f"attempts: {attempts}")
    lines.append(f"status_code distribution: {status_summary}")
    lines.append(f"errors/exceptions: {error_count}")
    successful_elapsed = [r["elapsed_seconds"] for r in results if r["error"] is None]
    if successful_elapsed:
        lines.append(
            f"elapsed: min={min(successful_elapsed)}s max={max(successful_elapsed)}s "
            f"avg={round(sum(successful_elapsed) / len(successful_elapsed), 2)}s",
        )
    consistent = len(status_counts) <= 1 and error_count == 0
    lines.append(f"consistent behavior throughout: {consistent}")

    combined = "\n".join(lines)
    _CHUNK_SIZE = 3500
    for i in range(0, len(combined), _CHUNK_SIZE):
        await _debug_send(message, combined[i:i + _CHUNK_SIZE])


@router.message(Command("debugpickupstatus"))
async def cmd_debugpickupstatus(message: Message, command: CommandObject):
    if message.from_user.id != _DEBUG_PICKUP_STATUS_ADMIN_ID:
        return

    if not command.args or not command.args.strip():
        await message.answer(
            "Usage: <code>/debugpickupstatus &lt;sku_or_url_substring&gt;</code>\n"
            "Case-insensitive substring match against sku OR url, across both "
            "personal /trackpickup rows and channel-forward pickup rows.",
            parse_mode="HTML",
        )
        return

    needle = command.args.strip().lower()

    forwarding_paused = get_forwarding_pause_info()["paused"]
    apple_globally_locked = is_site_locked("apple")

    lines = [
        f"🔍 Searching pickup_tracking + channel_forward_pickup_tracking for {needle!r} "
        f"(sku or url substring)…\n"
        f"[global] apple site-locked (all users): {apple_globally_locked!r}\n"
        f"[global] channel forwarding paused: {forwarding_paused!r}",
    ]

    personal_rows = [
        row for row in get_all_pickup_tracking()
        if needle in (row.get("sku") or "").lower() or needle in (row.get("url") or "").lower()
    ]
    channel_rows = [
        row for row in list_channel_forward_pickup()
        if needle in (row.get("sku") or "").lower() or needle in (row.get("url") or "").lower()
    ]

    if not personal_rows and not channel_rows:
        lines.append("No matching rows found in either table.")
        await _debug_send(message, "\n\n".join(lines))
        return

    for row in personal_rows:
        user_locked = is_site_locked("apple", row["user_id"])
        lines.append(
            f"[personal] id={row['id']} user_id={row['user_id']} name={row['name']!r}\n"
            f"  url: {row['url']}\n"
            f"  sku: {row['sku']!r}\n"
            f"  pincodes: {row['pincodes']}\n"
            f"  pincode_status: {json.dumps(row['pincode_status'])}\n"
            f"  created_at: {row.get('created_at')!r}\n"
            f"  apple site-locked for this user: {user_locked!r} (global: {apple_globally_locked!r})"
        )

    for row in channel_rows:
        lines.append(
            f"[channel] id={row['id']} name={row['name']!r}\n"
            f"  url: {row['url']}\n"
            f"  sku: {row.get('sku')!r}\n"
            f"  pincodes: {row['pincodes']}\n"
            f"  pincode_status: {json.dumps(row['pincode_status'])}\n"
            f"  last_checked: {row.get('last_checked')!r}\n"
            f"  created_at: {row.get('created_at')!r}\n"
            f"  forwarding paused: {forwarding_paused!r}"
        )

    combined = "\n\n".join(lines)
    _CHUNK_SIZE = 3500
    for i in range(0, len(combined), _CHUNK_SIZE):
        await _debug_send(message, combined[i:i + _CHUNK_SIZE])


@router.message(Command("debugpickupevents"))
async def cmd_debugpickupevents(message: Message, command: CommandObject):
    if message.from_user.id != _DEBUG_PICKUP_EVENTS_ADMIN_ID:
        return

    args = (command.args or "").strip().split()
    substring = None
    limit = 30
    for arg in args:
        if arg.isdigit():
            limit = min(int(arg), 200)
        else:
            substring = arg

    events = get_recent_pickup_alert_events(limit=limit, substring=substring)

    if not events:
        await _debug_send(
            message,
            f"No pickup_alert_log events found"
            + (f" matching {substring!r}" if substring else "")
            + ".",
        )
        return

    lines = [
        f"🔍 {len(events)} most recent pickup_alert_log event(s)"
        + (f" matching {substring!r}" if substring else "")
        + " (newest first):",
    ]
    for ev in events:
        lines.append(
            f"[{ev['ts']}] source={ev['source']} row_id={ev['row_id']} "
            f"pincode={ev['pincode']!r} event={ev['event']!r}"
            + (f"\n  detail: {ev['detail']}" if ev.get("detail") else "")
        )

    combined = "\n".join(lines)
    _CHUNK_SIZE = 3500
    for i in range(0, len(combined), _CHUNK_SIZE):
        await _debug_send(message, combined[i:i + _CHUNK_SIZE])


@router.message(Command("debugzipcodevalidation"))
async def cmd_debugzipcodevalidation(message: Message, command: CommandObject):
    if message.from_user.id != _DEBUG_ZIPCODE_VALIDATION_ADMIN_ID:
        return

    from config import PLAYWRIGHT_SCRAPER_URL

    if not command.args:
        await message.answer("Usage: <code>/debugzipcodevalidation &lt;apple_url&gt; &lt;pincode&gt;</code>", parse_mode="HTML")
        return

    parts = command.args.strip().split()
    if len(parts) < 2:
        await message.answer("Usage: <code>/debugzipcodevalidation &lt;apple_url&gt; &lt;pincode&gt;</code>", parse_mode="HTML")
        return
    url, pincode = parts[0], parts[1]

    if not PLAYWRIGHT_SCRAPER_URL:
        await _debug_send(message, "⚠️ PLAYWRIGHT_SCRAPER_URL is not set on this service — nothing to call.")
        return

    await _debug_send(
        message,
        f"🔍 Testing checkpoints A (typed) → B (+500ms dwell) → C (programmatic "
        f"blur) → D (click-elsewhere blur) on {url} for pincode {pincode} — "
        f"stops at the first checkpoint where Continue actually becomes enabled "
        f"(playwright_scraper: {PLAYWRIGHT_SCRAPER_URL})…",
    )

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{PLAYWRIGHT_SCRAPER_URL}/debug-zipcode-validation", json={"url": url, "pincode": pincode},
            )
    except Exception as exc:
        await _debug_send(message, f"⚠️ Request to playwright_scraper failed: {exc}")
        return

    if resp.status_code != 200:
        await _debug_send(message, f"⚠️ playwright_scraper returned HTTP {resp.status_code}: {resp.text[:500]!r}")
        return

    try:
        data = resp.json()
    except Exception as exc:
        await _debug_send(message, f"⚠️ Non-JSON response from playwright_scraper: {exc}")
        return

    enabled_at = data.get("enabled_at")
    await _debug_send(
        message,
        f"git_commit_sha: {data.get('git_commit_sha')!r}\n"
        f"enabled_at: {enabled_at!r}"
        + ("  ✅ THIS is what actually enables Continue" if enabled_at else "  ⚠️ never became enabled at any checkpoint tried")
        + f"\ndiagnostics: {data.get('diagnostics')}",
    )

    _CHUNK_SIZE = 3500
    typing_trajectory = data.get("typing_trajectory") or []
    if typing_trajectory:
        text = f"typing_trajectory ({len(typing_trajectory)} keystrokes):\n{json.dumps(typing_trajectory, indent=2)}"
        for i in range(0, len(text), _CHUNK_SIZE):
            await _debug_send(message, text[i:i + _CHUNK_SIZE])
    else:
        await _debug_send(
            message,
            "⚠️ typing_trajectory is empty — the flow failed before typing "
            "even started. Check diagnostics above for which step failed.",
        )

    checkpoints = data.get("checkpoints") or {}
    if checkpoints:
        text = f"checkpoints ({len(checkpoints)}):\n{json.dumps(checkpoints, indent=2)}"
        for i in range(0, len(text), _CHUNK_SIZE):
            await _debug_send(message, text[i:i + _CHUNK_SIZE])
    else:
        await _debug_send(
            message,
            "⚠️ no checkpoints were reached — the flow failed before typing "
            "even started. Check diagnostics above for which step failed.",
        )

    network_requests = data.get("network_requests") or []
    if network_requests:
        text = f"network_requests ({len(network_requests)} XHR/fetch since typing started):\n{json.dumps(network_requests, indent=2)}"
        for i in range(0, len(text), _CHUNK_SIZE):
            await _debug_send(message, text[i:i + _CHUNK_SIZE])
    else:
        await _debug_send(
            message,
            "network_requests: none seen — no XHR/fetch call was fired after "
            "typing, which argues against an async backend validation call "
            "being what Continue is waiting on.",
        )

    network_response_bodies = data.get("network_response_bodies") or []
    if network_response_bodies:
        text = (
            f"network_response_bodies ({len(network_response_bodies)} keyword-matched "
            f"responses, full body):\n{json.dumps(network_response_bodies, indent=2)}"
        )
        for i in range(0, len(text), _CHUNK_SIZE):
            await _debug_send(message, text[i:i + _CHUNK_SIZE])
    else:
        await _debug_send(
            message,
            "network_response_bodies: none matched _NETWORK_CAPTURE_KEYWORDS — "
            "either nothing availability-related fired, or it fired under a "
            "URL that doesn't contain any of those keywords.",
        )
