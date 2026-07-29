import asyncio
import json
import logging
import os
import random
import re
import time
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from .common import fetch_page

logger = logging.getLogger(__name__)

NEEDS_JS = False

# Apple uses "Add to Bag" (not "Add to Cart")
_ADD_PATTERNS = ["add to bag", "add to cart", "buy"]
_OOS_PATTERNS = [
    "out of stock", "sold out", "currently unavailable",
    "notify me when available", "coming soon",
]

# Class tokens that mark a button/anchor as DISABLED via CSS alone, with no
# `disabled`/`aria-disabled` HTML attribute present — the "Croma lesson" (see
# checkers/croma.py's history). Apple's storefront greys out "Add to Bag" via
# class styling in some states, so without this a disabled button could be
# read as active → false in-stock. JSON-LD is checked first and is usually
# authoritative on apple.com/in, so this button scan is only a fallback, but
# it should still respect a class-styled disabled state.
_DISABLED_CLASS_MARKERS = ("disable", "inactive")


def _is_disabled(el) -> bool:
    """Return True if a BS4 element is visually/semantically disabled — via
    the `disabled` attribute, `aria-disabled="true"`, or a _DISABLED_CLASS_MARKERS
    substring in its class list."""
    if el.get("disabled") is not None:
        return True
    if el.get("aria-disabled", "").lower() == "true":
        return True
    classes = " ".join(el.get("class", [])).lower()
    return any(marker in classes for marker in _DISABLED_CLASS_MARKERS)


def check(soup: BeautifulSoup, html: str) -> bool:
    html_lower = html.lower()

    # ── JSON-LD (most reliable on apple.com/in) ───────────────────────────────
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            for item in (data if isinstance(data, list) else [data]):
                if not isinstance(item, dict):
                    continue
                avail = item.get("offers", {}).get("availability", "")
                if "InStock" in avail:
                    logger.info("[apple] JSON-LD: InStock → True")
                    return True
                if "OutOfStock" in avail or "Discontinued" in avail:
                    logger.info("[apple] JSON-LD: OutOfStock/Discontinued → False")
                    return False
        except Exception:
            pass

    # ── Negative signals (checked BEFORE buttons — a disabled Add-to-Bag
    # button's surrounding page usually carries an unambiguous OOS text signal
    # too, and this order is the safer default even where it doesn't) ─────────
    for pattern in _OOS_PATTERNS:
        if pattern in html_lower:
            logger.info(f"[apple] OOS signal: '{pattern}' → False")
            return False

    # ── Buttons — skip disabled (attr, aria, OR class-styled) ─────────────────
    for btn in soup.find_all("button"):
        if _is_disabled(btn):
            continue
        text = btn.get_text(strip=True).lower()
        if any(p in text for p in _ADD_PATTERNS):
            logger.info(f"[apple] active button '{text[:40]}' → True")
            return True

    # ── Attrs — skip disabled — name: "buy", "add to bag" ─────────────────────
    for attr in ("data-testid", "aria-label", "id"):
        for el in soup.find_all(attrs={attr: True}):
            if _is_disabled(el):
                continue
            val = (el.get(attr) or "").lower()
            if "add-to-bag" in val or "addtobag" in val or any(p in val for p in _ADD_PATTERNS):
                logger.info(f"[apple] active attr {attr}='{val[:40]}' → True")
                return True

    logger.info("[apple] no conclusive signal → defaulting OUT OF STOCK (False)")
    return False


# ═══════════════════════════════════════════════════════════════════════════
# Pincode-specific availability via Apple's PUBLIC fulfillment-messages API
#
# https://www.apple.com/in/shop/fulfillment-messages?parts.0=<SKU>&location=<pincode>
# This is the exact endpoint Apple's own storefront JS calls to render nearby-
# store pickup availability — confirmed via multiple independent, currently-
# working third-party implementations (some India-specific), called with no
# API key, no login, and no scraped/reverse-engineered internal app credential
# (unlike the Blinkit/Zepto/JioMart cases, which were rejected for that reason).
#
# Design note — why an "unavailable" pickup signal never asserts OOS on its own:
# Apple has very few physical retail stores in India (a handful of metro
# cities), while its courier delivery network covers far more pincodes. So
# "no store shows pickup availability near this pincode" is the COMMON case
# for most Indian pincodes and does NOT reliably mean the product can't be
# bought/delivered there — it just means pickup data isn't informative here.
# Treating that as OOS would create systematic false negatives for most users.
# The pincode-specific lookup is therefore used ONLY to CONFIRM in-stock when
# it can (a genuine, pincode-specific positive signal); any inconclusive or
# negative pickup result falls back to the existing generic page-based check()
# — so accuracy is never worse than before, only better when it can confirm.
# ═══════════════════════════════════════════════════════════════════════════

_FULFILLMENT_URL = "https://www.apple.com/in/shop/fulfillment-messages"
# Timeout for the direct httpx call in _fetch_pickup_availability (see
# _cookie_auth_fetch) — kept generous (30s vs. the official-store
# checker's 20s) since this endpoint has shown slow-but-successful
# responses in the past; a slow response is still strictly better than a
# timeout. Only affects this one call; every other checker's fetch_page
# timeout is untouched.
_FULFILLMENT_TIMEOUT = 30.0

# Apple part numbers ("SKUs") are alphanumeric, always ending in a 2-letter
# country code + "/A" (e.g. "MG6M4HN/A" for India). Matched generically rather
# than hardcoding "HN" since not every listed product's SKU is guaranteed to
# follow that exact regional suffix.
_SKU_INLINE_PATTERN = re.compile(r'"partNumber"\s*:\s*"([A-Z0-9]{5,14}/A)"')
_SKU_JSONLD_INLINE_PATTERN = re.compile(r'"sku"\s*:\s*"([A-Z0-9]{5,14}/A)"')


def _sku_from_offers(offers) -> str | None:
    """
    JSON-LD's "offers" field is usually a single Offer object (a dict), but
    some product pages embed it as a LIST of Offer objects instead (e.g. one
    entry per variant/color) — indexing it with `.get("sku")` directly
    crashed with "'list' object has no attribute 'get'" for those pages
    (the root cause fixed here, not just caught at a caller). Handles both
    shapes; returns the first sku found across list entries, or None if
    `offers` is neither shape or yields no sku.
    """
    if isinstance(offers, dict):
        sku = offers.get("sku")
        return str(sku) if sku else None
    if isinstance(offers, list):
        for entry in offers:
            if isinstance(entry, dict):
                sku = entry.get("sku")
                if sku:
                    return str(sku)
        return None
    return None


