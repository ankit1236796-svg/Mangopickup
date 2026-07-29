"""
worker.py
~~~~~~~~~
Apple pickup-checking bot — extracted from Tracker-alert's bot.py, which
also runs regular stock-checking, access/trial maintenance, WhatsApp
forwarding, etc. Loops here: apple_pickup_check_loop (now ONLY
run_apple_official_pickup_cycle, on its own cadence — a separate,
cookie-based Apple endpoint, not playwright_scraper), apple_pickup_
stagger_loop (personal /trackpickup + channel-forward pickup rows, one
PRODUCT at a time with its pincodes checked concurrently — see that
loop's own module note for why this replaced the old bulk-concurrent
run_pickup_check_cycle / run_channel_forward_pickup_check_cycle), and
apple_cookie_refresh_loop.

Also registers apple_admin_handlers.router so the /debugpickup* diagnostic
commands work — this is a fully independent bot/token/DB, so there's no
polling conflict with any other bot.
"""

import asyncio
import logging
import random

import httpx
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault
from bs4 import BeautifulSoup

from apple_admin_handlers import router as admin_router
from pickup_handlers import router as pickup_router
from config import (
    BOT_TOKEN, APPLE_PICKUP_PINCODES, APPLE_OFFICIAL_PICKUP_ALERTS_ENABLED,
    APPLE_PICKUP_CHECK_INTERVAL, APPLE_PICKUP_STAGGER_INTERVAL_SECONDS,
    PLAYWRIGHT_SCRAPER_URL, PLAYWRIGHT_SCRAPER_INTERNAL_TOKEN,
    APPLE_COOKIE_REFRESH_INTERVAL, APPLE_COOKIE_REFRESH_PRODUCT_URL, APPLE_COOKIE_REFRESH_PINCODE,
    APPLE_COOKIE_REFRESH_MAX_ATTEMPTS, APPLE_COOKIE_REFRESH_RETRY_DELAY_MIN_SECONDS,
    APPLE_COOKIE_REFRESH_RETRY_DELAY_MAX_SECONDS,
)
from database import (
    init_db,
    get_all_products,
    is_service_paused,
    list_paused_user_ids,
    get_all_pickup_tracking,
    get_apple_official_pickup_status,
    upsert_apple_official_pickup_status,
    set_apple_session_cookies,
    list_channel_forward_pickup,
    log_pickup_alert_event,
)
from notifications import send_pickup_alert
from checkers import apple as apple_checker, fetch_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Apple official-store pickup auto-check (database.apple_official_pickup_
# status) — checks the 6 fixed official-store pincodes for every apple.com
# product in database.products. NOTE: nothing in this extracted repo adds
# rows to that table (Tracker-alert's /add command does, and wasn't part of
# this extraction) — this cycle will simply find zero products and no-op
# until something populates database.products with site="apple" rows.
# ---------------------------------------------------------------------------

async def _check_apple_official_pickup_group(bot: Bot, url: str, rows: list[dict]) -> None:
    cached = get_apple_official_pickup_status(url)
    sku = cached["sku"] if cached else None

    if not sku:
        try:
            resp = await fetch_page(url, render_js=apple_checker.NEEDS_JS, timeout=30.0, site="apple")
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            sku = apple_checker._extract_sku(soup, resp.text)
        except Exception as exc:
            logger.error(f"[apple][official-stores] product page fetch/SKU extraction failed for {url!r}: {exc}")
            return
        if not sku:
            logger.warning(f"[apple][official-stores] could not extract a SKU from {url!r} — skipping this cycle")
            return

    try:
        results = await apple_checker.check_pickup_at_official_stores(sku, APPLE_PICKUP_PINCODES, product_url=url)
    except Exception as exc:
        logger.error(f"[apple][official-stores] check failed for {url!r} sku={sku!r}: {exc}")
        return

    prior_status: dict = cached["pincode_status"] if cached else {}
    new_status = dict(prior_status)
    representative_name = rows[0]["name"]

    for pincode in APPLE_PICKUP_PINCODES:
        if pincode not in results:
            continue
        stores = results[pincode]
        now_available = bool(stores)
        was_available = bool(prior_status.get(pincode, False))
        new_status[pincode] = now_available

        if now_available and not was_available:
            logger.info(
                f"[apple][official-stores] {url!r} pincode={pincode!r} "
                f"({', '.join(s['store_name'] for s in stores)}) transitioned to available"
            )
            log_pickup_alert_event(
                "official_stores", None, pincode, "transition_true",
                f"sku={sku!r} url={url!r} stores={[s.get('store_name') for s in stores]}",
            )
            if APPLE_OFFICIAL_PICKUP_ALERTS_ENABLED:
                for row in rows:
                    try:
                        send_status = await send_pickup_alert(bot, row["user_id"], representative_name, pincode, stores)
                    except Exception as exc:
                        logger.error(
                            f"[apple][official-stores] alert failed for user {row['user_id']} "
                            f"url={url!r} pincode={pincode!r}: {exc}"
                        )
                        log_pickup_alert_event("official_stores", None, pincode, "alert_send_exception", str(exc))
                    else:
                        event = "alert_sent" if send_status == "sent" else (
                            "alert_suppressed_locked" if send_status == "suppressed_locked" else "alert_send_error"
                        )
                        log_pickup_alert_event(
                            "official_stores", None, pincode, event,
                            f"user_id={row['user_id']} status={send_status}",
                        )
            else:
                logger.info(
                    "[apple][official-stores] alert suppressed — "
                    "config.APPLE_OFFICIAL_PICKUP_ALERTS_ENABLED is False"
                )
                log_pickup_alert_event(
                    "official_stores", None, pincode, "alert_suppressed_config_disabled",
                    f"url={url!r}",
                )

    try:
        upsert_apple_official_pickup_status(url, sku, new_status)
    except Exception as exc:
        logger.error(f"[apple][official-stores] error persisting pincode_status for {url!r}: {exc}")
        log_pickup_alert_event("official_stores", None, None, "status_persist_error", f"url={url!r} {exc}")


async def run_apple_official_pickup_cycle(bot: Bot) -> dict:
    """
    One check pass across every apple.com product's fixed 6 official-store
    pincodes (all users, cross-user-deduplicated by exact URL).
    """
    if is_service_paused():
        logger.info("[apple][official-stores] service globally paused — skipping this check cycle entirely")
        return {"products": 0, "groups": 0, "paused": True}

    products = [p for p in get_all_products() if p["site"] == "apple"]
    paused_user_ids = set(list_paused_user_ids())
    if paused_user_ids:
        products = [p for p in products if p["user_id"] not in paused_user_ids]

    if not products:
        return {"products": 0, "groups": 0}

    groups: dict[str, list[dict]] = {}
    for product in products:
        groups.setdefault(product["url"], []).append(product)

    sem = asyncio.Semaphore(10)

    async def _bounded(url, rows):
        async with sem:
            await _check_apple_official_pickup_group(bot, url, rows)

    await asyncio.gather(*[_bounded(url, rows) for url, rows in groups.items()])
    return {"products": len(products), "groups": len(groups)}


# ---------------------------------------------------------------------------
# Official-store loop — UNCHANGED cadence, only run_apple_official_pickup_
# cycle now (personal/channel-forward pickup checking moved to
# apple_pickup_stagger_loop below). Kept separate because this cycle hits
# a completely different Apple endpoint (cookie-based fulfillment-
# messages via check_pickup_at_official_stores) — no Playwright/
# playwright_scraper involved, so it was never part of the resource-
# contention problem the stagger loop below solves, and has no reason to
# share that loop's per-combo pacing.
# ---------------------------------------------------------------------------

async def apple_pickup_check_loop(bot: Bot):
    logger.info(
        f"[apple][pickup] official-store check loop started (interval={APPLE_PICKUP_CHECK_INTERVAL}s)"
    )
    while True:
        try:
            await run_apple_official_pickup_cycle(bot)
        except Exception as exc:
            logger.error(f"[apple][pickup] Apple official-store pickup cycle error: {exc}")

        await asyncio.sleep(APPLE_PICKUP_CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# Staggered BY-PRODUCT pickup-checking — personal /trackpickup rows AND
# channel-forward pickup rows, combined into one round-robin list of
# product ROWS. Each tick checks ONE product: all of that product's
# pincodes together, concurrently (capped at config.APPLE_PICKUP_
# PINCODE_CONCURRENCY inside check_pickup_row / check_channel_pickup_row
# — see checkers.apple._gather_row_pincode_checks), then sleeps
# APPLE_PICKUP_STAGGER_INTERVAL_SECONDS before the next product, cycling
# forever. So at any moment at most ONE product — i.e. at most
# APPLE_PICKUP_PINCODE_CONCURRENCY simultaneous pincode-checks — is ever
# running, with real gaps between different products' checks. Each
# product's effective refresh interval ≈ interval × (total product rows,
# personal + channel combined): e.g. 5 products at the 60s default =
# each product refreshes ~every 5 minutes.
#
# History: originally bulk-concurrent (run_pickup_check_cycle /
# run_channel_forward_pickup_check_cycle, up to 10 rows at once via
# asyncio.Semaphore(10)) — produced ~40-60% "check failed" results under
# multi-item load, traced to playwright_scraper's own MAX_CONCURRENT_
# CHECKS=2 browser-slot ceiling (see that module's own history: raised
# 2->4, hit genuine concurrent-load browser-launch failures, reverted).
# Briefly a strict one-(product,pincode)-combo-per-tick schedule
# (2026-07-29, same day) before landing on this by-product grouping: a
# product's own pincodes are checked together so its status is a
# coherent snapshot, and 2 concurrent checks were never the source of
# overload — 10 were. checkers.apple's _playwright_fallback_slots
# semaphore (capacity-matched to playwright_scraper, shared by
# /mypickups and /checkforwarding too) remains the global backstop.
#
# Rows are rebuilt FRESH from the DB on every single tick (not once per
# lap) so: (a) `row` is always current data at the moment it's checked,
# never stale from earlier in a long lap, and (b) a row added/removed
# between two ticks is picked up (or drops out) on the very next tick
# rather than waiting for a full lap to notice.
# ---------------------------------------------------------------------------

async def _build_pickup_check_rows() -> list[tuple[str, dict]]:
    """Every current (source, row) product row across personal
    pickup_tracking and channel_forward_pickup_tracking — "personal"/
    "channel" tags which checker function + persist path a row needs.
    Empty (not just filtered) while the service is globally paused, same
    "skip entirely" semantics run_pickup_check_cycle used to have."""
    if is_service_paused():
        return []

    rows: list[tuple[str, dict]] = []
    paused_user_ids = set(list_paused_user_ids())
    for row in get_all_pickup_tracking():
        if row["user_id"] in paused_user_ids:
            continue
        if row["pincodes"]:
            rows.append(("personal", row))
    for row in list_channel_forward_pickup():
        if row.get("pincodes"):
            rows.append(("channel", row))
    return rows


async def apple_pickup_stagger_loop(bot: Bot):
    logger.info(
        f"[apple][pickup][stagger] staggered check loop started "
        f"(interval={APPLE_PICKUP_STAGGER_INTERVAL_SECONDS}s per product)"
    )
    index = 0
    while True:
        rows = await _build_pickup_check_rows()
        if not rows:
            await asyncio.sleep(APPLE_PICKUP_STAGGER_INTERVAL_SECONDS)
            continue

        index %= len(rows)
        source, row = rows[index]
        try:
            if source == "personal":
                await apple_checker.check_pickup_row(bot, row)
            else:
                await apple_checker.check_channel_pickup_row(bot, row)
        except Exception as exc:
            logger.error(
                f"[apple][pickup][stagger] error checking {source} row #{row.get('id')}: {exc}"
            )

        index += 1
        await asyncio.sleep(APPLE_PICKUP_STAGGER_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Apple cookie auto-refresher — POSTs to playwright_scraper's
# /refresh-apple-cookies periodically, storing the result via
# database.set_apple_session_cookies.
# ---------------------------------------------------------------------------

async def _request_apple_cookie_refresh() -> dict | None:
    headers = {}
    if PLAYWRIGHT_SCRAPER_INTERNAL_TOKEN:
        headers["X-Internal-Token"] = PLAYWRIGHT_SCRAPER_INTERNAL_TOKEN

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{PLAYWRIGHT_SCRAPER_URL}/refresh-apple-cookies",
                json={
                    "url": APPLE_COOKIE_REFRESH_PRODUCT_URL,
                    "pincode": APPLE_COOKIE_REFRESH_PINCODE,
                },
                headers=headers,
            )
    except Exception as exc:
        logger.warning(f"[apple][cookie-refresh] request to playwright_scraper failed: {exc}")
        return None

    if resp.status_code != 200:
        logger.warning(
            f"[apple][cookie-refresh] playwright_scraper returned HTTP "
            f"{resp.status_code}: {resp.text[:300]!r}"
        )
        return None

    try:
        data = resp.json()
    except Exception as exc:
        logger.warning(f"[apple][cookie-refresh] non-JSON response from playwright_scraper: {exc}")
        return None

    cookies = (data.get("cookies") or "").strip()
    user_agent = (data.get("user_agent") or "").strip()
    if not cookies or not user_agent:
        logger.warning(
            f"[apple][cookie-refresh] playwright_scraper response missing "
            f"cookies/user_agent: {data}"
        )
        return None

    return data


async def run_apple_cookie_refresh_cycle() -> bool:
    """
    One refresh CYCLE — up to APPLE_COOKIE_REFRESH_MAX_ATTEMPTS calls to
    _request_apple_cookie_refresh, stopping early the moment one comes back
    with pincode_check_confirmed=True. Returns True if any attempt produced
    a usable session that got stored, False only if every attempt failed
    outright.
    """
    if not PLAYWRIGHT_SCRAPER_URL:
        return False

    best_result: dict | None = None
    for attempt in range(1, APPLE_COOKIE_REFRESH_MAX_ATTEMPTS + 1):
        data = await _request_apple_cookie_refresh()
        if data is None:
            logger.warning(
                f"[apple][cookie-refresh] attempt {attempt}/{APPLE_COOKIE_REFRESH_MAX_ATTEMPTS} "
                f"produced no usable session"
            )
        else:
            best_result = data
            if data.get("pincode_check_confirmed"):
                logger.info(
                    f"[apple][cookie-refresh] attempt {attempt}/{APPLE_COOKIE_REFRESH_MAX_ATTEMPTS} "
                    f"confirmed (pincode_check_confirmed=True) — using this session, no further attempts needed."
                )
                break
            logger.info(
                f"[apple][cookie-refresh] attempt {attempt}/{APPLE_COOKIE_REFRESH_MAX_ATTEMPTS} "
                f"NOT confirmed (pincode_check_confirmed=False)"
            )

        if attempt < APPLE_COOKIE_REFRESH_MAX_ATTEMPTS:
            delay = random.uniform(
                APPLE_COOKIE_REFRESH_RETRY_DELAY_MIN_SECONDS, APPLE_COOKIE_REFRESH_RETRY_DELAY_MAX_SECONDS
            )
            logger.info(f"[apple][cookie-refresh] retrying in {delay:.1f}s...")
            await asyncio.sleep(delay)

    if best_result is None:
        logger.warning(
            f"[apple][cookie-refresh] all {APPLE_COOKIE_REFRESH_MAX_ATTEMPTS} attempts failed outright — "
            f"DB session left unchanged."
        )
        return False

    set_apple_session_cookies(best_result["cookies"].strip(), best_result["user_agent"].strip())
    logger.info(
        f"[apple][cookie-refresh] stored a freshly-refreshed Apple session "
        f"(pincode_check_confirmed={best_result.get('pincode_check_confirmed')}, "
        f"diagnostics={best_result.get('diagnostics')})"
    )
    return True


async def apple_cookie_refresh_loop():
    if not PLAYWRIGHT_SCRAPER_URL:
        logger.info(
            "[apple][cookie-refresh] PLAYWRIGHT_SCRAPER_URL not set — "
            "auto-refresher disabled, using APPLE_COOKIES/APPLE_USER_AGENT "
            "env vars only."
        )
        return

    logger.info(
        f"[apple][cookie-refresh] loop started (interval="
        f"{APPLE_COOKIE_REFRESH_INTERVAL}s, target={PLAYWRIGHT_SCRAPER_URL})"
    )
    while True:
        try:
            await run_apple_cookie_refresh_cycle()
        except Exception as exc:
            logger.error(f"[apple][cookie-refresh] cycle error: {exc}")
        await asyncio.sleep(APPLE_COOKIE_REFRESH_INTERVAL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def register_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="add", description="Track an apple.com product for official-store pickup checks"),
        BotCommand(command="trackpickup", description="Track Apple Store pickup availability by pincode"),
        BotCommand(command="mypickups", description="Check your tracked pickup items right now"),
        BotCommand(command="untrackpickup", description="Stop tracking a pickup item"),
        BotCommand(command="debugpickup", description="[admin] Raw fulfillment-messages diagnostic"),
        BotCommand(command="debugpickupraw", description="[admin] Replay fulfillment-messages with pasted cookies"),
        BotCommand(command="debugpickupflow", description="[admin] Full pickup-check flow via playwright_scraper"),
        BotCommand(command="debugpickupavailability", description="[admin] Production pickup-availability checker"),
        BotCommand(command="debugpickupmessage", description="[admin] Test /shop/retail/pickup-message directly"),
        BotCommand(command="debugpickupmessagestress", description="[admin] Stress-test /shop/retail/pickup-message"),
        BotCommand(command="debugpickupstatus", description="[admin] Dump persisted pickup_tracking rows"),
        BotCommand(command="debugpickupevents", description="[admin] Dump pickup_alert_log events"),
        BotCommand(command="debugzipcodevalidation", description="[admin] Diagnose pincode-field validation"),
        BotCommand(command="setchannel", description="[admin] Register the channel for forwarded pickup alerts"),
        BotCommand(command="addchannelpickup", description="[admin] Forward Apple pickup alerts to the channel"),
        BotCommand(command="stopforwardingpickup", description="[admin] Stop forwarding a pickup item to the channel"),
        BotCommand(command="checkforwarding", description="[admin] Check every forwarded pickup item right now"),
        BotCommand(command="debugproxyip", description="[admin][temp] Verify Webshare proxy IP rotation"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    logger.info(f"Registered {len(commands)} bot commands with Telegram")


async def main():
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    # admin_router first: its handlers are filtered to ADMIN_USER_ID only, so
    # order relative to pickup_router doesn't affect regular users.
    dp.include_router(admin_router)
    dp.include_router(pickup_router)

    await register_commands(bot)

    apple_cookie_task = asyncio.create_task(apple_cookie_refresh_loop())
    apple_pickup_task = asyncio.create_task(apple_pickup_check_loop(bot))
    apple_pickup_stagger_task = asyncio.create_task(apple_pickup_stagger_loop(bot))

    logger.info("Mangopickup worker starting…")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        apple_cookie_task.cancel()
        apple_pickup_task.cancel()
        apple_pickup_stagger_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