def _extract_sku(soup: BeautifulSoup, html: str) -> str | None:
    """
    Extract Apple's public SKU/part number from the already-fetched product
    page — no extra request needed. It's visible in both the JSON-LD block
    and the page's inline JS config. Tried in order; logs which method (if
    any) succeeded so a page-structure change is visible in Railway logs.
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict):
                continue
            sku = item.get("sku") or _sku_from_offers(item.get("offers"))
            if sku:
                logger.info(f"[apple][resolve] SKU from JSON-LD: {sku!r}")
                return str(sku)

    m = _SKU_INLINE_PATTERN.search(html)
    if m:
        logger.info(f"[apple][resolve] SKU from inline partNumber: {m.group(1)!r}")
        return m.group(1)

    m = _SKU_JSONLD_INLINE_PATTERN.search(html)
    if m:
        logger.info(f"[apple][resolve] SKU from inline sku field: {m.group(1)!r}")
        return m.group(1)

    logger.warning("[apple][resolve] could not extract SKU/part number from product page")
    return None


def _build_fulfillment_target(sku: str, pincode: str) -> str:
    # Param set verified 2026-07-27 against a live Chrome DevTools capture
    # of a real, working fulfillment-messages request (see the "PARAM SET
    # HISTORY" note above _cookie_auth_fetch below) — `little`, `mts.1`,
    # and `fts` are gone from Apple's current frontend request entirely,
    # and a new `pl=true` param appeared in their place. The OLD param set
    # below (little/mts.1/fts, no pl) consistently 541'd ("Page Not
    # Found") even with a valid, freshly-refreshed session and a real,
    # currently-listed SKU — this is what actually fixed it, not a
    # cookie/header problem.
    params = {
        "fae": "true",
        "pl": "true",
        "mts.0": "regular",
        "parts.0": sku,
        "location": pincode,
    }
    return f"{_FULFILLMENT_URL}?{urlencode(params)}"


# ── Direct cookie-based fetch (shared by every fulfillment-messages caller
# below AND by the official-store checker further down this file) ─────────
#
# Formerly a navigate-to-the-product-page-then-run-an-in-page-fetch()-via-
# Scrape.do's-"Execute"/Zyte's-"evaluate"-browser-action workaround (built
# after a COLD standalone GET was found to consistently ReadTimeout via
# Zyte). That workaround was itself found broken in production — real
# Railway logs showed "marker not found in returned HTML — Execute/
# evaluate action did not run" on every attempt, both the plain-session
# and super_proxy tiers, every cycle. Root-caused (not just worked around
# again) to the in-page script's own JS: it began with a bare top-level
# `return`, which is a SyntaxError when a provider's evaluate/Execute
# action evaluates the supplied source as a plain expression rather than a
# function body (the documented pattern for both providers avoids `return`
# entirely) — so the script likely never ran at all, on either tier, every
# time, which is exactly what a hard syntax error (not a proxy/timing
# issue) would produce.
#
# Rather than keep fighting the proxy layer, this now mirrors the
# reference implementation directly: a real browser User-Agent + a real
# logged-in Cookie header, loaded from Railway env vars (APPLE_USER_AGENT,
# APPLE_COOKIES) rather than captured/managed by this bot itself — the
# admin refreshes them manually (their own local Chrome cookie-extraction
# script) when Apple's session expires. Same operational model as
# checkers/croma.py's CROMA_APIM_KEY.
#
# HEADER HISTORY (worth keeping — this was mis-diagnosed once already):
# a location=<pincode> request via this same cookie-based transport
# initially 541'd ("Page Not Found"). That was first (wrongly) attributed
# to the location= param itself, and _build_fulfillment_target was
# temporarily replaced with a store=<code>-per-official-store lookup
# requiring each of the 6 stores' internal Apple codes to be manually
# captured. Real screenshots later confirmed the TRUE cause was a header
# mismatch: this function's Accept/Referer were wrong and x-skip-redirect
# was missing entirely (now fixed below, matching a real working reference
# implementation exactly). With the correct headers, location=<pincode>
# works for all 6 official stores AND any arbitrary /trackpickup pincode —
# no store-code capture needed at all, so that whole detour (store=<code>,
# config.APPLE_PICKUP_STORE_CODES, resolve_nearest_official_store) was
# reverted. Moral: verify header/cookie correctness before concluding a
# query PARAM is the problem.
# UPDATE: cookies/UA are now sourced primarily from the auto-refreshed
# session in database.apple_session_cookies (a real headless-Chromium
# session, minted periodically by the separate playwright_scraper service —
# see bot.py's apple_cookie_refresh_loop and config.py's
# APPLE_COOKIE_REFRESH_* settings) rather than solely from a manually-pasted
# APPLE_USER_AGENT/APPLE_COOKIES env var pair. The env vars are kept as a
# fallback — used only until the refresher has populated the DB for the
# first time, or if it's unavailable/failing — so a deploy that hasn't set
# up the Playwright refresher behaves exactly as before.
def _resolve_apple_session() -> tuple[str, str, str]:
    """
    Returns (user_agent, cookies, source) — source is "db" when a valid
    Playwright-refreshed session was found, else "env". Either user_agent or
    cookies may be "" if neither source has a usable value (caller already
    handles that as "not configured", same as before this DB-backed lookup
    was added).

    The DB read is wrapped in try/except so a database hiccup degrades to
    the env-var fallback rather than breaking every Apple pickup check —
    this function must never raise.
    """
    try:
        from database import get_apple_session_cookies
        row = get_apple_session_cookies()
    except Exception as exc:
        logger.warning(f"[apple] could not read auto-refreshed session from DB: {exc}")
        row = None

    if row:
        user_agent = (row.get("user_agent") or "").strip()
        cookies = (row.get("cookies") or "").strip()
        if user_agent and cookies:
            return user_agent, cookies, "db"

    # Fallback: manually-pasted Railway env vars (mirrors checkers/croma.py's
    # _apim_key() pattern — read at call time so an env var change takes
    # effect without an import-order dependency on this module's import time).
    user_agent = os.environ.get("APPLE_USER_AGENT", "").strip()
    cookies = os.environ.get("APPLE_COOKIES", "").strip()
    if user_agent and cookies:
        logger.warning(
            "[apple] no auto-refreshed session in the DB (Playwright refresher "
            "never ran or is failing) — falling back to the APPLE_USER_AGENT/"
            "APPLE_COOKIES env vars."
        )
    return user_agent, cookies, "env"


# PARAM SET / HEADER HISTORY, PART 2 (2026-07-27) — worth keeping next to
# the HEADER HISTORY note above: after the Jul 25 header fix, this endpoint
# started 541'ing ("Page Not Found") again — same status/body as the
# original incident, but this time the SKU/pincode/cookies were all
# confirmed correct (byte-identical extracted SKU vs. request parts.0;
# fails identically across multiple SKUs and pincodes; fails even for the
# refresh loop's own in-page fetch executed by a real, freshly-authenticated
# Chromium session — ruling out a session-freshness or per-page cookie-
# affinity explanation). A live Chrome DevTools "Copy as cURL" capture of a
# genuinely working request revealed the actual cause: Apple's frontend no
# longer sends `little`/`mts.1`/`fts` at all, and now sends `pl=true`
# instead — see _build_fulfillment_target's own comment. The captured
# request's Referer was also the SPECIFIC product variant page, not this
# module's old hardcoded generic "https://www.apple.com/in/shop", and it
# carried the standard Fetch-metadata/Client-Hints headers a real same-
# origin `fetch()` call sends (sec-fetch-*, sec-ch-ua*, cache-control,
# pragma, priority, accept-language) that a plain httpx GET never adds on
# its own. Both are applied below now: `referer` defaults to the real
# tracked product URL (threaded through from every caller below) instead
# of a generic page, and the extra headers are added unconditionally.
#
# UPDATE (same day, second capture): the FULL cURL (not just a header-name
# summary) showed two things the first pass got wrong/incomplete —
#   1. sec-ch-ua* below is copied byte-for-byte from that capture rather
#      than inferred from a generic Chrome/128 guess: the real working
#      session was Edge 150 (UA "...Chrome/150.0.0.0 Safari/537.36
#      Edg/150.0.0.0"), not Chrome 128. Kept in sync with
#      playwright_scraper's own _REALISTIC_USER_AGENT, which now sends
#      that exact UA string too — sec-ch-ua and User-Agent must describe
#      the SAME browser or the mismatch is itself a detectable
#      inconsistency (arguably worse than omitting sec-ch-ua entirely).
#   2. The real capture also included a header this module has never
#      sent: `x-aos-ui-fetch-call-1: <opaque token>` (looks like a
#      per-page-load correlation id Apple's own frontend JS attaches to
#      its fetch() calls). Deliberately NOT added here — its generation
#      algorithm is unknown, and a wrong-but-present value could read as
#      more suspicious than a missing one. If /debugpickup still fails
#      with the headers below, this header is the next thing to
#      investigate — not something to guess at blindly.
#   3. Also worth noting: the real capture did NOT include
#      `x-skip-redirect` at all, even though the Jul 25 fix treated it as
#      essential. Left in place below since there's no evidence it's
#      actively harmful (an extra header a WAF doesn't check for rarely
#      causes rejection, unlike a genuinely missing required one) — but
#      flagging it here in case it turns out to matter later.
#
# UPDATE (same day, third pass): the "150" above was itself found to be a
# growing lie — playwright_scraper's Docker image (the actual engine
# behind whatever User-Agent gets stored and later replayed here) had
# sat pinned at Chromium 129 since this service was built, while the
# User-Agent string claimed a MUCH newer "150". sec-ch-ua below is no
# longer a hardcoded constant — see _sec_ch_ua_for, which derives the
# version from whatever User-Agent is ACTUALLY being sent for a given
# call, so it can never drift out of sync with reality again (whether
# that's the DB session's dynamically-versioned UA, or the APPLE_USER_AGENT
# env var fallback, whatever an admin happened to paste there).
_CHROME_VERSION_PATTERN = re.compile(r"Chrome/(\d+)")


def _sec_ch_ua_for(user_agent: str) -> str:
    """Derives a sec-ch-ua value from whatever Chrome/Edge major version
    is actually present in `user_agent`, rather than a hardcoded string —
    see the module note above this function for why a hardcoded version
    silently went stale once already. Falls back to a fixed, plausible
    major version only if `user_agent` doesn't contain a parseable
    "Chrome/<N>" at all (e.g. an admin-pasted APPLE_USER_AGENT env var in
    some unexpected format) — better than sending an obviously-nonsensical
    number no matter what's actually running."""
    m = _CHROME_VERSION_PATTERN.search(user_agent)
    major = m.group(1) if m else "128"
    browser_name = "Microsoft Edge" if "Edg/" in user_agent else "Google Chrome"
    return f'"Not;A=Brand";v="8", "Chromium";v="{major}", "{browser_name}";v="{major}"'


async def _cookie_auth_fetch(
    target: str, *, log_tag: str, context: str, timeout: float,
    referer: str | None = None, cookie_override: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[dict | None, str | None]:
    """
    One direct httpx GET to `target` (an Apple fulfillment-messages URL) —
    real browser User-Agent + Cookie headers from APPLE_USER_AGENT/
    APPLE_COOKIES env vars, no Scrape.do/Zyte involved. Returns
    (data, error) — error is None on success, else a short human-readable
    reason (missing env vars, network error, HTTP status, non-JSON body),
    for callers that want to surface WHY a fetch failed (e.g.
    /debugpickup's diagnostics, or check_pickup_at_official_stores'
    pincode->result reporting). Never raises.

    401/403, or a 200 that isn't valid JSON (Apple's real API always
    returns JSON when the session is genuinely accepted — a non-JSON 200
    body is the same "silently stalled/challenge page" signature this
    endpoint has shown before, now most likely an expired/rejected cookie
    jar) are both logged as a clear "APPLE_COOKIES likely expired"
    message, mirroring checkers/croma.py's check_via_api 401/403 handling
    for CROMA_APIM_KEY — so an admin scanning logs doesn't have to
    reverse-engineer a generic failure into "go refresh the cookies."

    `log_tag`/`context` control only the log-line prefix (e.g.
    log_tag="resolve", context="fulfillment-messages pincode='400051'") so
    each caller's Railway logs stay grep-able under its own existing
    prefix rather than a generic shared one.

    `referer`: the specific product page URL this fulfillment-messages
    call is for — see the PARAM SET / HEADER HISTORY, PART 2 note above.
    Falls back to the generic shop page only when a caller genuinely has
    no specific product URL to give (there's currently no such caller,
    but this keeps the function usable if one is ever added).

    `client`: an optional pre-built httpx.AsyncClient to reuse instead of
    opening a fresh TCP+TLS connection for this one call — passed by
    check_pickup_at_official_stores for its whole batch of pincode
    requests (a real browser session reuses one connection across many
    requests; opening N brand-new connections for N near-simultaneous
    calls is itself a bot signal, independent of request pacing). When
    None (every other call site, all single one-off requests), a client
    is created and torn down for just this call, same as before.

    `cookie_override`: TEMPORARY diagnostic hook (see admin_handlers.py's
    /debugpickupraw) — when given, replaces the DB/env-resolved cookies
    entirely, while every other header (User-Agent, Referer, sec-ch-ua*,
    etc.) stays exactly what production would send. Exists to isolate
    "are the Playwright-harvested cookies themselves the problem" as a
    single variable, independent of headers/params/replay logic (all of
    which were individually confirmed correct against a real working
    browser capture but still 541'd) — see checkers/apple.py's PARAM SET
    / HEADER HISTORY notes above for the investigation this came out of.
    Safe to remove alongside /debugpickupraw once that's resolved. None
    (every production call site) keeps today's exact behavior unchanged.
    """
    logger.info(f"[apple][{log_tag}] {context} target={target!r}")

    user_agent, resolved_cookies, _source = _resolve_apple_session()
    cookies = cookie_override if cookie_override is not None else resolved_cookies
    if cookie_override is not None:
        logger.info(
            f"[apple][{log_tag}] {context} using a manually-supplied cookie "
            f"OVERRIDE (/debugpickupraw) — NOT the DB/env session, "
            f"cookie_length={len(cookie_override)}"
        )
    if not user_agent or not cookies:
        reason = (
            "no Apple session available — neither the auto-refreshed DB "
            "session nor the APPLE_USER_AGENT/APPLE_COOKIES env vars are set"
        )
        logger.error(
            f"[apple][{log_tag}] {context} {reason} — cannot call Apple's "
            f"fulfillment-messages API directly. Skipping."
        )
        return None, reason

    headers = {
        "User-Agent": user_agent,
        "Cookie": cookies,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9,en-IN;q=0.8",
        "Referer": referer or "https://www.apple.com/in/shop",
        "x-skip-redirect": "true",
        # Fetch-metadata + Client Hints headers a real same-origin
        # fetch() call sends automatically — a plain httpx GET doesn't add
        # any of these on its own. sec-ch-ua below is DERIVED from
        # `user_agent` (see _sec_ch_ua_for's own note on why a hardcoded
        # version went stale once already) — always describes the same
        # browser as the User-Agent header above, whatever that happens
        # to be this call.
        "sec-ch-ua": _sec_ch_ua_for(user_agent),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "priority": "u=1, i",
        "pragma": "no-cache",
        "cache-control": "no-cache",
    }

    # Diagnostic — header NAMES + cookie length only, never the cookie
    # value itself (a live Apple session shouldn't land in Railway logs).
    # Referer IS logged in full (unlike cookies) — it's never sensitive,
    # and its value was previously invisible in logs entirely, which
    # stalled an earlier round of this exact investigation. Added
    # alongside the Playwright refresh's own akamai_markers_present
    # logging to compare "cookies minted" against "cookies actually sent
    # on a failing request" without guessing.
    logger.info(
        f"[apple][{log_tag}] {context} request_headers={sorted(headers.keys())} "
        f"cookie_length={len(cookies)} user_agent={user_agent!r} "
        f"referer={headers['Referer']!r}"
    )

    try:
        if client is not None:
            resp = await client.get(target, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=timeout) as owned_client:
                resp = await owned_client.get(target, headers=headers)
    except Exception as exc:
        reason = f"request failed: {type(exc).__name__}: {exc}"
        logger.warning(f"[apple][{log_tag}] {context} {reason}", exc_info=True)
        return None, reason

    logger.info(f"[apple][{log_tag}] {context} status={resp.status_code}")

    if resp.status_code in (401, 403):
        reason = f"HTTP {resp.status_code} — Apple session likely expired"
        logger.error(
            f"[apple][{log_tag}] {context} HTTP {resp.status_code} — Apple "
            f"session likely expired. If the Playwright auto-refresher "
            f"(PLAYWRIGHT_SCRAPER_URL) is configured it should self-correct "
            f"on its next cycle; otherwise re-run your local cookie-"
            f"extraction script and update the APPLE_COOKIES / "
            f"APPLE_USER_AGENT Railway env vars. Skipping. "
            f"full response (up to 1500 chars): {resp.text[:1500]!r} "
            f"response_headers={dict(resp.headers)}"
        )
        return None, reason
    if resp.status_code != 200:
        reason = f"HTTP {resp.status_code}: {resp.text[:200]!r}"
        logger.warning(
            f"[apple][{log_tag}] {context} {reason} — full response "
            f"(up to 1500 chars): {resp.text[:1500]!r} "
            f"response_headers={dict(resp.headers)}"
        )
        return None, reason

    try:
        return resp.json(), None
    except Exception:
        reason = f"non-JSON response (likely a bot-check/challenge page): {resp.text[:200]!r}"
        logger.error(
            f"[apple][{log_tag}] {context} non-JSON response (likely a "
            f"bot-check/challenge page) — Apple session likely expired; see "
            f"the 401/403 log line above for the same self-correction note. "
            f"full body (up to 1500 chars): {resp.text[:1500]!r} "
            f"response_headers={dict(resp.headers)}"
        )
        return None, reason


async def _fetch_pickup_availability(
    sku: str, pincode: str, product_url: str | None = None,
) -> tuple[dict | None, str | None, list[tuple[str, str | None]]]:
    """
    Calls Apple's fulfillment-messages endpoint directly via
    _build_fulfillment_target + _cookie_auth_fetch above (real Cookie/
    User-Agent headers, no Scrape.do/Zyte). Returns (data, method,
    diagnostics):
      - method is "direct" on success, None on failure.
      - diagnostics is a single-entry list [("direct", error_or_None)] —
        kept as a list (not a bare tuple) for backward compatibility with
        every existing caller (check_pickup_row, refine_with_pincode,
        admin_handlers.py's /debugpickup) that already unpacks/iterates
        it, even though there's only ever one attempt now — no more
        "session"/"session_super_proxy" tiers to report (that whole
        navigate-then-execute-in-session approach is gone; see
        _cookie_auth_fetch's module note above).
    Never raises; callers that only need the data can unpack as
    `data, _method, _diag = await ...`, exactly as before.

    product_url is now used as the request's Referer header (see the
    PARAM SET / HEADER HISTORY, PART 2 note above _cookie_auth_fetch) — a
    real browser's fulfillment-messages call carries the SPECIFIC product
    page's URL as Referer, not a generic shop-page URL, so every caller
    passing a real tracked-product URL here now gets that matched exactly.
    """
    target = _build_fulfillment_target(sku, pincode)
    data, err = await _cookie_auth_fetch(
        target, log_tag="resolve", context=f"fulfillment-messages pincode={pincode!r}",
        timeout=_FULFILLMENT_TIMEOUT, referer=product_url,
    )
    diagnostics: list[tuple[str, str | None]] = [("direct", err)]
    if data is not None:
        return data, "direct", diagnostics
    return None, None, diagnostics


def _evaluate_pickup_availability(data: dict, sku: str) -> bool | None:
    """
    True  - at least one nearby store shows this SKU as pickup available/
             eligible: a genuine, pincode-specific confirmation of in-stock.
    None  - inconclusive: no stores found near this pincode (the common case
             for most Indian pincodes — see module docstring), an
             "unavailable" pickup result (not treated as OOS, for the same
             reason), or an unexpected response shape. Caller falls back to
             the generic check() rather than risk a false OOS.
    """
    logger.info(f"[apple][resolve] raw fulfillment response (truncated): {str(data)[:500]!r}")

    stores = (
        (data.get("body") or {}).get("content", {}).get("pickupMessage", {}).get("stores", [])
    )
    logger.info(f"[apple][resolve] {len(stores)} store(s) returned for this pincode")

    if not stores:
        logger.info(
            "[apple][resolve] no stores found near this pincode (common in India's "
            "sparse Apple Store network) — inconclusive, falling back to generic check"
        )
        return None

    for store in stores:
        part_info = (store.get("partsAvailability") or {}).get(sku, {})
        pickup_display = part_info.get("pickupDisplay", "")
        logger.info(
            f"[apple][resolve] store={store.get('storeName')!r} "
            f"pickupDisplay={pickup_display!r}"
        )
        if pickup_display in ("available", "eligible"):
            logger.info(
                f"[apple][resolve] confirmed available at {store.get('storeName')!r} → True"
            )
            return True

    logger.info(
        "[apple][resolve] no store shows pickup availability for this SKU — NOT "
        "treated as OOS (pickup-only signal; courier delivery coverage is wider "
        "than pickup in India); falling back to generic check"
    )
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Pincode-specific availability via the PUBLIC, ZERO-AUTH /shop/retail/
# pickup-message endpoint (2026-07-28) — a THIRD Apple endpoint, found in
# two independent open-source implementations, that turned out to need NO
# cookies/session/custom headers at all (unlike _fetch_pickup_availability
# above, which stays at 0% success against fulfillment-messages the same
# way). First proven live via /debugpickupmessage (a genuine 200 OK with
# real pickupDisplay/storesCount data), then stress-tested for reliability
# via /debugpickupmessagestress (8/8 200s, byte-identical body, ~0.27s
# average — vs. 30-90+s for the Playwright approach). Response shape
# matches fulfillment-messages exactly (body.content.pickupMessage.stores),
# so _parse_pickup_message_response below reuses the same field names
# _evaluate_pickup_availability/available_stores_for_pickup already know.
#
# This is now tried FIRST by _fetch_pickup_availability_via_page_render
# below, with the existing Playwright/Xvfb approach kept as an AUTOMATIC
# FALLBACK — never a manual switch — for whenever this endpoint errors,
# times out, or returns an unexpected shape. Both paths stay active
# simultaneously; see that function's own docstring for the exact
# fallback contract and the `method_used`/`direct_http_attempted`/
# `direct_http_error` meta fields that make which path actually ran
# visible in diagnostics/logs for every single check.
# ═══════════════════════════════════════════════════════════════════════════

_PICKUP_MESSAGE_URL_TEMPLATE = "https://www.apple.com/{country}/shop/retail/pickup-message"
_PICKUP_MESSAGE_COUNTRY = "in"
# Stress-tested average was ~0.27s (see module note above) — 15s is
# generous headroom for a slow response while still being a small
# fraction of the old 240s Playwright-path timeout below.
_PICKUP_MESSAGE_TIMEOUT = 15.0


def _parse_pickup_message_response(data: dict, sku: str) -> tuple[bool | None, list[dict], str | None, dict]:
    """
    Parses a /shop/retail/pickup-message response body — confirmed
    (2026-07-28, live test) to share fulfillment-messages' exact shape:
    body.content.pickupMessage.stores, each store carrying
    partsAvailability keyed by SKU. Mirrors playwright_scraper's own
    _parse_pickup_stores three-way True/False/None semantics exactly, so
    both paths behave identically from every caller's point of view:
      - True: at least one store shows this SKU as available/eligible for
        pickup — a genuine, pincode-specific confirmation.
      - False: real stores were returned and the SKU WAS found in at
        least one store's partsAvailability, but none show it available —
        a confirmed negative, not "no data".
      - None: no stores near this pincode at all, OR the SKU wasn't found
        in any returned store's partsAvailability — inconclusive, never a
        confirmed negative (same "sparse Indian store network" reasoning
        this module uses everywhere else).

    error is set ONLY when the response is missing body/content/
    pickupMessage entirely (a genuinely unexpected shape) — NOT on the
    ordinary "zero stores"/"sku not found" cases above, which are valid,
    fully-parsed signals. This distinction is what
    _fetch_pickup_availability_via_page_render's fallback decision is
    built on: error triggers a fallback to Playwright, a legitimate None
    does not (it means exactly what Playwright's own None already means
    to every caller).
    """
    try:
        pickup_message = (data.get("body") or {}).get("content", {}).get("pickupMessage")
    except AttributeError:
        pickup_message = None
    if not isinstance(pickup_message, dict):
        return None, [], "unexpected response shape — no body.content.pickupMessage", {
            "store_count": 0, "sku_found": False, "sku_keys_seen": [],
        }
    stores = pickup_message.get("stores")
    if stores is None:
        return None, [], "unexpected response shape — no body.content.pickupMessage.stores", {
            "store_count": 0, "sku_found": False, "sku_keys_seen": [],
        }

    if not stores:
        return None, [], None, {"store_count": 0, "sku_found": False, "sku_keys_seen": []}

    sku_found = False
    matching_stores: list[dict] = []
    sku_keys_seen: set[str] = set()
    for store in stores:
        if not isinstance(store, dict):
            continue
        parts_availability = store.get("partsAvailability") or {}
        sku_keys_seen.update(parts_availability.keys())
        part_info = parts_availability.get(sku)
        if part_info is None:
            continue
        sku_found = True
        if part_info.get("pickupDisplay", "") in ("available", "eligible"):
            matching_stores.append({
                "store_name": store.get("storeName") or "(unnamed store)",
                "location": _extract_store_location(store),
            })

    info = {"store_count": len(stores), "sku_found": sku_found, "sku_keys_seen": sorted(sku_keys_seen)}
    if not sku_found:
        return None, [], None, info
    return (True if matching_stores else False), matching_stores, None, info


async def _fetch_pickup_message_direct(sku: str, pincode: str) -> tuple[bool | None, list[dict], str | None, dict | None]:
    """
    One direct, unauthenticated httpx GET to /shop/retail/pickup-message —
    ZERO cookies, ZERO session, ZERO custom headers (confirmed sufficient
    live; see the module note above this section). Returns (available,
    matching_stores, error, parse_info) with the same True/False/None
    semantics as _parse_pickup_message_response above. error is set on a
    network failure, non-200, non-JSON body, or a genuinely unexpected
    response shape — the exact conditions
    _fetch_pickup_availability_via_page_render treats as "this endpoint
    failed, fall back to Playwright". Never raises.
    """
    url = _PICKUP_MESSAGE_URL_TEMPLATE.format(country=_PICKUP_MESSAGE_COUNTRY)
    params = {"parts.0": sku, "location": pincode}

    try:
        async with httpx.AsyncClient(timeout=_PICKUP_MESSAGE_TIMEOUT) as client:
            resp = await client.get(url, params=params)
    except Exception as exc:
        reason = f"request failed: {type(exc).__name__}: {exc}"
        logger.warning(f"[apple][pickup-message-direct] pincode={pincode!r} {reason}")
        return None, [], reason, None

    if resp.status_code != 200:
        reason = f"HTTP {resp.status_code}: {resp.text[:200]!r}"
        logger.warning(f"[apple][pickup-message-direct] pincode={pincode!r} {reason}")
        return None, [], reason, None

    try:
        data = resp.json()
    except Exception as exc:
        reason = f"non-JSON response: {exc}"
        logger.warning(f"[apple][pickup-message-direct] pincode={pincode!r} {reason}")
        return None, [], reason, None

    available, matching_stores, error, info = _parse_pickup_message_response(data, sku)
    if error is not None:
        logger.warning(f"[apple][pickup-message-direct] pincode={pincode!r} {error}")
    else:
        logger.info(
            f"[apple][pickup-message-direct] pincode={pincode!r}: available={available} "
            f"matching_stores={[s.get('store_name') for s in matching_stores]}"
        )
    return available, matching_stores, error, info


# ═══════════════════════════════════════════════════════════════════════════
# Pincode-specific availability via PAGE-RENDER (playwright_scraper), as an
# alternative to _fetch_pickup_availability above. Exists because the
# direct httpx approach is currently 0% successful — every SKU/pincode/
# param/header/cookie combination tried failed (see this module's PARAM
# SET / HEADER HISTORY notes above _cookie_auth_fetch). playwright_scraper's
# _check_pickup_availability (2026-07-28 network-interception rewrite)
# doesn't scrape rendered DOM at all anymore — it types the pincode into
# Apple's own pickup-search field, which makes the page's OWN JS fire
# pickup-message-recommendations (or fulfillment-messages as a fallback)
# as real backend calls that succeed (200 OK) with genuine in-page session
# context our httpx replay can never reproduce, and parses whichever
# response body arrives directly. See playwright_scraper/main.py's
# _check_pickup_availability docstring for the full architecture and —
# importantly — its available=False caveat, not silently papered over
# here either.
#
# 2026-07-28: no longer the PRIMARY method — _fetch_pickup_availability_
# via_page_render below now tries _fetch_pickup_message_direct (above)
# FIRST, falling back to this browser-based approach only when the direct
# call fails. Kept in full, unchanged otherwise: it's the safety net the
# whole fallback design depends on.
# ═══════════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# Cross-user dedup for page-render checks (2026-07-28): mirrors stock_
# checker.py's own _fetch_cache/_fetch_locks TTL-cache pattern (see that
# module's own note on WHY — different users tracking the identical
# product each got their own row, previously firing one independent
# request per tracker even though the underlying check is identical),
# keyed here by (product_url, pincode) instead of stock_checker.py's
# fetch-parameter key. Matters MORE for Apple pickup specifically than
# for the lighter checkers that pattern was built for: each check here
# launches a real headful Chromium browser (playwright_scraper), and
# MAX_CONCURRENT_CHECKS was JUST cut 4->2 after real resource-contention
# failures under concurrent load — every check avoided here directly
# reduces pressure on that same limited concurrency budget, not just
# request volume.
#
# Caches the FULL (available, matching_stores) result, not just a raw
# fetch like stock_checker.py does — unlike the stock checker, there's no
# further per-user refinement step run on top afterward (available/
# matching_stores from playwright_scraper IS the final per-pincode
# answer), so caching the finished result is both correct and simpler.
#
# Only a genuinely SUCCESSFUL call (error is None) is cached — a failure
# is never cached, so a transient blip for one user's check doesn't get
# silently replayed as a failure for every other user sharing this exact
# (product_url, pincode) within the TTL window; each of THEIR calls still
# gets its own fresh attempt (each already includes playwright_scraper's
# own internal retry loop, so this isn't relying on the cache for
# resilience, only for genuine cross-user duplicate avoidance).
#
# Each cache entry is a dict (not a positional tuple — 2026-07-28,
# widened when /debugpickupavailability was wired through this same
# function so a manual two-calls-in-a-row test could actually observe a
# cache hit) carrying {"ts", "available", "matching_stores", "error",
# "diagnostics", "git_commit_sha"}: "diagnostics"/"git_commit_sha" are
# playwright_scraper's own response fields, previously read only for a
# log line and discarded — now threaded all the way back to the caller
# (via the `meta` return value below) so a cache HIT can still report
# what the underlying real check actually saw, not just "cached: True"
# with nothing else to show for it.
_PAGE_RENDER_CACHE_TTL_SECONDS = 150  # ~83% of APPLE_PICKUP_CHECK_INTERVAL's
                                       # 180s default (config.py) — same
                                       # "comfortably under one cycle's own
                                       # interval" proportion stock_checker.
                                       # py's 240s/300s TTL uses, so this
                                       # collapses duplicates WITHIN one
                                       # apple_pickup_check_loop pass
                                       # without spanning into the next.
_page_render_cache: dict[str, dict] = {}
_page_render_locks: dict[str, asyncio.Lock] = {}
_page_render_locks_guard = asyncio.Lock()


def _page_render_cache_key(product_url: str, pincode: str) -> str:
    return f"{product_url}|{pincode}"


async def _get_page_render_lock(key: str) -> asyncio.Lock:
    async with _page_render_locks_guard:
        lock = _page_render_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _page_render_locks[key] = lock
        return lock


def _prune_page_render_cache(now: float) -> None:
    expired = [k for k, entry in _page_render_cache.items() if now - entry["ts"] >= _PAGE_RENDER_CACHE_TTL_SECONDS]
    for k in expired:
        _page_render_cache.pop(k, None)
        _page_render_locks.pop(k, None)


def _adapt_page_render_stores(matching_stores: list[dict]) -> list[dict]:
    """
    Adapts playwright_scraper's matching_stores shape
    ({"store_name", "pickup_search_quote", "store_pickup_quote"}) into the
    {"store_name", "location"} shape available_stores_for_pickup produces
    and notifications.send_pickup_alert / send_channel_pickup_alert
    expect (2026-07-28, added when wiring page-render into
    check_pickup_row/check_channel_pickup_row).

    "location" here is NOT a geographic address (page-render's endpoint
    doesn't return one) — it's the store's own pickup-timing quote
    (store_pickup_quote preferred, pickup_search_quote as a fallback,
    e.g. "Tomorrow at Apple Noida"), reused best-effort as still-useful
    context in the alert text, same "speculative, several plausible
    values" spirit as _extract_store_location above.
    """
    adapted = []
    for store in matching_stores:
        adapted.append({
            "store_name": store.get("store_name") or "(unnamed store)",
            "location": store.get("store_pickup_quote") or store.get("pickup_search_quote"),
        })
    return adapted


async def _fetch_pickup_availability_via_page_render(
    product_url: str, pincode: str, sku: str | None = None,
) -> tuple[bool | None, list[dict], str | None, dict]:
    """
    Cross-user deduplicated pincode-specific pickup check. As of
    2026-07-28 this tries TWO methods in a fixed order, not one:

      1. PRIMARY — _fetch_pickup_message_direct (above): a single,
         unauthenticated httpx GET to /shop/retail/pickup-message. Only
         attempted when `sku` is given (callers that don't have one yet,
         e.g. admin_handlers.py's /debugpickupavailability today, skip
         straight to step 2 — exactly today's pre-2026-07-28 behavior).
      2. FALLBACK — playwright_scraper's /check-pickup-availability (the
         ENTIRE body of this function before 2026-07-28), used whenever
         step 1 wasn't attempted, OR was attempted and failed (network
         error, non-200, non-JSON, or an unexpected response shape — see
         _parse_pickup_message_response's own docstring for exactly which
         cases count as "failed" vs. a legitimate parsed result). A
         legitimate step-1 result (including available=None from a
         genuinely empty/no-match response) is NOT a failure and does
         NOT fall back — it's returned as-is, same as it always would be
         from step 2.

    Both methods share the SAME cross-user cache (_page_render_cache) and
    the SAME public return contract, so no caller needs to know or care
    which one actually ran for a given call — that's the whole point of
    "automatic fallback, not a manual switch" per the migration this was
    built for.

    Returns (available, matching_stores, error, meta):
      - available / matching_stores / error: same meaning regardless of
        which method produced them — True/False/None have the identical
        semantics _parse_pickup_message_response and playwright_scraper's
        own _parse_pickup_stores both independently document (True =
        confirmed available at >=1 named store; False = real stores
        checked, none available — a confirmed negative; None = no stores
        near this pincode, SKU not found in the response, or the check
        failed before a usable response arrived). matching_stores is
        {"store_name", "location"} either way, empty whenever available
        is not True.
      - meta: {"served_from_cache": bool, "diagnostics": dict | None,
        "git_commit_sha": str | None, "method_used": str | None,
        "direct_http_attempted": bool, "direct_http_error": str | None}.
        "method_used" is "direct_http" or "playwright" — the SAME field
        this docstring's whole design exists to make visible (requirement
        (c) of the fallback-chain request this was built for): which path
        actually produced this result, for every single check, in
        diagnostics/logs. "direct_http_attempted"/"direct_http_error" stay
        populated even when the FINAL result came from the Playwright
        fallback, so a cache hit or a Playwright-served result still shows
        whether/why step 1 was tried and didn't work out.
        "diagnostics"/"git_commit_sha" are playwright_scraper's own
        response fields when method_used=="playwright" (None when
        method_used=="direct_http" — no browser involved, nothing to
        report there); "diagnostics" holds _parse_pickup_message_response's
        own `info` dict instead when method_used=="direct_http", so a
        direct-http None can still be told apart from "no stores at all"
        vs. "SKU not found", same as the Playwright path's parse_info
        already does.

    WIRED into check_pickup_row / check_channel_pickup_row (2026-07-28,
    both now pass their already-known `sku`) — the OLDER direct httpx path
    (_fetch_pickup_availability, cookie-auth against fulfillment-messages)
    stays at 0% success (see this module's PARAM SET / HEADER HISTORY
    notes) and is UNRELATED to the new one added here; that one still isn't
    used by these two functions. admin_handlers.py's
    /debugpickupavailability calls this without a sku (unchanged 2026-07-28
    behavior) — see its own file for the option to pass one manually.
    refine_with_pincode is untouched — a separate decision, not made here.
    playwright_scraper's Dockerfile starts a virtual display (start.sh —
    explicit Xvfb + readiness poll) so its headless=False requirement can
    run on Railway's display-less container — confirmed working via a real
    deploy as of the checkpoint diagnostics this endpoint's rewrite is
    built on top of.

    timeout=240.0 for the Playwright fallback (2026-07-28, raised from
    90.0 when this endpoint started being called from an unattended
    production cycle instead of only manual /debugpickupavailability
    calls): playwright_scraper's own retry loop (APPLE_PICKUP_MAX_ATTEMPTS,
    default 3) can legitimately take close to 90s on its own in a
    realistic worst case (three ~25-30s attempts + inter-attempt delays),
    and a badly-behaving page hitting several individual step timeouts
    within one attempt (pickup_details_wait_timed_out, overlay_wait_timed_
    out, etc. — all real, previously observed failure modes) can push a
    single attempt well past that. 90s risked this client giving up before
    playwright_scraper's own retry loop had a chance to. The direct-http
    attempt has its own, much shorter timeout (_PICKUP_MESSAGE_TIMEOUT,
    15.0s) — see that constant's own note.

    Cross-user deduplicated (2026-07-28) via _page_render_cache — see
    that module-level note above for the full reasoning. Identical
    (product_url, pincode) calls within _PAGE_RENDER_CACHE_TTL_SECONDS
    reuse the most recent SUCCESSFUL result instead of launching another
    check by EITHER method; a lock per cache key prevents a thundering
    herd where several concurrent calls for the same not-yet-cached key
    each launch their own check before the first one has a chance to
    populate the cache. The cache is checked BEFORE deciding which method
    to try, so a cache hit skips both — not just the Playwright browser
    launch this cache was originally built to avoid.

    Never raises.
    """
    no_check_meta = {
        "served_from_cache": False, "diagnostics": None, "git_commit_sha": None,
        "method_used": None, "direct_http_attempted": False, "direct_http_error": None,
    }

    def _cached_meta(cached: dict) -> dict:
        return {
            "served_from_cache": True, "diagnostics": cached["diagnostics"],
            "git_commit_sha": cached["git_commit_sha"], "method_used": cached["method_used"],
            "direct_http_attempted": cached["direct_http_attempted"],
            "direct_http_error": cached["direct_http_error"],
        }

    cache_key = _page_render_cache_key(product_url, pincode)
    now = time.monotonic()
    _prune_page_render_cache(now)

    cached = _page_render_cache.get(cache_key)
    if cached is not None and now - cached["ts"] < _PAGE_RENDER_CACHE_TTL_SECONDS:
        logger.info(
            f"[apple][page-render] cache hit (age={now - cached['ts']:.0f}s) pincode={pincode!r} "
            f"— check skipped (method_used={cached['method_used']!r})"
        )
        return cached["available"], cached["matching_stores"], cached["error"], _cached_meta(cached)

    lock = await _get_page_render_lock(cache_key)
    async with lock:
        # Re-check after acquiring the lock: a concurrent call for the
        # same key may have already populated the cache while we waited.
        now = time.monotonic()
        cached = _page_render_cache.get(cache_key)
        if cached is not None and now - cached["ts"] < _PAGE_RENDER_CACHE_TTL_SECONDS:
            logger.info(
                f"[apple][page-render] cache hit post-lock (age={now - cached['ts']:.0f}s) "
                f"pincode={pincode!r} — check skipped (method_used={cached['method_used']!r})"
            )
            return cached["available"], cached["matching_stores"], cached["error"], _cached_meta(cached)

        direct_http_attempted = False
        direct_http_error: str | None = None
        if sku:
            direct_http_attempted = True
            d_available, d_stores, d_error, d_info = await _fetch_pickup_message_direct(sku, pincode)
            if d_error is None:
                logger.info(
                    f"[apple][page-render] pincode={pincode!r}: served by direct_http "
                    f"(Playwright fallback NOT needed) available={d_available} "
                    f"matching_stores={[s.get('store_name') for s in d_stores]}"
                )
                _page_render_cache[cache_key] = {
                    "ts": time.monotonic(), "available": d_available, "matching_stores": d_stores,
                    "error": None, "diagnostics": d_info, "git_commit_sha": None,
                    "method_used": "direct_http", "direct_http_attempted": True, "direct_http_error": None,
                }
                meta = {
                    "served_from_cache": False, "diagnostics": d_info, "git_commit_sha": None,
                    "method_used": "direct_http", "direct_http_attempted": True, "direct_http_error": None,
                }
                return d_available, d_stores, None, meta
            direct_http_error = d_error
            logger.warning(
                f"[apple][page-render] pincode={pincode!r}: direct_http failed "
                f"({d_error}) — falling back to Playwright"
            )

        from config import PLAYWRIGHT_SCRAPER_URL

        if not PLAYWRIGHT_SCRAPER_URL:
            reason = "PLAYWRIGHT_SCRAPER_URL is not configured"
            logger.warning(f"[apple][page-render] {reason} — skipping pincode={pincode!r}")
            meta = dict(no_check_meta)
            meta["direct_http_attempted"] = direct_http_attempted
            meta["direct_http_error"] = direct_http_error
            return None, [], reason, meta

        try:
            async with httpx.AsyncClient(timeout=240.0) as client:
                resp = await client.post(
                    f"{PLAYWRIGHT_SCRAPER_URL}/check-pickup-availability",
                    json={"url": product_url, "pincode": pincode},
                )
        except Exception as exc:
            reason = f"request to playwright_scraper failed: {type(exc).__name__}: {exc}"
            logger.warning(f"[apple][page-render] {reason}")
            meta = dict(no_check_meta)
            meta["direct_http_attempted"] = direct_http_attempted
            meta["direct_http_error"] = direct_http_error
            return None, [], reason, meta  # never cached — a fresh call may succeed

        if resp.status_code != 200:
            reason = f"playwright_scraper returned HTTP {resp.status_code}: {resp.text[:300]!r}"
            logger.warning(f"[apple][page-render] {reason}")
            meta = dict(no_check_meta)
            meta["direct_http_attempted"] = direct_http_attempted
            meta["direct_http_error"] = direct_http_error
            return None, [], reason, meta  # never cached

        try:
            data = resp.json()
        except Exception as exc:
            reason = f"non-JSON response from playwright_scraper: {exc}"
            logger.warning(f"[apple][page-render] {reason}")
            meta = dict(no_check_meta)
            meta["direct_http_attempted"] = direct_http_attempted
            meta["direct_http_error"] = direct_http_error
            return None, [], reason, meta  # never cached

        available = data.get("available")
        matching_stores = _adapt_page_render_stores(data.get("matching_stores") or [])
        diagnostics = data.get("diagnostics")
        git_commit_sha = data.get("git_commit_sha")
        logger.info(
            f"[apple][page-render] pincode={pincode!r}: served by playwright available={available} "
            f"matching_stores={[s.get('store_name') for s in matching_stores]} "
            f"diagnostics={diagnostics}"
        )
        _page_render_cache[cache_key] = {
            "ts": time.monotonic(), "available": available, "matching_stores": matching_stores,
            "error": None, "diagnostics": diagnostics, "git_commit_sha": git_commit_sha,
            "method_used": "playwright", "direct_http_attempted": direct_http_attempted,
            "direct_http_error": direct_http_error,
        }
        meta = {
            "served_from_cache": False, "diagnostics": diagnostics, "git_commit_sha": git_commit_sha,
            "method_used": "playwright", "direct_http_attempted": direct_http_attempted,
            "direct_http_error": direct_http_error,
        }
        return available, matching_stores, None, meta


# ═══════════════════════════════════════════════════════════════════════════
# Pickup-availability TRACKING (separate from refine_with_pincode above,
# which only ever CONFIRMS the existing generic in-stock check and never
# reports store-level detail). Used by /trackpickup + bot.run_pickup_check_cycle
# — see database.py's pickup_tracking table and bot.py's module docstring
# for the feature. Additive only: nothing below changes check(),
# refine_with_pincode(), or _evaluate_pickup_availability()'s existing
# behavior, so the regular per-pincode stock-confirmation path used in
# production today is untouched.
# ═══════════════════════════════════════════════════════════════════════════

# Best-effort ONLY — unlike the SKU/auth/response-shape details confirmed
# above via multiple independent sources, no confirmed field name for a
# per-store distance or postal address was found on the store object inside
# pickupMessage.stores (independent sources agree on storeName/storeNumber/
# storeListNumber/city/state/partsAvailability, but none show a distance or
# full-address field). Because this only affects optional, cosmetic
# notification text — never the True/False availability signal itself — it's
# safe to speculatively check a short list of plausible keys rather than
# omitting the feature entirely: any that happen to exist in the real
# response get included, any that don't are silently skipped, so a wrong
# guess here can only make a notification slightly less detailed, never
# incorrect.
_STORE_LOCATION_KEYS = (
    "storeDistanceWithUnit", "distance", "address", "city", "state",
)


def _extract_store_location(store: dict) -> str | None:
    """Best-effort, optional 'distance/address' text for a pickup-alert
    notification — see the module note above on why this speculatively
    checks several plausible keys instead of a single confirmed one."""
    parts = []
    for key in _STORE_LOCATION_KEYS:
        val = store.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    if not parts:
        return None
    # De-dupe while preserving order (e.g. "city" and "state" might both
    # legitimately appear; a key repeating the same text as another doesn't).
    seen = set()
    unique_parts = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            unique_parts.append(p)
    return ", ".join(unique_parts)


def _extract_product_name(soup: BeautifulSoup) -> str | None:
    """Best-effort product display name for a pickup-tracking entry — tries
    JSON-LD's "name" field first (same blocks _extract_sku already scans),
    then falls back to the page <title> tag. Returns None if neither is
    found; the caller falls back to a URL-derived name in that case."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and item.get("name"):
                return str(item["name"]).strip()

    if soup.title and soup.title.string:
        return soup.title.string.strip()

    return None


def available_stores_for_pickup(data: dict, sku: str) -> list[dict]:
    """
    Returns every store in `data` (a raw fulfillment-messages response) where
    `sku` currently shows pickupDisplay "available"/"eligible" — the same
    positive-signal check _evaluate_pickup_availability uses, but returning
    full per-store detail (name + best-effort location) instead of a
    collapsed True/None verdict, for the pickup-tracker's notification text.
    Empty list means "no store currently shows pickup available for this
    SKU/pincode" — including the case where `data` has zero stores at all.
    """
    stores = (
        (data.get("body") or {}).get("content", {}).get("pickupMessage", {}).get("stores", [])
    )
    available = []
    for store in stores:
        part_info = (store.get("partsAvailability") or {}).get(sku, {})
        pickup_display = part_info.get("pickupDisplay", "")
        if pickup_display in ("available", "eligible"):
            available.append({
                "store_name": store.get("storeName") or "(unnamed store)",
                "location": _extract_store_location(store),
            })
    return available


async def check_pickup_row(bot, row: dict) -> dict:
    """
    Checks every saved pincode for one database.pickup_tracking row RIGHT
    NOW: calls playwright_scraper's page-render pickup check
    (_fetch_pickup_availability_via_page_render) per pincode, updates the
    row's persisted pincode_status on any change, and sends a
    pickup-availability notification (notifications.send_pickup_alert) on
    a genuine unavailable->available transition.

    Lives HERE rather than in bot.py (where it originated) so both
    bot.run_pickup_check_cycle's scheduled cycle AND handlers.py's
    on-demand /mypickups command can share ONE implementation — handlers.py
    can't import bot.py directly (bot.py imports handlers.router, so that
    would be circular), but both already import checkers.apple.

    Sequential across pincodes WITHIN this row (never concurrent) so
    pincode_status can be safely read-modified-written once at the end
    without a lost-update race between two pincodes of the SAME row
    finishing at different times. Callers may still run different ROWS
    concurrently (see run_pickup_check_cycle's semaphore-gated gather) —
    this only serializes within a row, matching the low pincode-per-row
    counts the feature expects (a handful of pincodes at most).

    Returns {pincode: [store dicts]} for every pincode actually checked
    this call — used by /mypickups to show the caller a live per-pincode
    result immediately, on top of the DB-update-and-notify side effects
    this function already performs (the scheduled cycle simply ignores the
    return value). A pincode whose check fails this round is absent from
    the returned dict, mirroring its "left untouched" DB-status behavior
    below.

    2026-07-28: WIRED to _fetch_pickup_availability_via_page_render
    (playwright_scraper), replacing the direct httpx path
    (_fetch_pickup_availability), which stays at 0% success (see this
    module's PARAM SET / HEADER HISTORY notes) — this row was effectively
    a no-op before this change (every call returned data=None, so nothing
    below the `continue` ever ran). SKU is no longer looked up here at
    all: the page-render endpoint extracts it itself from the live page
    (see playwright_scraper's own SKU-extraction priority note), so
    row["sku"] (still used elsewhere, e.g. refine_with_pincode) is simply
    unused in this function now.

    A pincode whose check returns available=None is left completely
    untouched: mirrors bot._apply_result_to_row's "None = inconclusive,
    skip the write" convention for the regular stock checker — a
    transient failure (or a genuine "no stores near this pincode at all")
    must never flip a previously-available pincode back to unavailable,
    which would otherwise manufacture a spurious future "transition" and
    a duplicate/false alert once the signal recovers. This now also
    covers the outright-failure case (error is not None) since
    _fetch_pickup_availability_via_page_render always returns
    available=None whenever error is set.

    A returned available=False (real candidate stores were checked for
    this pincode, none show the SKU as available — page-render's own
    confirmed-negative case, see that function's docstring) IS persisted
    as a real "unavailable" answer, same reasoning this function's
    predecessor used for "zero stores" under the direct-API path — just
    now backed by a more precise signal that distinguishes it from "no
    stores near this pincode at all" (which stays None/untouched instead,
    unlike the old direct-API path which couldn't tell the two apart).
    """
    # Deferred imports: database.py/notifications.py don't import checkers
    # back (verified — no cycle either direction), but keeping these local
    # avoids any import-order surprise and matches this codebase's
    # established caution around cross-module imports (see
    # checkers/flipkart_api.py's check_stock_with_fallback for the same
    # pattern).
    from database import update_pickup_status, log_pickup_alert_event
    from notifications import send_pickup_alert

    status = dict(row["pincode_status"])
    changed = False
    results: dict[str, list[dict]] = {}
    for pincode in row["pincodes"]:
        try:
            available, stores, _error, _meta = await _fetch_pickup_availability_via_page_render(
                row["url"], pincode, sku=row.get("sku"),
            )
        except Exception as exc:
            logger.error(
                f"[apple][pickup] error checking tracking #{row['id']} pincode={pincode!r}: {exc}"
            )
            log_pickup_alert_event("personal", row["id"], pincode, "fetch_error", str(exc))
            continue
        if available is None:
            continue  # inconclusive this call — leave prior status untouched

        results[pincode] = stores
        now_available = available
        was_available = bool(status.get(pincode, False))

        if now_available != was_available:
            status[pincode] = now_available
            changed = True

        if now_available and not was_available:
            log_pickup_alert_event(
                "personal", row["id"], pincode, "transition_true",
                f"sku={row.get('sku')!r} url={row['url']!r} stores={[s.get('store_name') for s in stores]}",
            )
            try:
                send_status = await send_pickup_alert(bot, row["user_id"], row["name"], pincode, stores)
            except Exception as exc:
                logger.error(
                    f"[apple][pickup] error sending alert for tracking #{row['id']} "
                    f"pincode={pincode!r}: {exc}"
                )
                log_pickup_alert_event("personal", row["id"], pincode, "alert_send_exception", str(exc))
            else:
                event = "alert_sent" if send_status == "sent" else (
                    "alert_suppressed_locked" if send_status == "suppressed_locked" else "alert_send_error"
                )
                log_pickup_alert_event("personal", row["id"], pincode, event, send_status)

    if changed:
        try:
            update_pickup_status(row["id"], status)
        except Exception as exc:
            logger.error(
                f"[apple][pickup] error persisting pincode_status for tracking #{row['id']}: {exc}"
            )
            log_pickup_alert_event("personal", row["id"], None, "status_persist_error", str(exc))

    return results


async def check_channel_pickup_row(bot, row: dict) -> dict:
    """
    Channel-forwarding sibling of check_pickup_row above — same
    fetch/persist/notify shape, but for ONE database.channel_forward_
    pickup_tracking row (now a pincodes LIST + pincode_status dict per
    row, mirroring pickup_tracking's own shape exactly) and forwarding
    alerts to the registered channel (looked up here via
    database.get_forward_channel, same as bot.py's own
    _apply_result_to_channel_forward_row does for the stock side) instead
    of a user_id. Lives here for the same reason check_pickup_row does: bot.py's
    background cycle AND admin_handlers.py's on-demand /checkforwarding
    both need this, and admin_handlers.py can't import bot.py (circular —
    bot.py imports admin_handlers.router), but both already import
    checkers.apple.

    SKU is fetched fresh from the product page if not already cached on
    the row (mirrors bot.py's own _check_apple_official_pickup_group) —
    channel_forward_pickup_tracking rows are created via /addchannelpickup
    with the SKU already resolved at add-time, so this is normally a
    no-op fetch-skip; only relevant if that initial extraction somehow
    didn't get persisted.

    Sequential across pincodes WITHIN this row, same reasoning as
    check_pickup_row's own docstring (avoids a lost-update race on this
    row's single persisted pincode_status dict).

    Returns {pincode: [store dicts]} for every pincode actually checked
    this call — used by /checkforwarding to show a live per-pincode
    result immediately, same as check_pickup_row's own return value is
    used by /mypickups. A pincode whose API call fails this round is
    absent from the returned dict and left untouched in the persisted
    status, mirroring check_pickup_row's own inconclusive-result handling.

    Status + last_checked are persisted unconditionally at the end of
    every call (unlike check_pickup_row, which only writes `if changed`)
    so /listforwarding's last-checked display stays accurate even on
    cycles with no transitions.

    2026-07-28: WIRED to _fetch_pickup_availability_via_page_render, same
    change and same reasoning as check_pickup_row above — see that
    function's own docstring for the full detail (0%-success direct httpx
    path replaced, None/False semantics, etc.). `sku` is still resolved
    here (unlike check_pickup_row, which drops SKU lookup entirely) only
    because update_channel_forward_pickup_status persists it separately;
    it's no longer passed into the availability check itself.
    """
    # Deferred imports — see check_pickup_row's own note above for why.
    from database import (
        update_channel_forward_pickup_status, is_forwarding_paused, get_forward_channel,
        log_pickup_alert_event,
    )
    from notifications import send_channel_pickup_alert

    sku = row.get("sku")
    if not sku:
        try:
            resp = await fetch_page(row["url"], render_js=NEEDS_JS, timeout=30.0, site="apple")
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            sku = _extract_sku(soup, resp.text)
        except Exception as exc:
            logger.error(
                f"[apple][channel-pickup] product page fetch/SKU extraction "
                f"failed for #{row['id']} url={row['url']!r}: {exc}"
            )
            return {}
        if not sku:
            logger.warning(
                f"[apple][channel-pickup] could not extract a SKU for "
                f"#{row['id']} url={row['url']!r} — skipping this cycle"
            )
            return {}

    status = dict(row.get("pincode_status") or {})
    results: dict[str, list[dict]] = {}
    channel = None
    for pincode in row.get("pincodes") or []:
        try:
            available, stores, _error, _meta = await _fetch_pickup_availability_via_page_render(
                row["url"], pincode, sku=sku,
            )
        except Exception as exc:
            logger.error(
                f"[apple][channel-pickup] error checking #{row['id']} pincode={pincode!r}: {exc}"
            )
            log_pickup_alert_event("channel", row["id"], pincode, "fetch_error", str(exc))
            continue
        if available is None:
            continue  # inconclusive this call — leave prior status untouched

        results[pincode] = stores
        now_available = available
        was_available = bool(status.get(pincode, False))
        status[pincode] = now_available

        if now_available and not was_available:
            log_pickup_alert_event(
                "channel", row["id"], pincode, "transition_true",
                f"sku={sku!r} url={row['url']!r} stores={[s.get('store_name') for s in stores]}",
            )
            if is_forwarding_paused():
                logger.info(
                    f"[apple][channel-pickup] #{row['id']} pincode={pincode!r} "
                    f"transitioned to available but forwarding is paused — "
                    f"alert suppressed."
                )
                log_pickup_alert_event("channel", row["id"], pincode, "alert_suppressed_paused")
            else:
                if channel is None:
                    channel = get_forward_channel() or {}
                if not channel:
                    logger.warning(
                        f"[apple][channel-pickup] #{row['id']} pincode={pincode!r} "
                        f"transitioned to available but no channel is "
                        f"registered — alert skipped."
                    )
                    log_pickup_alert_event("channel", row["id"], pincode, "alert_no_channel_registered")
                else:
                    try:
                        send_status = await send_channel_pickup_alert(bot, channel["chat_id"], row["name"], pincode, stores)
                    except Exception as exc:
                        logger.error(
                            f"[apple][channel-pickup] alert failed for #{row['id']} "
                            f"pincode={pincode!r}: {exc}"
                        )
                        log_pickup_alert_event("channel", row["id"], pincode, "alert_send_exception", str(exc))
                    else:
                        event = "alert_sent" if send_status == "sent" else "alert_send_error"
                        log_pickup_alert_event("channel", row["id"], pincode, event, send_status)

    try:
        update_channel_forward_pickup_status(row["id"], status, sku=sku)
    except Exception as exc:
        logger.error(
            f"[apple][channel-pickup] error persisting pincode_status for #{row['id']}: {exc}"
        )
        log_pickup_alert_event("channel", row["id"], None, "status_persist_error", str(exc))

    return results


async def refine_with_pincode(
    soup: BeautifulSoup, html: str, pincode: str, generic_result: bool, url: str,
) -> bool:
    """
    Called from stock_checker.py when a pincode is available for an Apple
    product. Tries to CONFIRM in-stock via the public fulfillment-messages
    API; never downgrades the result to OOS based on pincode data alone —
    worst case (SKU not found, API fails, no stores nearby, or nothing
    available for pickup), it returns generic_result unchanged, so accuracy
    is never worse than the pre-existing page-based check.

    `url` is the tracked product page URL. It's threaded through to
    _fetch_pickup_availability for signature backward-compatibility only —
    that function no longer uses it (see its own docstring); kept here so
    this function's own signature/callers don't need to change either.
    """
    sku = _extract_sku(soup, html)
    if not sku:
        logger.warning(
            "[apple][resolve] no SKU extracted — cannot run pincode-specific "
            "lookup, falling back to generic check"
        )
        return generic_result

    data, _method, _diag = await _fetch_pickup_availability(sku, pincode, url)
    if data is None:
        return generic_result

    pincode_result = _evaluate_pickup_availability(data, sku)
    if pincode_result is None:
        return generic_result

    return pincode_result


# ═══════════════════════════════════════════════════════════════════════════
# Official-store pickup checker (checkers.apple.check_pickup_at_official_
# stores + bot.run_apple_official_pickup_cycle) — a THIRD, separate Apple
# signal from check()/refine_with_pincode() above. Checks the SAME
# fulfillment-messages endpoint, but against a FIXED list of India's 6
# physical Apple Store pincodes (config.APPLE_PICKUP_PINCODES) for every
# /add-tracked apple.com product automatically — unlike refine_with_pincode
# (the user's own single saved pincode only) or the separate opt-in
# /trackpickup system (user-chosen pincodes, requires a command to set up).
#
# Shares _cookie_auth_fetch/_resolve_apple_session AND
# _build_fulfillment_target (all defined above, next to
# _fetch_pickup_availability) for the actual request — this checker and
# _fetch_pickup_availability hit the exact same Apple endpoint the exact
# same way (real Cookie/User-Agent headers, no Scrape.do/Zyte,
# location=<pincode> — confirmed via real screenshots to work for all 6
# official-store pincodes once the request headers were fixed; see
# _cookie_auth_fetch's own module note for that history). Consequently
# this checker spends ZERO Scrape.do/Zyte credits and is automatically
# absent from database.get_zyte_usage_summary's per-site breakdown /
# admin_handlers.py's /creditusage (that table is only ever written to
# from inside zyte_client.fetch_page) — no separate credit-tracking
# exclusion needed.
# ═══════════════════════════════════════════════════════════════════════════

_OFFICIAL_STORE_TIMEOUT = 20.0


async def _fetch_official_store_availability(
    sku: str, pincode: str, *, product_url: str | None = None, client: httpx.AsyncClient | None = None,
) -> dict | None:
    """
    One direct httpx GET straight to Apple's fulfillment-messages endpoint
    for one of the 6 fixed official-store pincodes — see _cookie_auth_fetch
    (shared with _fetch_pickup_availability above) for the actual request/
    auth/error-handling logic. Returns the parsed JSON on success, None on
    any failure (missing env vars, network error, HTTP status, non-JSON
    body — all logged there). Never raises.

    `client`: optional shared httpx.AsyncClient, passed through to
    _cookie_auth_fetch — see check_pickup_at_official_stores below, the
    only caller that passes one (its whole 6-pincode batch reuses a
    single connection instead of opening a fresh one per pincode).
    """
    target = _build_fulfillment_target(sku, pincode)
    data, _err = await _cookie_auth_fetch(
        target, log_tag="official-stores", context=f"pincode={pincode!r}",
        timeout=_OFFICIAL_STORE_TIMEOUT, referer=product_url, client=client,
    )
    return data


# Randomized delay between consecutive pincode requests within one
# check_pickup_at_official_stores batch — replaces the old asyncio.gather
# parallel burst (all 6 pincodes hit at the exact same instant), which is
# itself a strong bot signal no real browser session produces. Range
# chosen to look like plausible human inter-request timing without
# meaningfully slowing the official-store cycle (worst case ~5×5s=25s of
# added delay per product, well within APPLE_PICKUP_CHECK_INTERVAL).
_PINCODE_DELAY_MIN_SECONDS = 2.0
_PINCODE_DELAY_MAX_SECONDS = 5.0


async def check_pickup_at_official_stores(
    sku: str, pincodes: list[str], *, product_url: str | None = None,
) -> dict[str, list[dict]]:
    """
    Checks `sku` against every pincode in `pincodes` (config.
    APPLE_PICKUP_PINCODES in production) SEQUENTIALLY — NOT concurrently
    (see _PINCODE_DELAY_MIN/MAX_SECONDS above for why the old
    asyncio.gather-based parallel burst was replaced) — with a randomized
    delay between each pincode, reusing ONE httpx.AsyncClient across the
    whole batch instead of opening a fresh TCP+TLS connection per pincode
    (see _cookie_auth_fetch's `client` param). Both changes exist to make
    this endpoint's traffic look less like an automated burst and more
    like a real session's occasional, connection-reusing requests — part
    of investigating why APPLE_COOKIES' session was dying after ~2 hours.

    Returns {pincode: [store dicts]} — the SAME shape checkers.apple.
    check_pickup_row/available_stores_for_pickup already use, reused here
    rather than a parallel parsing implementation. A pincode whose request
    failed (see _fetch_official_store_availability) is simply absent from
    the returned dict — the caller treats a missing key as "inconclusive
    this cycle for that pincode", never as a confirmed "not available".
    """
    results: dict[str, list[dict]] = {}
    async with httpx.AsyncClient(timeout=_OFFICIAL_STORE_TIMEOUT) as client:
        for i, pincode in enumerate(pincodes):
            data = await _fetch_official_store_availability(sku, pincode, product_url=product_url, client=client)
            if data is not None:
                results[pincode] = available_stores_for_pickup(data, sku)
            if i < len(pincodes) - 1:
                await asyncio.sleep(random.uniform(_PINCODE_DELAY_MIN_SECONDS, _PINCODE_DELAY_MAX_SECONDS))
    return results
