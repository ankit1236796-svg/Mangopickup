"""
playwright_scraper/main.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Standalone Playwright + Chromium service, serving two independent purposes
in the main bot's architecture:

1. A pilot scraper for iQOO and Vivo, built to test replacing their current
   Scrape.do render=true checks — those burn render credits at scale; this
   self-hosts the same JS-rendering step behind an optional metered proxy
   instead. NOT wired into the main bot's production checkers (checkers/
   iqoo.py, checkers/vivo.py are untouched) — /check-stock exists purely for
   standalone testing/comparison.
2. The main bot's Apple pickup-checking IS wired to this service, but only
   for periodic cookie refresh (/refresh-apple-cookies, called by bot.py's
   apple_cookie_refresh_loop every APPLE_COOKIE_REFRESH_INTERVAL) — never
   for the actual per-check requests, which stay on plain httpx in the main
   bot for speed. See that endpoint's own section below for why.

ISOLATION: this is a completely separate Railway service — its own
container, its own process, its own requirements.txt (Playwright is NOT a
dependency of the main bot). If this crashes, gets blocked, or a proxy runs
out of quota: /check-stock's iQOO/Vivo pilot is simply unaffected (nothing
in the main bot calls it); /refresh-apple-cookies failing just means the
main bot's stored Apple session goes unrefreshed until the next cycle or
this service recovers — the main bot falls back to whatever session it last
had, then to the APPLE_COOKIES/APPLE_USER_AGENT env vars (see
checkers/apple.py's _resolve_apple_session). Never fatal to the main bot.

HTTP surface:
  POST /check-stock    Body: {"url": str, "store": "iqoo"|"vivo"}. Returns
                        {"url", "store", "in_stock": bool|None, "signal": str,
                        "attempts": int}. in_stock is None ("check failed")
                        when no conclusive signal was found after retrying —
                        NEVER a guessed False, unlike the main bot's own
                        checkers (deliberate: this is a pilot being tuned, an
                        inconclusive read should surface for investigation,
                        not silently default to "out of stock").
  POST /debug-network  Body: {"url": str, "pincode": str (optional, default
                        "110001")}. Applies pincode as a cookie (best-effort
                        — see _capture_network_calls), loads the page, and
                        records every XHR/fetch response whose URL contains
                        a serviceability/delivery/pincode/availability/
                        stock/fulfillment keyword. Returns {"url", "pincode",
                        "matched_requests": [{"url", "method", "status",
                        "body"}...], "total_requests_seen", "matched_count",
                        "all_responses_seen": [{"url","status",
                        "resource_type"}...] (lightweight, every response —
                        not just matches, capped at 100), "diagnostics":
                        {"goto_status", "goto_error", "final_url",
                        "page_title", "page_crashed",
                        "networkidle_timed_out", "networkidle_error",
                        "html_length", "html_snippet", ...} so a silent
                        navigation failure or anti-bot block page is visible
                        in the response, not just a suspiciously low count.
                        For admin diagnostic use (e.g. RelianceDigital's
                        /debugreliance) — hit directly, no auth (matches
                        /check-stock; this whole service has none).
  POST /debug-dom       Body: {"url": str, "click_text": str (optional)}.
                        _capture_network_calls' sibling for DOM STRUCTURE
                        discovery instead of network traffic (see
                        _capture_dom_elements) — loads the page, EXPLICITLY
                        waits for pickUpDetails to appear (observed
                        rendering inconsistently run-to-run — present
                        twice on one capture, absent on the next identical
                        URL — so a fixed dwell alone isn't reliable; see
                        diagnostics.pickup_details_wait_timed_out), then
                        returns every element whose data-autom/text/
                        attributes mention pickup, fulfillment, store, zip,
                        pincode, or location, plus the full outerHTML of
                        any pickUpDetails element(s) AND every plausibly-
                        clickable descendant within it (button/link/
                        role=button/tabindex/onclick/cursor:pointer),
                        regardless of exact text — its real trigger text
                        may vary by state. When click_text is given, also
                        clicks the first element containing that text
                        (case-insensitive) after the initial capture, waits
                        for any resulting modal/overlay to settle, and
                        re-runs the same extraction — returned as
                        "after_click" — so a pincode input hidden behind a
                        click trigger becomes visible without guessing at
                        the modal's own selectors first. Returns {"url",
                        "page_title", "data_autom_elements",
                        "pincode_like_inputs", "pickup_related_buttons",
                        "pickup_details_html", "pickup_details_clickables",
                        "after_click", "diagnostics" (includes
                        "click_result" when click_text was given)}. Built
                        for the 2026-07-27 Apple pickup-widget selector
                        discovery (see checkers/apple.py's
                        investigation notes) — no auth, same as every
                        other endpoint here.
  POST /debug-pickup-flow
                        Body: {"url": str, "pincode": str (optional,
                        default "110001")}. /debug-dom's more targeted
                        successor (see _capture_pickup_flow) — performs
                        the FULL confirmed pincode-check interaction:
                        click the "Check availability" trigger, wait for
                        the overlay, fill pincode into its zipCode input,
                        click Continue, wait for results, then return the
                        overlay's full outerHTML (the actual point — what
                        the result markup looks like) plus the same
                        keyword-filtered DOM scan /debug-dom uses. Returns
                        {"url", "pincode", "overlay_html",
                        "data_autom_elements", "pincode_like_inputs",
                        "pickup_related_buttons", "pickup_details_html",
                        "pickup_details_clickables", "diagnostics"
                        (per-step errors: trigger_click_error,
                        overlay_wait_timed_out, zip_fill_error,
                        continue_click_error, results_wait_timed_out)}.
                        No auth, same as every other endpoint here.
  POST /refresh-apple-cookies
                        Body: {"url": str (an apple.com/<locale>/shop/...
                        product page), "pincode": str}. Loads the page in a
                        real headless-Chromium session, extracts its SKU,
                        and performs an in-page fetch() of Apple's
                        fulfillment-messages endpoint (same query params as
                        the main bot's checkers/apple.py) for `pincode` —
                        this is the exact request Apple's own pickup-
                        availability UI would trigger, run from inside the
                        already-loaded page's JS context so it carries a
                        genuine browser TLS fingerprint, headers, and
                        cookies. Returns {"url", "pincode", "cookies":
                        "name=value; ..." (semicolon-joined, ready to use as
                        a Cookie header), "user_agent": str,
                        "pincode_check_confirmed": bool (True only if that
                        in-page fetch returned HTTP 200 with valid JSON),
                        "diagnostics": {...}}. Requires header
                        "X-Internal-Token: <INTERNAL_REFRESH_TOKEN>" only if
                        that env var is set on this service (unset = no auth,
                        matching every other endpoint's default here). See
                        _refresh_apple_cookies's own docstring for the full
                        reasoning on why this uses an in-page fetch instead
                        of clicking through Apple's pickup UI widget.
  GET  /health         Unauthenticated: {"ok", "max_concurrent_checks",
                        "proxy_configured", "supported_stores",
                        "apple_cookie_refresh_auth_required"}.

Stock-detection logic for iqoo/vivo (check_iqoo_vivo_stock, _OOS_PATTERNS,
_offer_availability) is PORTED from checkers/iqoo.py and checkers/vivo.py —
both already probe-confirmed reliable (JSON-LD offers.availability primary,
embedded-JSON fallback, explicit OOS text last resort) against real
in-stock/OOS product pages. This sandbox has no live network access to
verify the signal still holds when sourced via Playwright+proxy instead of
Scrape.do's render=true — see README.md for the live-verification steps
this needs once deployed.
"""

import importlib.metadata
import json
import logging
import os
import re
import threading
import time
from typing import Callable
from urllib.parse import urlencode, urlparse

from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("playwright_scraper")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PORT = int(os.getenv("PORT", "8080"))
HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() != "false"

# Railway auto-injects this for every deploy built from git — surfaced in
# /health and _check_pickup_availability's own diagnostics (2026-07-28)
# so a test result can self-report which commit actually produced it,
# instead of needing to cross-reference Railway's dashboard/logs by
# timestamp to rule out "new code hasn't deployed yet" vs. "new code
# deployed but the fix didn't work" — a real ambiguity hit once already
# when a fix changed behavior but not the diagnostics dict's shape, so
# two different commits' outputs looked identical. None locally / on any
# platform that doesn't set this var.
GIT_COMMIT_SHA = os.getenv("RAILWAY_GIT_COMMIT_SHA")

# Optional shared secret for /refresh-apple-cookies only (every other
# endpoint here stays unauthenticated, by design — see the module docstring
# and README). Unset means no auth check, matching this service's existing
# "internal pilot" posture; set it (and the matching
# PLAYWRIGHT_SCRAPER_INTERNAL_TOKEN on the main bot side) once this handles
# real session cookies rather than just stock-check results.
INTERNAL_REFRESH_TOKEN = os.getenv("INTERNAL_REFRESH_TOKEN", "")

# Requirement #7 ("Add resource limits: cap con...") — the message cut off
# there; taken as "cap concurrent browser instances", since that's the
# standard resource-exhaustion risk for a self-hosted scraper (each headless
# Chromium instance can use 150-300MB+ RAM). A Semaphore bounds how many
# checks run in parallel; excess requests queue for a free slot rather than
# spawning unbounded browsers. Flag if a different limit was meant (memory
# ceiling, requests/min, etc.) — Railway's own container limits are separate
# and unaffected by this.
#
# Raised 2 -> 4 (2026-07-28, when _check_pickup_availability was wired into
# check_pickup_row/check_channel_pickup_row's production cycles), then
# REVERTED 4 -> 2 the same day: a real "BrowserType.launch: Target page,
# context or browser has been closed" 502 surfaced under concurrent load
# at 4 (a manual /debugpickupavailability check running alongside
# /mypickups' multi-item check). --disable-dev-shm-usage was added to
# _new_browser_and_context as a first, standard mitigation (see that
# function's own note) but did NOT fully resolve it — a real /mypickups
# run after that fix still showed multiple "⚠️ check failed" results
# alongside genuine "❌ not available" ones. Since neither this
# environment nor the person operating it could inspect Railway's actual
# container CPU/RAM graphs at the failure window to pin down the exact
# bottleneck (headful Chromium's own memory footprint vs. something
# else), the conservative move is reducing concurrent resource
# contention directly rather than continuing to guess at the precise
# cause. headful Chromium (required here — see APPLE_PICKUP_HEADLESS
# above) is heavier than headless, so 2 is a real ceiling worth respecting
# until there's actual evidence (e.g. Railway's dashboard, or a clean
# multi-run test at a raised value) that this container can sustain more.
# NOTE: with 2 slots and up to ~4 concurrently-checking rows (see
# check_pickup_row's own docstring — different rows run concurrently,
# each checking its own pincodes sequentially), some checks will now
# genuinely queue for a slot rather than running immediately — up to
# SLOT_WAIT_TIMEOUT_SECONDS before failing outright if a slot never frees
# up in time. That's the accepted tradeoff of this drop: less throughput,
# more headroom per running browser.
MAX_CONCURRENT_CHECKS = int(os.getenv("MAX_CONCURRENT_CHECKS", "2"))
# How long an incoming request waits for a free concurrency slot before
# giving up (returns a "check failed" result rather than queueing forever).
SLOT_WAIT_TIMEOUT_SECONDS = float(os.getenv("SLOT_WAIT_TIMEOUT_SECONDS", "60"))

NAV_TIMEOUT_MS = int(os.getenv("NAV_TIMEOUT_MS", "20000"))
# How long to wait specifically for the primary stock signal (a JSON-LD
# script tag) to appear before giving up on THIS attempt — requirement #6's
# "stock element isn't found within a reasonable timeout". Not a hard
# failure: the fallback signals (embedded JSON, OOS text) still run against
# whatever HTML is present even if this wait times out.
SIGNAL_WAIT_TIMEOUT_MS = int(os.getenv("SIGNAL_WAIT_TIMEOUT_MS", "8000"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_DELAY_SECONDS = float(os.getenv("RETRY_DELAY_SECONDS", "2"))

# Apple cookie-refresh-specific timing (see _refresh_apple_cookies) — kept
# as its OWN separate knobs rather than reusing NAV_TIMEOUT_MS/SIGNAL_WAIT_
# TIMEOUT_MS above (those tune the iQOO/Vivo /check-stock pilot's JSON-LD
# wait, a different concern). Diagnostic logging confirmed _abck/bm_sz/
# ak_bmsc (Akamai Bot Manager's own marker cookies) were ALL absent from a
# refresh that captured cookies immediately after domcontentloaded with no
# dwell time at all — the session was never validated by Akamai in the
# first place, which is why it died in ~9 minutes instead of the ~2 hours a
# manually-copied, actively-used real browser session showed. These three
# values give Akamai's own sensor/telemetry JS time to run before cookies
# are captured and the browser is torn down.
APPLE_REFRESH_NETWORKIDLE_TIMEOUT_MS = int(os.getenv("APPLE_REFRESH_NETWORKIDLE_TIMEOUT_MS", "8000"))
APPLE_REFRESH_DWELL_MS = int(os.getenv("APPLE_REFRESH_DWELL_MS", "5000"))
APPLE_REFRESH_POST_FETCH_WAIT_MS = int(os.getenv("APPLE_REFRESH_POST_FETCH_WAIT_MS", "2000"))

# _check_pickup_availability's own headless override — defaults to headFUL
# (false), the OPPOSITE default of the module-wide HEADLESS above. A
# controlled same-network A/B test (2026-07-28) confirmed headless
# Chromium fails to render Apple's pickUpDetails widget at all, while an
# otherwise-identical headful run succeeds consistently. NOTE: headful
# needs a real display; the Dockerfile starts one via start.sh (explicit
# Xvfb start + readiness poll, replacing an earlier xvfb-run-wrapped CMD
# that deployed fine but still left DISPLAY pointing at nothing — see
# start.sh's own comment for why). IMPORTANT: this function is the ONLY
# code path in this whole service that launches headless=False — every
# OTHER function (_capture_pickup_flow included) never sets headless at
# all and runs on the module-wide HEADLESS default (True), so a working
# deploy of THOSE endpoints proves nothing about whether Xvfb itself is
# actually up; don't mistake one for evidence of the other again. This
# env var lets a deploy override back to headless=true without a code
# change, e.g. if Xvfb ever turns out to be unnecessary or itself causes
# a new problem.
APPLE_PICKUP_HEADLESS = os.getenv("APPLE_PICKUP_HEADLESS", "false").lower() == "true"

# _check_pickup_availability retry loop (2026-07-28, added after repeated
# real runs showed genuinely inconsistent Apple-side behavior — sometimes
# pickUpDetails renders slowly, sometimes the pincode lookup's backend
# call takes variable time to fire, sometimes it doesn't fire at all in
# the allotted window — each individually confirmed via diagnostics
# rather than assumed. Modeled on bot.py's run_apple_cookie_refresh_cycle
# (APPLE_COOKIE_REFRESH_MAX_ATTEMPTS/RETRY_DELAY_*): retry the WHOLE
# check (fresh browser + fresh navigation each attempt, not just the
# in-page type/wait step) up to this many times, stopping early the
# moment one attempt returns a conclusive (non-None) available signal.
APPLE_PICKUP_MAX_ATTEMPTS = int(os.getenv("APPLE_PICKUP_MAX_ATTEMPTS", "3"))
APPLE_PICKUP_RETRY_DELAY_SECONDS = float(os.getenv("APPLE_PICKUP_RETRY_DELAY_SECONDS", "3"))

# /debug-network: default pincode applied when the caller doesn't specify
# one — an arbitrary real Indian pincode, not tied to any particular store.
DEFAULT_DEBUG_PINCODE = os.getenv("DEBUG_NETWORK_DEFAULT_PINCODE", "110001")
# Substrings (case-insensitive) of a response URL worth capturing — these
# are the endpoint-name patterns a serviceability/stock-by-pincode API call
# would plausibly contain.
_NETWORK_CAPTURE_KEYWORDS = (
    "serviceability", "delivery", "pincode", "availability", "stock", "fulfillment",
    # "pickup" added 2026-07-28: pickup-message-recommendations, found via
    # _diagnose_zipcode_validation's broader unfiltered capture, doesn't
    # match any of the keywords above on its own — needed so its response
    # body gets captured too, not just fulfillment-messages'.
    "pickup",
)
# Cap on how much of each matched response body is kept, so one huge JSON
# blob can't blow up the HTTP response back to the caller.
_MAX_BODY_CHARS = 5000

# Webshare (or any HTTP-auth) proxy — entirely optional. Unset PROXY_HOST
# means "no proxy", so this runs directly for local testing before buying a
# proxy plan; set all four once you have Webshare credentials.
PROXY_HOST = os.getenv("PROXY_HOST", "")
PROXY_PORT = os.getenv("PROXY_PORT", "")
PROXY_USERNAME = os.getenv("PROXY_USERNAME", "")
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "")


def _proxy_config() -> dict | None:
    """Playwright's launch(proxy=...) dict, or None if PROXY_HOST/PORT
    aren't both set — proxy is fully optional, never required to run."""
    if not PROXY_HOST or not PROXY_PORT:
        return None
    cfg = {"server": f"http://{PROXY_HOST}:{PROXY_PORT}"}
    if PROXY_USERNAME:
        cfg["username"] = PROXY_USERNAME
    if PROXY_PASSWORD:
        cfg["password"] = PROXY_PASSWORD
    return cfg


# ---------------------------------------------------------------------------
# Anti-detection: vanilla headless Chromium (no user-agent override, no
# viewport, navigator.webdriver=true, empty plugins list) is commonly
# fingerprinted and served a near-empty challenge/block page instead of the
# real site — a live /debug-network run against two real RelianceDigital
# URLs came back with total_requests_seen=1 for BOTH (only the document
# itself, no scripts/XHR at all), which is exactly that symptom, not a
# per-page fluke. The same class of issue was already confirmed and fixed
# this same way for whatsapp_forwarder's WhatsApp Web automation earlier —
# applied here too, to every browser this service launches (not just
# /debug-network), since it's a systemic defense, not RelianceDigital-
# specific.
# ---------------------------------------------------------------------------
_REALISTIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def _apple_user_agent_for(browser) -> str:
    """
    Apple-specific User-Agent, used ONLY by _refresh_apple_cookies below —
    NOT _REALISTIC_USER_AGENT above, which every OTHER browser this
    service launches still uses unchanged (RelianceDigital/BigBasket/
    Zepto/Blinkit checks, /check-stock, /debug-network).

    2026-07-27 UPDATE: the Chrome/Edge VERSION here is now DERIVED from
    `browser.version` (the real, actually-running Chromium build's own
    reported version) rather than a hardcoded string. Original version:
    the Dockerfile's Playwright image sat pinned at v1.47.0 (Chromium
    129, Sept 2024) unchanged since this service was built, while this
    constant hardcoded "Chrome/150.0.0.0 ... Edg/150.0.0.0" — copied from
    a real captured working request — creating a growing, detectable
    engine-vs-claimed-identity mismatch: the TLS/JA3 fingerprint, HTTP/2
    behavior, and JS feature surface all still reflected the real frozen
    Chromium 129 engine no matter what the header claimed. Deriving the
    version here means bumping the Dockerfile's image tag in the future
    automatically keeps this claim honest, with no separate hardcoded
    number to remember to update. Still a custom desktop-shaped UA
    string, not the browser's own raw default (which announces
    "HeadlessChrome" — a strong, well-known bot signal real desktop
    browsers never send) — keeps the Edge-branded template from the real
    working capture, just with an always-accurate version number.

    checkers/apple.py's own sec-ch-ua header (sent later, on the bot
    service's httpx replay of whatever session this mints) derives its
    version the same way — from the User-Agent string actually being
    sent, not a second hardcoded constant — see its _sec_ch_ua_for.
    """
    chrome_version = browser.version  # e.g. "131.0.6778.85" — real, not guessed
    major = chrome_version.split(".")[0]
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36 Edg/{major}.0.0.0"
    )


_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
}
"""


def _new_browser_and_context(
    pw, user_agent: str | Callable[[object], str] = _REALISTIC_USER_AGENT, headless: bool = HEADLESS,
):
    """Launch a browser + context with the anti-detection measures above —
    shared by _render_page (/check-stock), _capture_network_calls
    (/debug-network), and _refresh_apple_cookies (with its own
    _apple_user_agent_for override) so all three get the same defenses,
    not just whichever endpoint happened to be under investigation when
    this was added.

    `user_agent` may be a plain string (every caller except the Apple
    refresh) or a callable taking the just-launched Browser and returning
    a string (_apple_user_agent_for) — the browser instance has to exist
    before its own real version can be read, so a callable is invoked
    AFTER launch but before the context (and therefore the UA) is
    created.

    `headless` defaults to the module-wide HEADLESS setting but is
    overridable per-call — added 2026-07-28 for _check_pickup_availability,
    which needs headless=False specifically (see APPLE_PICKUP_HEADLESS):
    a controlled same-network A/B test confirmed headless Chromium fails to
    render Apple's pickUpDetails widget at all, while headful succeeds
    consistently. Every other caller keeps passing the module default
    unchanged."""
    # --disable-blink-features=AutomationControlled (2026-07-27, found via
    # a working third-party Apple pickup monitor's own config — see
    # checkers/apple.py's investigation notes) — blocks the CDP-automation
    # signal at the browser-launch level, BEFORE any page ever loads.
    # Different from (and stronger than) _STEALTH_INIT_SCRIPT's
    # navigator.webdriver override below: that patches a JS property
    # AFTER a real browser already set it true, which some fingerprinting
    # can detect as a patched getter; this flag stops Chromium from ever
    # setting the underlying automation flag in the first place. Applied
    # to every browser this service launches (not just Apple) — a
    # systemic defense, not site-specific, same reasoning as every other
    # measure in this function.
    #
    # --disable-dev-shm-usage (2026-07-28, added after a real
    # "BrowserType.launch: Target page, context or browser has been
    # closed" 502 surfaced from /check-pickup-availability while multiple
    # checks were running concurrently, shortly after MAX_CONCURRENT_
    # CHECKS was raised 2->4). This container's Dockerfile/start.sh never
    # configured Chromium's shared-memory usage at all — confirmed by
    # direct inspection, no prior mention of shm/no-sandbox anywhere in
    # this service. Docker containers commonly cap /dev/shm at a small
    # default (often 64MB) regardless of available RAM, and Chromium uses
    # /dev/shm heavily for rendering; running MULTIPLE Chromium instances
    # concurrently (now up to 4, since the MAX_CONCURRENT_CHECKS bump)
    # multiplies that pressure. This exact error message is one of the
    # most commonly documented Playwright/Puppeteer container failure
    # modes, and Playwright's own docs recommend this flag (or a larger
    # /dev/shm mount, which isn't controllable from inside this
    # Dockerfile) as the standard fix — it makes Chromium fall back to
    # /tmp instead of crashing when shared memory is exhausted, at a
    # modest performance cost. NOTE: this is a plausible, code-grounded
    # candidate fix for the reported symptom, not a confirmed root cause
    # — nothing in this environment can inspect Railway's actual
    # container memory/CPU graphs to prove shm exhaustion was what
    # happened at that specific timestamp; if the error recurs after this
    # ships, that's real evidence against this hypothesis and against
    # Xvfb-concurrency as the cause too (a single Xvfb/X11 server
    # ordinarily handles multiple simultaneous client connections without
    # issue — that's not a well-documented bottleneck the way shm
    # exhaustion is), pointing instead toward genuine container-level
    # memory/CPU exhaustion under this concurrency level.
    browser = pw.chromium.launch(
        headless=headless,
        proxy=_proxy_config(),
        args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
    )
    resolved_user_agent = user_agent(browser) if callable(user_agent) else user_agent
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=resolved_user_agent,
        locale="en-US",
    )
    context.add_init_script(_STEALTH_INIT_SCRIPT)
    return browser, context


# ---------------------------------------------------------------------------
# Bandwidth optimization: block everything except what's needed to read the
# DOM/JS-injected stock signal. This is the whole point of self-hosting
# against a metered (GB-priced) proxy — a product page with full images can
# be 2-5MB; blocking image/font/media/stylesheet cuts that dramatically
# since we only ever read page.content(), never render anything visually.
# ---------------------------------------------------------------------------
_ALLOWED_RESOURCE_TYPES = {"document", "script", "xhr", "fetch"}


def _make_resource_blocker(stats: dict):
    """Returns a Playwright route handler bound to a per-request stats dict
    (allowed/blocked counts + bytes-so-far isn't available pre-response, so
    we count requests, not bytes) — logged after each check so bandwidth
    savings are visible, not just assumed."""

    def _handler(route):
        resource_type = route.request.resource_type
        if resource_type in _ALLOWED_RESOURCE_TYPES:
            stats["allowed"] = stats.get("allowed", 0) + 1
            route.continue_()
        else:
            stats["blocked"] = stats.get("blocked", 0) + 1
            route.abort()

    return _handler


# ---------------------------------------------------------------------------
# Stock detection — ported from checkers/iqoo.py and checkers/vivo.py
# (identical logic in both; both stores' probe found JSON-LD availability
# reliably differentiates OOS vs in-stock). Returns (in_stock, signal):
# in_stock is True/False for a confident read, None when nothing conclusive
# was found THIS attempt (the caller retries on None rather than defaulting
# to False — see MAX_RETRIES / _fetch_and_check).
# ---------------------------------------------------------------------------
_OOS_PATTERNS = [
    "out of stock", "sold out", "currently unavailable",
    "notify me", "coming soon", "temporarily unavailable",
]


def _offer_availability(offers) -> str:
    """Extract the first availability string from an 'offers' value that may
    be a single Offer dict, an AggregateOffer dict wrapping a nested offers
    list, or a plain list of Offer dicts. Returns "" when none is found."""
    if isinstance(offers, dict):
        avail = offers.get("availability", "")
        if avail:
            return str(avail)
        nested = offers.get("offers", [])
        if isinstance(nested, list):
            for o in nested:
                if isinstance(o, dict) and o.get("availability"):
                    return str(o["availability"])
        elif isinstance(nested, dict) and nested.get("availability"):
            return str(nested["availability"])
    elif isinstance(offers, list):
        for o in offers:
            if isinstance(o, dict) and o.get("availability"):
                return str(o["availability"])
    return ""


def check_iqoo_vivo_stock(soup: BeautifulSoup, html: str) -> tuple[bool | None, str]:
    html_lower = html.lower()

    # ── JSON-LD (primary, proven-reliable signal per the original probe) ──
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict):
                continue
            avail = _offer_availability(item.get("offers", {}))
            if not avail:
                continue
            if "InStock" in avail:
                return True, f"JSON-LD offers.availability={avail!r}"
            if "OutOfStock" in avail or "Discontinued" in avail:
                return False, f"JSON-LD offers.availability={avail!r}"

    # ── Embedded JSON (fallback if JSON-LD is ever absent) ─────────────────
    for key in ('"in_stock":true', '"inStock":true', '"is_available":true', '"isAvailable":true'):
        if key in html:
            return True, f"embedded JSON key {key!r} present"
    for key in ('"in_stock":false', '"inStock":false', '"is_available":false', '"isAvailable":false'):
        if key in html:
            return False, f"embedded JSON key {key!r} present"

    # ── Explicit OOS text (last-resort negative signal) ─────────────────────
    for pattern in _OOS_PATTERNS:
        if pattern in html_lower:
            return False, f"OOS text pattern {pattern!r} found"

    return None, "no conclusive signal found on this attempt"


CHECKERS = {
    "iqoo": check_iqoo_vivo_stock,
    "vivo": check_iqoo_vivo_stock,
}


# ---------------------------------------------------------------------------
# Rendering + retry
# ---------------------------------------------------------------------------
_check_semaphore = threading.Semaphore(MAX_CONCURRENT_CHECKS)


def _render_page(url: str) -> str:
    """Launch a fresh, isolated browser for this one check (simplest
    possible isolation between requests — no shared state, no thread-safety
    concerns with Playwright's sync API — at the cost of ~1-2s browser
    startup per check, acceptable for a pilot's request volume). Bounded by
    _check_semaphore so at most MAX_CONCURRENT_CHECKS browsers run at once."""
    acquired = _check_semaphore.acquire(timeout=SLOT_WAIT_TIMEOUT_SECONDS)
    if not acquired:
        raise RuntimeError(
            f"too many concurrent checks (max {MAX_CONCURRENT_CHECKS}) — "
            f"timed out after {SLOT_WAIT_TIMEOUT_SECONDS}s waiting for a free slot"
        )
    try:
        with sync_playwright() as pw:
            browser, context = _new_browser_and_context(pw)
            try:
                page = context.new_page()
                stats: dict = {}
                page.route("**/*", _make_resource_blocker(stats))
                page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                try:
                    page.wait_for_selector(
                        'script[type="application/ld+json"]', timeout=SIGNAL_WAIT_TIMEOUT_MS
                    )
                except PlaywrightTimeoutError:
                    logger.info(
                        f"[render] JSON-LD script tag didn't appear within "
                        f"{SIGNAL_WAIT_TIMEOUT_MS}ms for {url} — proceeding with "
                        f"whatever rendered (fallback signals may still catch it)"
                    )
                html = page.content()
                logger.info(
                    f"[render] {url}: {stats.get('allowed', 0)} requests allowed, "
                    f"{stats.get('blocked', 0)} blocked (image/font/media/stylesheet)"
                )
                return html
            finally:
                browser.close()
    finally:
        _check_semaphore.release()


def _fetch_and_check(url: str, store: str) -> dict:
    checker = CHECKERS[store]
    last_signal = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            html = _render_page(url)
        except Exception as exc:
            last_signal = f"page render failed: {exc}"
            logger.warning(f"[check-stock] attempt {attempt}/{MAX_RETRIES} render failed for {url}: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
            continue

        soup = BeautifulSoup(html, "html.parser")
        in_stock, signal = checker(soup, html)
        if in_stock is not None:
            logger.info(f"[check-stock] {url} -> in_stock={in_stock} ({signal}) on attempt {attempt}")
            return {"in_stock": in_stock, "signal": signal, "attempts": attempt}

        last_signal = signal
        logger.info(f"[check-stock] attempt {attempt}/{MAX_RETRIES}: {signal} for {url} — retrying")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS)

    logger.error(f"[check-stock] check failed after {MAX_RETRIES} attempts for {url}: {last_signal}")
    return {
        "in_stock": None,
        "signal": f"check failed after {MAX_RETRIES} attempts — last result: {last_signal}",
        "attempts": MAX_RETRIES,
    }


# ---------------------------------------------------------------------------
# /debug-network: record XHR/fetch calls made while loading a page, filtered
# to serviceability/stock-by-pincode-looking endpoints. For admin diagnostic
# use (RelianceDigital's stock signal appears to live behind a pincode-
# gated API call rather than in the page's own embedded JSON — see
# admin_handlers.py's /debugreliance on the main bot side).
# ---------------------------------------------------------------------------

_MAX_ALL_SEEN_REPORTED = 100  # cap the lightweight all-responses list


def _capture_network_calls(url: str, pincode: str) -> dict:
    """Launch an isolated browser, apply `pincode` as a cookie, load `url`,
    and record every XHR/fetch response whose URL contains one of
    _NETWORK_CAPTURE_KEYWORDS.

    The cookie is a best-effort guess at how pincode selection works on the
    target site — this sandbox has no live network access to confirm
    RelianceDigital's actual mechanism (cookie vs. localStorage vs. a UI
    widget the user has to type into and submit). If it turns out a cookie
    alone doesn't trigger the serviceability call, that itself is useful
    diagnostic information (matched_count will be 0 despite total_requests_
    seen being nonzero) — report back what's actually observed so this can
    be adjusted, same as every other live-tuning step this pilot has needed.

    Every step that can silently fail (navigation, the network-idle wait,
    reading page content, an individual response's body) is wrapped and
    logged into the returned "diagnostics" dict rather than left to fail
    invisibly — a prior live run came back with total_requests_seen=1 for
    two different real product pages, which pointed at either an
    unhandled navigation error or the page being served an anti-bot
    challenge instead of the real content; this makes either case visible
    in the response instead of just a suspiciously low count.

    Bounded by the same _check_semaphore /check-stock uses, so total
    concurrent browser instances across both endpoints stays capped."""
    acquired = _check_semaphore.acquire(timeout=SLOT_WAIT_TIMEOUT_SECONDS)
    if not acquired:
        raise RuntimeError(
            f"too many concurrent checks (max {MAX_CONCURRENT_CHECKS}) — "
            f"timed out after {SLOT_WAIT_TIMEOUT_SECONDS}s waiting for a free slot"
        )
    try:
        with sync_playwright() as pw:
            browser, context = _new_browser_and_context(pw)
            try:
                domain = urlparse(url).netloc
                if domain:
                    context.add_cookies([{
                        "name": "pincode", "value": pincode,
                        "domain": domain, "path": "/",
                    }])

                page = context.new_page()
                stats: dict = {}
                page.route("**/*", _make_resource_blocker(stats))

                diagnostics: dict = {
                    "goto_status": None,
                    "goto_error": None,
                    "final_url": None,
                    "page_title": None,
                    "page_crashed": False,
                    "networkidle_timed_out": False,
                    "networkidle_error": None,
                    "content_error": None,
                    "response_listener_errors": 0,
                    "html_length": None,
                    "html_snippet": None,
                }

                def _on_crash(_page):
                    diagnostics["page_crashed"] = True
                    logger.error(f"[debug-network] page CRASHED while loading {url}")

                page.on("crash", _on_crash)

                matched: list[dict] = []
                all_seen: list[dict] = []

                def _on_response(response):
                    try:
                        resource_type = response.request.resource_type
                    except Exception:
                        resource_type = "?"
                    all_seen.append({
                        "url": response.url, "status": response.status, "resource_type": resource_type,
                    })
                    try:
                        url_lower = response.url.lower()
                        if not any(kw in url_lower for kw in _NETWORK_CAPTURE_KEYWORDS):
                            return
                        try:
                            body = response.text()
                        except Exception as exc:
                            body = f"<could not read response body: {exc}>"
                        if body and len(body) > _MAX_BODY_CHARS:
                            body = body[:_MAX_BODY_CHARS] + f"...(truncated, {len(body)} chars total)"
                        matched.append({
                            "url": response.url,
                            "method": response.request.method,
                            "status": response.status,
                            "body": body,
                        })
                    except Exception as exc:
                        diagnostics["response_listener_errors"] += 1
                        logger.error(f"[debug-network] response listener error on {response.url}: {exc}")

                page.on("response", _on_response)

                try:
                    main_response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    if main_response is not None:
                        diagnostics["goto_status"] = main_response.status
                except Exception as exc:
                    diagnostics["goto_error"] = str(exc)
                    logger.error(f"[debug-network] page.goto failed for {url}: {exc}")

                try:
                    diagnostics["final_url"] = page.url
                except Exception as exc:
                    logger.error(f"[debug-network] could not read page.url for {url}: {exc}")
                try:
                    diagnostics["page_title"] = page.title()
                except Exception as exc:
                    logger.error(f"[debug-network] could not read page.title() for {url}: {exc}")

                if diagnostics["goto_error"] is None:
                    try:
                        page.wait_for_load_state("networkidle", timeout=SIGNAL_WAIT_TIMEOUT_MS)
                    except PlaywrightTimeoutError:
                        diagnostics["networkidle_timed_out"] = True
                        logger.info(f"[debug-network] networkidle wait timed out for {url}")
                    except Exception as exc:
                        diagnostics["networkidle_error"] = str(exc)
                        logger.error(f"[debug-network] networkidle wait raised for {url}: {exc}")

                try:
                    html = page.content()
                    diagnostics["html_length"] = len(html)
                    diagnostics["html_snippet"] = html[:500]
                except Exception as exc:
                    diagnostics["content_error"] = str(exc)
                    logger.error(f"[debug-network] page.content() failed for {url}: {exc}")

                logger.info(
                    f"[debug-network] {url}: {len(all_seen)} response(s) seen, "
                    f"{len(matched)} matched; diagnostics={diagnostics}"
                )
                return {
                    "matched_requests": matched,
                    "total_requests_seen": len(all_seen),
                    "matched_count": len(matched),
                    "all_responses_seen": all_seen[:_MAX_ALL_SEEN_REPORTED],
                    "all_responses_truncated": len(all_seen) > _MAX_ALL_SEEN_REPORTED,
                    "diagnostics": diagnostics,
                }
            finally:
                browser.close()
    finally:
        _check_semaphore.release()


# ---------------------------------------------------------------------------
# /debug-dom: _capture_network_calls' sibling for DOM STRUCTURE discovery
# rather than network traffic — built specifically to find Apple's real
# pincode/pickup-availability widget selectors (2026-07-27 investigation:
# fulfillment-messages never gets a validated Akamai session no matter
# what's sent to it, so the plan shifted to reading the RENDERED PAGE
# instead of replaying an API call — see checkers/apple.py's own notes).
# Neither this sandbox's own outbound fetches (WebFetch) nor the Wayback
# Machine can reach apple.com at all (both blocked outright), so real
# selectors can only come from THIS service's own already-working
# Playwright browser actually loading the page — same reasoning as
# _capture_network_calls existing for RelianceDigital's serviceability
# call instead of guessing at it, and the exact same "verify empirically,
# don't guess" lesson this investigation already learned once with the
# fulfillment-messages param set. Keyword-filtered rather than dumping
# the full DOM (which would be enormous and mostly noise) — every element
# whose data-autom/text/attributes mention pickup, fulfillment, store,
# zip, pincode, or location, since those are the only parts of the page
# actually relevant to this investigation.
# ---------------------------------------------------------------------------
_DOM_CAPTURE_JS = """() => {
    function truncate(s, n) {
        if (!s) return s;
        s = s.trim().replace(/\\s+/g, ' ');
        return s.length > n ? s.slice(0, n) + '...' : s;
    }
    function matchesKeywords(parts) {
        const joined = parts.filter(Boolean).join(' ');
        return /pickup|pick up|fulfil|store|location|zip|pin ?code/i.test(joined);
    }

    const dataAutomElements = Array.from(document.querySelectorAll('[data-autom]'))
        .map(el => ({
            tag: el.tagName.toLowerCase(),
            data_autom: el.getAttribute('data-autom'),
            text: truncate(el.textContent || '', 120),
            aria_label: el.getAttribute('aria-label'),
            aria_disabled: el.getAttribute('aria-disabled'),
        }))
        .filter(e => matchesKeywords([e.data_autom, e.text, e.aria_label]))
        .slice(0, 60);

    const pincodeLikeInputs = Array.from(document.querySelectorAll('input'))
        .map(el => ({
            placeholder: el.placeholder || null,
            aria_label: el.getAttribute('aria-label'),
            name: el.name || null,
            id: el.id || null,
            type: el.type || null,
            data_autom: el.getAttribute('data-autom'),
        }))
        .filter(e => matchesKeywords([e.placeholder, e.aria_label, e.name, e.id, e.data_autom]))
        .slice(0, 30);

    const pickupRelatedButtons = Array.from(document.querySelectorAll('button, a[role="button"], a'))
        .map(el => ({
            tag: el.tagName.toLowerCase(),
            data_autom: el.getAttribute('data-autom'),
            text: truncate(el.textContent || '', 80),
            aria_label: el.getAttribute('aria-label'),
        }))
        .filter(e => matchesKeywords([e.data_autom, e.text, e.aria_label]))
        .slice(0, 40);

    // Full outerHTML of every pickUpDetails element — added 2026-07-27
    // once a real capture confirmed this is the actual container Apple
    // renders pickup status into (data-autom="pickUpDetails"), so its
    // exact clickable structure (the "Check availability" trigger, most
    // likely) can be inspected directly instead of guessed at.
    const pickupDetailsElements = Array.from(document.querySelectorAll('[data-autom="pickUpDetails"]'));
    const pickupDetailsHtml = pickupDetailsElements.map(el => {
        const html = el.outerHTML || '';
        return html.length > 4000 ? html.slice(0, 4000) + '...(truncated)' : html;
    });

    // Every plausibly-clickable descendant WITHIN pickUpDetails specifically
    // — added same day a click_text="Check availability" attempt timed out
    // with no matching visible text, and pickUpDetails wasn't even present
    // that run (see the module note on pickUpDetailsWaitResult below): its
    // exact clickable trigger text may vary by state (no-location vs.
    // already-has-a-location, an error state, etc.), so this reports EVERY
    // candidate (button, link, role=button, tabindex, onclick handler, or
    // cursor:pointer styling) regardless of exact text, rather than
    // requiring a guess at the right one up front.
    function isClickable(el) {
        if (['BUTTON', 'A'].includes(el.tagName)) return true;
        if (el.getAttribute('role') === 'button') return true;
        if (el.hasAttribute('tabindex')) return true;
        if (el.hasAttribute('onclick')) return true;
        try {
            return window.getComputedStyle(el).cursor === 'pointer';
        } catch (e) {
            return false;
        }
    }
    const pickupDetailsClickables = [];
    for (const container of pickupDetailsElements) {
        for (const el of container.querySelectorAll('*')) {
            if (!isClickable(el)) continue;
            pickupDetailsClickables.push({
                tag: el.tagName.toLowerCase(),
                data_autom: el.getAttribute('data-autom'),
                text: truncate(el.textContent || '', 100),
                aria_label: el.getAttribute('aria-label'),
                aria_disabled: el.getAttribute('aria-disabled'),
                id: el.id || null,
            });
        }
    }

    return {
        page_title: document.title,
        data_autom_elements: dataAutomElements,
        pincode_like_inputs: pincodeLikeInputs,
        pickup_related_buttons: pickupRelatedButtons,
        pickup_details_html: pickupDetailsHtml,
        pickup_details_clickables: pickupDetailsClickables.slice(0, 40),
    };
}"""


def _capture_dom_elements(url: str, click_text: str | None = None) -> dict:
    """Launch an isolated browser, load `url`, wait for it to settle, then
    run _DOM_CAPTURE_JS to pull out every keyword-matching element — see
    the module note above for why this exists. Uses _apple_user_agent_for
    when `url` is an apple.com page (matching what production would
    actually send), the default _REALISTIC_USER_AGENT otherwise, same as
    every other endpoint in this file.

    Every step that can silently fail (navigation, the network-idle wait,
    the DOM extraction itself) is captured into "diagnostics" rather than
    left to fail invisibly — mirrors _capture_network_calls' own
    reasoning exactly.

    `click_text` (2026-07-27): a real capture found no pincode input on
    initial page load, but DID find a rendered "Check availability" label
    inside pickUpDetails — consistent with the pincode field living behind
    a click-triggered modal/overlay, not on the page from the start. When
    given, this clicks the FIRST element whose text contains `click_text`
    (case-insensitive substring match, via Playwright's own text= locator
    engine) after the initial capture, waits for any resulting overlay to
    settle, then runs _DOM_CAPTURE_JS a SECOND time — returned as
    "after_click" alongside the original (now "before_click"-equivalent)
    top-level fields, so a modal that's appended to the DOM (not a
    same-origin iframe — those wouldn't be visible to this same
    querySelectorAll-based capture) becomes visible without guessing at
    its own selectors first."""
    acquired = _check_semaphore.acquire(timeout=SLOT_WAIT_TIMEOUT_SECONDS)
    if not acquired:
        raise RuntimeError(
            f"too many concurrent checks (max {MAX_CONCURRENT_CHECKS}) — "
            f"timed out after {SLOT_WAIT_TIMEOUT_SECONDS}s waiting for a free slot"
        )
    try:
        with sync_playwright() as pw:
            is_apple = "apple.com" in urlparse(url).netloc.lower()
            browser, context = _new_browser_and_context(
                pw, user_agent=_apple_user_agent_for if is_apple else _REALISTIC_USER_AGENT,
            )
            try:
                page = context.new_page()

                diagnostics: dict = {
                    "goto_status": None,
                    "goto_error": None,
                    "final_url": None,
                    "page_title": None,
                    "networkidle_timed_out": False,
                    "networkidle_error": None,
                    "extract_error": None,
                    "click_result": None,
                    "pickup_details_wait_timed_out": None,
                }

                try:
                    main_response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    if main_response is not None:
                        diagnostics["goto_status"] = main_response.status
                except Exception as exc:
                    diagnostics["goto_error"] = str(exc)
                    logger.error(f"[debug-dom] page.goto failed for {url}: {exc}")

                try:
                    diagnostics["final_url"] = page.url
                    diagnostics["page_title"] = page.title()
                except Exception as exc:
                    logger.error(f"[debug-dom] could not read final_url/page_title for {url}: {exc}")

                if diagnostics["goto_error"] is None:
                    try:
                        page.wait_for_load_state("networkidle", timeout=SIGNAL_WAIT_TIMEOUT_MS)
                    except PlaywrightTimeoutError:
                        diagnostics["networkidle_timed_out"] = True
                        logger.info(f"[debug-dom] networkidle wait timed out for {url}")
                    except Exception as exc:
                        diagnostics["networkidle_error"] = str(exc)
                        logger.error(f"[debug-dom] networkidle wait raised for {url}: {exc}")

                    # pickUpDetails has been observed rendering
                    # INCONSISTENTLY run-to-run on the identical URL
                    # (present twice in one capture, entirely absent in
                    # another) — a fixed dwell alone isn't a reliable
                    # enough signal that it's actually rendered by the
                    # time the DOM gets scanned, since it appears to
                    # populate asynchronously/lazily rather than being
                    # present at networkidle. Wait explicitly for the
                    # selector itself (generous timeout — this may be
                    # tied to the same variable Akamai-scoring behavior
                    # seen elsewhere in this investigation, not pure
                    # rendering speed), falling back to "whatever's
                    # there" if it never appears rather than blocking
                    # indefinitely — matches this file's existing
                    # "capture the failure mode, don't hide it"
                    # philosophy (see networkidle_timed_out above).
                    try:
                        page.wait_for_selector('[data-autom="pickUpDetails"]', timeout=10000)
                        diagnostics["pickup_details_wait_timed_out"] = False
                    except PlaywrightTimeoutError:
                        diagnostics["pickup_details_wait_timed_out"] = True
                        logger.info(
                            f"[debug-dom] pickUpDetails never appeared within 10s for "
                            f"{url} — proceeding with whatever's rendered"
                        )
                    except Exception as exc:
                        diagnostics["pickup_details_wait_timed_out"] = True
                        logger.warning(f"[debug-dom] pickUpDetails wait raised for {url}: {exc}")

                # Same dwell as the reference implementation that inspired
                # this whole investigation used (3s) — even once
                # pickUpDetails' own container appears, giving its inner
                # content (status text, click targets) a little more time
                # to finish rendering.
                page.wait_for_timeout(3000)

                result = {
                    "page_title": None, "data_autom_elements": [],
                    "pincode_like_inputs": [], "pickup_related_buttons": [],
                    "pickup_details_html": [], "pickup_details_clickables": [],
                }
                try:
                    result = page.evaluate(_DOM_CAPTURE_JS)
                except Exception as exc:
                    diagnostics["extract_error"] = str(exc)
                    logger.error(f"[debug-dom] DOM extraction failed for {url}: {exc}")

                after_click = None
                if click_text:
                    click_result: dict = {
                        "attempted": True, "found": False,
                        "clicked_element_text": None, "click_error": None,
                        "post_click_extract_error": None,
                    }
                    try:
                        locator = page.get_by_text(click_text, exact=False).first
                        locator.wait_for(timeout=5000)
                        click_result["found"] = True
                        try:
                            click_result["clicked_element_text"] = locator.inner_text()
                        except Exception:
                            pass
                        locator.click(timeout=5000)
                    except Exception as exc:
                        click_result["click_error"] = str(exc)
                        logger.error(f"[debug-dom] click on text={click_text!r} failed for {url}: {exc}")
                    else:
                        # Bounded, best-effort — a modal opening doesn't
                        # necessarily trigger new network activity, so a
                        # networkidle timeout here is expected/harmless,
                        # not an error (same reasoning as the initial
                        # page-load wait above).
                        try:
                            page.wait_for_load_state("networkidle", timeout=SIGNAL_WAIT_TIMEOUT_MS)
                        except Exception:
                            pass
                        page.wait_for_timeout(3000)
                        try:
                            after_click = page.evaluate(_DOM_CAPTURE_JS)
                        except Exception as exc:
                            click_result["post_click_extract_error"] = str(exc)
                            logger.error(f"[debug-dom] post-click DOM extraction failed for {url}: {exc}")
                    diagnostics["click_result"] = click_result

                logger.info(
                    f"[debug-dom] {url}: {len(result.get('data_autom_elements', []))} data-autom "
                    f"match(es), {len(result.get('pincode_like_inputs', []))} input match(es), "
                    f"{len(result.get('pickup_related_buttons', []))} button match(es); "
                    f"diagnostics={diagnostics}"
                )
                return {**result, "after_click": after_click, "diagnostics": diagnostics}
            finally:
                browser.close()
    finally:
        _check_semaphore.release()


# ---------------------------------------------------------------------------
# /debug-pickup-flow: the FULL confirmed pincode-check interaction,
# end-to-end — click "Check availability" -> fill zipCode -> click
# Continue -> wait for results -> dump the resulting overlay markup. This
# is /debug-dom's more targeted successor: /debug-dom's own generic
# exploration (2026-07-27) found and confirmed the exact real selectors
# this uses (data-autom values, not guessed):
#   [data-autom^="productLocatorTriggerLink"]  the "Check availability"
#                                               trigger inside pickUpDetails
#                                               — SKU-suffixed
#                                               (productLocatorTriggerLink_
#                                               <SKU>), confirming which
#                                               variant's trigger was hit
#   [data-autom="plOverlayContainer"]          the modal/overlay it opens
#   [data-autom="zipCode"]                     the pincode input inside it
#   [data-autom="continuePickUp"]              the submit button
# The last unknown is what the RESULT markup looks like once Continue is
# clicked — this endpoint's whole purpose is capturing that, via the
# overlay's full outerHTML, so a real checker function can be built
# against confirmed structure instead of another guess-and-verify round.
# ---------------------------------------------------------------------------
_PICKUP_DETAILS_SELECTOR = '[data-autom="pickUpDetails"]'
_PICKUP_TRIGGER_SELECTOR = '[data-autom^="productLocatorTriggerLink"]'
# 2026-07-28: pickUpDetails can ALSO render already "resolved" to a
# default/session store (e.g. "Pick up Today at Apple Saket"), with NO
# productLocatorTriggerLink at all — confirmed from a real captured dump.
# That state's clickable element has NO data-autom attribute; the only
# real, confirmed hook is data-ase-click="show" (its data-ase-overlay
# value, "buac-overlay", varies and isn't a reliable selector on its own).
# Clicking it reopens the SAME pincode-search overlay as the trigger above
# — confirmed live, not guessed.
_ALREADY_RESOLVED_TRIGGER_SELECTOR = f'{_PICKUP_DETAILS_SELECTOR} [data-ase-click="show"]'
_PICKUP_OVERLAY_SELECTOR = '[data-autom="plOverlayContainer"]'
_PICKUP_ZIPCODE_SELECTOR = '[data-autom="zipCode"]'
_PICKUP_CONTINUE_SELECTOR = '[data-autom="continuePickUp"]'


def _capture_pickup_flow(url: str, pincode: str) -> dict:
    """
    Full interaction: load `url`, click the "Check availability" trigger,
    wait for the overlay, fill `pincode` into its zipCode input, click
    Continue, wait for results, then return the overlay's full outerHTML
    plus the same keyword-filtered DOM scan _capture_dom_elements uses.

    Every step that can fail is captured into "diagnostics" rather than
    aborting the whole flow — a later step's failure still lets earlier
    steps' diagnostics (and whatever partial DOM state exists) come back,
    same "capture the failure mode, don't hide it" philosophy as every
    other function in this file. Steps after a failure are skipped (there's
    nothing meaningful left to click/fill once an earlier step didn't
    happen), but the function always returns a result, never raises for
    anything short of the whole page failing to load at all.
    """
    acquired = _check_semaphore.acquire(timeout=SLOT_WAIT_TIMEOUT_SECONDS)
    if not acquired:
        raise RuntimeError(
            f"too many concurrent checks (max {MAX_CONCURRENT_CHECKS}) — "
            f"timed out after {SLOT_WAIT_TIMEOUT_SECONDS}s waiting for a free slot"
        )
    try:
        with sync_playwright() as pw:
            browser, context = _new_browser_and_context(pw, user_agent=_apple_user_agent_for)
            try:
                page = context.new_page()

                diagnostics: dict = {
                    "goto_status": None,
                    "goto_error": None,
                    "pickup_details_wait_timed_out": None,
                    "trigger_data_autom": None,
                    "trigger_click_error": None,
                    "overlay_wait_timed_out": None,
                    "zip_fill_error": None,
                    "continue_click_error": None,
                    "results_wait_timed_out": None,
                    "extract_error": None,
                }

                try:
                    main_response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    if main_response is not None:
                        diagnostics["goto_status"] = main_response.status
                except Exception as exc:
                    diagnostics["goto_error"] = str(exc)
                    logger.error(f"[debug-pickup-flow] page.goto failed for {url}: {exc}")

                if diagnostics["goto_error"] is None:
                    try:
                        page.wait_for_load_state("networkidle", timeout=SIGNAL_WAIT_TIMEOUT_MS)
                    except Exception:
                        pass  # best-effort, same as every other endpoint here
                    try:
                        page.wait_for_selector('[data-autom="pickUpDetails"]', timeout=10000)
                        diagnostics["pickup_details_wait_timed_out"] = False
                    except PlaywrightTimeoutError:
                        diagnostics["pickup_details_wait_timed_out"] = True
                        logger.info(f"[debug-pickup-flow] pickUpDetails never appeared within 10s for {url}")
                    except Exception as exc:
                        diagnostics["pickup_details_wait_timed_out"] = True
                        logger.warning(f"[debug-pickup-flow] pickUpDetails wait raised for {url}: {exc}")
                    page.wait_for_timeout(3000)

                    # Step 1: click the "Check availability" trigger.
                    try:
                        trigger = page.locator(_PICKUP_TRIGGER_SELECTOR).first
                        trigger.wait_for(timeout=10000)
                        diagnostics["trigger_data_autom"] = trigger.get_attribute("data-autom")
                        trigger.click(timeout=5000)
                    except Exception as exc:
                        diagnostics["trigger_click_error"] = str(exc)
                        logger.error(f"[debug-pickup-flow] trigger click failed for {url}: {exc}")

                    # Step 2: wait for the overlay to appear.
                    if diagnostics["trigger_click_error"] is None:
                        try:
                            page.wait_for_selector(_PICKUP_OVERLAY_SELECTOR, timeout=10000)
                        except PlaywrightTimeoutError:
                            diagnostics["overlay_wait_timed_out"] = True
                            logger.info(f"[debug-pickup-flow] overlay never appeared within 10s for {url}")
                        except Exception as exc:
                            diagnostics["overlay_wait_timed_out"] = True
                            logger.warning(f"[debug-pickup-flow] overlay wait raised for {url}: {exc}")

                    # Step 3: fill the zipCode input.
                    if diagnostics["trigger_click_error"] is None:
                        try:
                            zip_input = page.locator(_PICKUP_ZIPCODE_SELECTOR).first
                            zip_input.wait_for(timeout=8000)
                            zip_input.fill(pincode)
                        except Exception as exc:
                            diagnostics["zip_fill_error"] = str(exc)
                            logger.error(f"[debug-pickup-flow] zipCode fill failed for {url}: {exc}")

                    # Step 4: click Continue.
                    if diagnostics["trigger_click_error"] is None and diagnostics["zip_fill_error"] is None:
                        try:
                            continue_btn = page.locator(_PICKUP_CONTINUE_SELECTOR).first
                            continue_btn.wait_for(timeout=5000)
                            continue_btn.click(timeout=5000)
                        except Exception as exc:
                            diagnostics["continue_click_error"] = str(exc)
                            logger.error(f"[debug-pickup-flow] continuePickUp click failed for {url}: {exc}")

                    # Step 5: wait for the results to load in the overlay.
                    try:
                        page.wait_for_load_state("networkidle", timeout=SIGNAL_WAIT_TIMEOUT_MS)
                    except PlaywrightTimeoutError:
                        diagnostics["results_wait_timed_out"] = True
                    except Exception:
                        pass
                    page.wait_for_timeout(4000)

                # Full overlay markup — the actual point of this endpoint.
                # Cap raised 8000->30000 (2026-07-28): a real Railway
                # capture hit exactly 8014 chars, cutting off right before
                # the results/store-list section — the whole reason this
                # endpoint exists. Debug-only; PRODUCTION
                # (_check_pickup_availability) doesn't scrape overlay
                # HTML/text at all anymore (2026-07-28 network-
                # interception rewrite — it parses the pickup-message-
                # recommendations/fulfillment-messages response body
                # directly), so this cap only ever affects this debug
                # endpoint's own output.
                overlay_html = None
                try:
                    overlay_html = page.evaluate(
                        """(selector) => {
                            const el = document.querySelector(selector);
                            if (!el) return null;
                            const html = el.outerHTML || '';
                            return html.length > 30000 ? html.slice(0, 30000) + '...(truncated)' : html;
                        }""",
                        _PICKUP_OVERLAY_SELECTOR,
                    )
                except Exception as exc:
                    diagnostics["extract_error"] = str(exc)
                    logger.error(f"[debug-pickup-flow] overlay extraction failed for {url}: {exc}")

                result = {
                    "page_title": None, "data_autom_elements": [],
                    "pincode_like_inputs": [], "pickup_related_buttons": [],
                    "pickup_details_html": [], "pickup_details_clickables": [],
                }
                try:
                    result = page.evaluate(_DOM_CAPTURE_JS)
                except Exception as exc:
                    diagnostics["extract_error"] = f"{diagnostics.get('extract_error') or ''}; dom_capture: {exc}".lstrip("; ")
                    logger.error(f"[debug-pickup-flow] DOM capture failed for {url}: {exc}")

                logger.info(f"[debug-pickup-flow] {url} pincode={pincode!r}: diagnostics={diagnostics}")
                return {**result, "overlay_html": overlay_html, "diagnostics": diagnostics}
            finally:
                browser.close()
    finally:
        _check_semaphore.release()


# ---------------------------------------------------------------------------
# /check-pickup-availability: PRODUCTION pincode-availability check via
# page-render, for Apple's fulfillment-messages API being unusable when
# replayed directly via httpx (see checkers/apple.py's module docstring —
# 0% success across every param/header/cookie combination tried). Scoped
# to iPhone product pages ONLY (the only tracked product category).
#
# ARCHITECTURE (2026-07-28, network-interception rewrite): earlier
# versions of this function scraped the pickup overlay's own rendered
# text, and separately chased why its "Continue" button never visually
# enabled even after correct, error-free pincode entry (see the git
# history on this function for that whole investigation — blur/timing/
# combobox theories, all ruled out with real diagnostic evidence). The
# actual breakthrough: typing a valid pincode makes the page's own JS
# fire pickup-message-recommendations (and fulfillment-messages) AS REAL
# BACKEND CALLS, successfully (200 OK) — carrying genuine in-page session
# context our own httpx replay can never reproduce — regardless of
# whether Continue ever becomes clickable. So this function no longer
# depends on Continue OR on scraping overlay text at all: it registers a
# network-response listener before typing, types the pincode (confirmed
# sufficient on its own to trigger both calls), and parses whichever
# response body arrives directly. pickup-message-recommendations is
# preferred (its shape was confirmed via real captured positive AND
# negative examples); fulfillment-messages is a fallback for if that
# specific endpoint doesn't fire, using the SAME shape/values already
# established and used by checkers/apple.py's own
# _evaluate_pickup_availability for years, not a fresh guess.
#
# ⚠️ REQUIRES headless=False (APPLE_PICKUP_HEADLESS, default off — see
# above). A controlled same-network A/B test confirmed headless Chromium
# fails to render pickUpDetails at all, while headful succeeds
# consistently. The Dockerfile starts a virtual display via start.sh
# (explicit Xvfb + readiness poll) so a headful launch has something to
# render into on Railway's display-less container — confirmed working via
# a real deploy (git_commit_sha-verified) as of the checkpoint diagnostics
# this rewrite is built on top of.
#
# ⚠️ available=False IS a deliberate departure from this codebase's usual
# "never assert False from a pickup-only signal" rule (see
# checkers/apple.py's _evaluate_pickup_availability and its own module
# docstring) — that rule exists because most Indian pincodes have ZERO
# nearby Apple Stores, so "no store shows availability" is normally just
# "no stores nearby," not "checked and out of stock." The distinction
# that makes False defensible HERE: pickup-message-recommendations
# already returns a list of SPECIFIC, NAMED candidate stores for this
# exact pincode. An EMPTY stores list still means "no stores near here at
# all" and stays None (same reasoning as always). But a NON-EMPTY list of
# real stores where none show our SKU as "available" is a materially
# stronger signal — real candidate stores were actually checked, not just
# absent. See _parse_pickup_stores below for exactly where that line is
# drawn.
# ---------------------------------------------------------------------------

_PICKUP_MESSAGE_RECOMMENDATIONS_URL_MARKER = "pickup-message-recommendations"
_FULFILLMENT_MESSAGES_URL_MARKER = "fulfillment-messages"

# Confirmed via real captured responses (2026-07-28):
#   POSITIVE (iPhone 16 Plus 128GB Pink, MXVW3HN/A, Apple Noida):
#     pickupDisplay "available", pickupSearchQuote "Available Tomorrow",
#     storePickupQuote "Tomorrow at Apple Noida"
#   NEGATIVE (iPhone 17 Black, no stock): pickupDisplay "ineligible"/
#     "unavailable"
_PICKUP_MESSAGE_RECOMMENDATIONS_AVAILABLE_VALUES = ("available",)
# fulfillment-messages' OWN confirmed shape/values — checkers/apple.py's
# _evaluate_pickup_availability, established independently and long
# before this rewrite. Kept separate rather than merged with the values
# above: these are two different endpoints, not guaranteed to agree on
# field values just because they're both Apple pickup APIs.
_FULFILLMENT_MESSAGES_AVAILABLE_VALUES = ("available", "eligible")


def _find_stores_list(node, _depth: int = 0):
    """
    Recursively searches a parsed JSON body for a list that structurally
    looks like a pickup "stores" array — every element a dict carrying a
    "partsAvailability" key — REGARDLESS of what key it's nested under.

    Fallback for _parse_pickup_stores (2026-07-28), added after a real
    fulfillment-messages 200 OK response body was captured whose
    pickupMessage did NOT contain a "stores" key at the expected path
    (parse_error: "missing key 'stores' in response body"), proving
    Apple's own response shape isn't fixed even for the same endpoint.
    Rather than guessing a specific alternate key name to hardcode as a
    second path (unconfirmed — no real captured example of the alternate
    shape exists yet), this matches on the STRUCTURE already confirmed
    from real captured data (a list of dicts, each carrying
    partsAvailability), so it works regardless of the surrounding key
    names. Depth-capped to avoid pathological recursion on adversarial/
    huge bodies.
    """
    if _depth > 8:
        return None
    if isinstance(node, list):
        if node and all(isinstance(item, dict) and "partsAvailability" in item for item in node):
            return node
        for item in node:
            found = _find_stores_list(item, _depth + 1)
            if found is not None:
                return found
    elif isinstance(node, dict):
        for value in node.values():
            found = _find_stores_list(value, _depth + 1)
            if found is not None:
                return found
    return None


def _parse_pickup_stores(
    body_text: str, path: list[str], sku: str, available_values: tuple[str, ...],
) -> tuple[bool | None, list[dict], str | None, dict | None]:
    """
    Parses one pickup-response JSON body (either endpoint — `path` says
    where its stores array lives) and classifies availability for `sku`.

    Returns (available, matching_stores, error, info):
      - available: True if `sku` shows one of `available_values` at ANY
        store; False if the stores list is NON-EMPTY, `sku` was found in
        at least one store's partsAvailability, but none show it as
        available (see the module note above on why this specific case
        is treated as a real negative, not the usual inconclusive None);
        None if the stores list is empty (no stores near this pincode at
        all — inconclusive, same reasoning this codebase always uses),
        `sku` isn't present in ANY store's partsAvailability (no data
        about OUR product specifically — also inconclusive, not "checked
        and unavailable"), or the body couldn't be parsed at all.
      - matching_stores: every store showing `sku` as available, as
        {"store_name", "pickup_search_quote", "store_pickup_quote"} —
        empty whenever available is not True.
      - error: a short string on structural/parse failure, else None.
      - info: {"store_count": int, "sku_found": bool, "sku_keys_seen":
        list[str]} whenever the body was successfully parsed down to a
        stores list (even an empty one) — added 2026-07-28 after a real
        run returned available=None/error=None/matching_stores=[] with
        no way to tell WHICH of two different "inconclusive" cases that
        was: zero stores near this pincode at all, vs. real stores
        present but this SKU absent from every one of their
        partsAvailability dicts (a possible SKU-formatting mismatch —
        wrong case, stray whitespace, wrong variant). store_count/
        sku_found distinguish the two directly; sku_keys_seen is every
        partsAvailability key actually observed across all stores, so a
        near-miss is immediately visible instead of requiring another
        guess-and-redeploy round. None only on a structural failure
        (JSON parse failure, or no stores list found anywhere at all) —
        there's no store data to describe in that case.

    If `path` doesn't resolve to a list (a different response shape than
    the one `path` was written for), falls back to _find_stores_list
    before giving up — see that function's docstring for why. If THAT
    also finds nothing, but navigation succeeded all the way to path's
    second-to-last key (i.e. only the final "stores" key itself was
    absent from an otherwise-valid parent dict), that's treated the same
    as an explicit "stores": [] — see the note further below (2026-07-28)
    for why this specific case is distinguished from an unrecognized
    shape rather than reported as a parse error.
    """
    try:
        data = json.loads(body_text)
    except Exception as exc:
        return None, [], f"JSON parse failed: {exc}", None

    node = data
    path_error = None
    failed_key_index = None
    for i, key in enumerate(path):
        if not isinstance(node, dict):
            path_error = f"expected a dict while navigating to {key!r}, got {type(node).__name__}"
            failed_key_index = i
            node = None
            break
        node = node.get(key)
        if node is None:
            path_error = f"missing key {key!r} in response body"
            failed_key_index = i
            break
    stores = node if isinstance(node, list) else None

    if stores is None:
        stores = _find_stores_list(data)
        if stores is not None:
            logger.info(
                f"[check-pickup-availability] _parse_pickup_stores: path {path} didn't resolve "
                f"({path_error}) but a stores-shaped list was found elsewhere in the body via "
                f"structural fallback — response shape differs from what `path` expected."
            )

    # 2026-07-28: a real run captured a 200 OK fulfillment-messages body
    # (top-level keys just ['body', 'head'] — a normal-looking envelope)
    # where navigation walked all the way through to the LAST path key
    # ("stores") before failing there specifically, and the structural
    # fallback above found no store-shaped list anywhere else in the body
    # either. The most defensible reading of that combination — parent
    # dict present and valid, only the terminal "stores" field itself
    # absent, and genuinely no store data anywhere — is that this
    # instance's API response simply omitted an empty "stores" field
    # rather than including it as "stores": [] (a common JSON convention:
    # omit empty/absent fields instead of spelling out emptiness). This
    # is NOT a guess at a new key name — it's confirmed by failed_key_index
    # itself pointing at the path's last position, meaning every dict
    # above it resolved exactly as expected.
    if stores is None and failed_key_index == len(path) - 1:
        stores = []

    if stores is None:
        top_level = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
        return None, [], f"{path_error}; body top-level keys: {top_level}", None

    if not stores:
        # no stores near this pincode — inconclusive
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
        if part_info.get("pickupDisplay", "") in available_values:
            matching_stores.append({
                "store_name": store.get("storeName") or "(unnamed store)",
                "pickup_search_quote": part_info.get("pickupSearchQuote"),
                "store_pickup_quote": part_info.get("storePickupQuote"),
            })

    info = {"store_count": len(stores), "sku_found": sku_found, "sku_keys_seen": sorted(sku_keys_seen)}

    if not sku_found:
        return None, [], None, info  # stores exist, but none carry data for OUR sku

    return (True if matching_stores else False), matching_stores, None, info


def _check_pickup_availability_once(url: str, pincode: str) -> dict:
    """
    ONE attempt — see _check_pickup_availability (below) for the retry
    loop wrapping this. Launches its own fresh browser/context/page each
    call, deliberately: a stale page/session is itself a plausible part
    of the intermittency this retry loop exists to work around, so each
    retry starts genuinely clean rather than reusing anything.

    Navigate, detect + click whichever pickUpDetails state is present
    (trigger_present or already_resolved — see
    _ALREADY_RESOLVED_TRIGGER_SELECTOR above), wait for the iPhone-
    specific overlay, then type `pincode` into zipCode via real
    per-character keystroke simulation (clear() then press_sequentially()
    per character — confirmed, via a real diagnosed run's own
    typing_trajectory capture, to land every character correctly AND be
    sufficient on its own to trigger Apple's backend pickup-availability
    calls; bulk press_sequentially(pincode) was never specifically
    confirmed to do the same, so this keeps the proven mechanism rather
    than reverting to something merely equivalent in theory). A
    network-response listener registered BEFORE page.goto() (even earlier
    than "before typing" — no risk of missing an early-firing call)
    captures whichever of pickup-message-recommendations /
    fulfillment-messages fires, and that response body — not Continue's
    click-ability, not scraped overlay text — is what gets parsed. See
    the module note above this function for the full architecture
    rationale and the available=False caveat.

    SKU is read from the trigger's own data-autom="productLocatorTriggerLink_
    <SKU>" attribute when available (diagnostics["sku_source"] =
    "trigger_data_autom") — confirmed more reliable than the generic
    page-content regex fallback (diagnostics["sku"] set once early,
    unconditionally, as a fallback for the already_resolved state which
    has no such attribute to read): a real run showed the regex grabbing
    a DIFFERENT sku than this same trigger's own attribute had shown for
    the IDENTICAL product URL on an earlier run, most likely because the
    page contains multiple "partNumber" occurrences (related/accessory
    items) and the regex takes whichever appears first in page source,
    not necessarily the one this specific widget is checking.

    diagnostics["all_responses_seen"] records every XHR/fetch URL+status
    observed regardless of whether it matched either strict marker above
    — added after a real run showed response_wait_timed_out=True on a
    URL that HAD produced a real 200 OK just minutes earlier via
    _diagnose_zipcode_validation's much broader keyword match, so a
    future recurrence is directly diagnosable instead of another guess.

    Every step failure is captured into `diagnostics` rather than raising
    — same "capture the failure mode, don't hide it" philosophy as every
    other function in this file.

    NOTE: does NOT acquire _check_semaphore itself — the retry wrapper
    (_check_pickup_availability, below) acquires it once for the whole
    attempt sequence, not once per attempt.
    """
    with sync_playwright() as pw:
        browser, context = _new_browser_and_context(
            pw, user_agent=_apple_user_agent_for, headless=APPLE_PICKUP_HEADLESS,
        )
        try:
            page = context.new_page()

            diagnostics: dict = {
                "goto_status": None,
                "goto_error": None,
                "pickup_details_wait_timed_out": None,
                "state": None,  # "trigger_present" | "already_resolved" | "neither"
                "click_error": None,
                "overlay_wait_timed_out": None,
                "zip_fill_error": None,
                "enter_key_error": None,
                "sku": None,
                "sku_source": None,  # "page_content_regex" | "trigger_data_autom"
                "sku_error": None,
                "pickup_message_response_seen": False,
                "fulfillment_messages_response_seen": False,
                "response_wait_timed_out": None,
                "used_endpoint": None,  # "pickup_message_recommendations" | "fulfillment_messages" | None
                "parse_error": None,
                # 2026-07-28: distinguishes the two different reasons
                # available can end up None with parse_error=None — see
                # _parse_pickup_stores' own docstring on `info`.
                "parse_info": None,
                # Fallback visibility (2026-07-28, added after a real
                # run showed response_wait_timed_out=True on a URL
                # that HAD produced a real pickup-message-
                # recommendations 200 OK just minutes earlier via
                # _diagnose_zipcode_validation's much broader keyword
                # match): every XHR/fetch URL+status seen, REGARDLESS
                # of whether it matched either strict marker above. If
                # this ever shows a URL that plausibly IS the target
                # endpoint but wasn't captured into captured_bodies,
                # that's direct proof the marker strings themselves
                # need adjusting — rather than guessing at that again.
                "all_responses_seen": [],
            }
            _MAX_ALL_RESPONSES_SEEN = 40
            available: bool | None = None
            matching_stores: list[dict] = []

            # Registered as early as possible (before goto even) —
            # stricter than "before typing" and catches everything,
            # with no risk of missing an early-firing call. Bodies
            # kept as raw text (not pre-parsed) so a body read
            # failure never crashes the listener itself.
            captured_bodies: dict[str, str | None] = {
                "pickup_message_recommendations": None,
                "fulfillment_messages": None,
            }

            def _on_response(response):
                url_lower = response.url.lower()
                if len(diagnostics["all_responses_seen"]) < _MAX_ALL_RESPONSES_SEEN:
                    diagnostics["all_responses_seen"].append({"url": response.url, "status": response.status})
                try:
                    if (
                        _PICKUP_MESSAGE_RECOMMENDATIONS_URL_MARKER in url_lower
                        and captured_bodies["pickup_message_recommendations"] is None
                    ):
                        captured_bodies["pickup_message_recommendations"] = response.text()
                    elif (
                        _FULFILLMENT_MESSAGES_URL_MARKER in url_lower
                        and "location=" in url_lower
                        and captured_bodies["fulfillment_messages"] is None
                    ):
                        captured_bodies["fulfillment_messages"] = response.text()
                except Exception as exc:
                    logger.error(f"[check-pickup-availability] response capture error on {response.url}: {exc}")

            page.on("response", _on_response)

            try:
                main_response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                if main_response is not None:
                    diagnostics["goto_status"] = main_response.status
            except Exception as exc:
                diagnostics["goto_error"] = str(exc)
                logger.error(f"[check-pickup-availability] page.goto failed for {url}: {exc}")

            if diagnostics["goto_error"] is None:
                try:
                    page.wait_for_load_state("networkidle", timeout=SIGNAL_WAIT_TIMEOUT_MS)
                except Exception:
                    pass

                try:
                    diagnostics["sku"] = _extract_apple_sku(page.content())
                    diagnostics["sku_source"] = "page_content_regex" if diagnostics["sku"] else None
                except Exception as exc:
                    diagnostics["sku_error"] = str(exc)

                try:
                    page.wait_for_selector(_PICKUP_DETAILS_SELECTOR, timeout=10000)
                    diagnostics["pickup_details_wait_timed_out"] = False
                except PlaywrightTimeoutError:
                    diagnostics["pickup_details_wait_timed_out"] = True
                except Exception:
                    diagnostics["pickup_details_wait_timed_out"] = True
                page.wait_for_timeout(3000)

                if diagnostics["pickup_details_wait_timed_out"] is False:
                    trigger_locator = page.locator(_PICKUP_TRIGGER_SELECTOR)
                    resolved_locator = page.locator(_ALREADY_RESOLVED_TRIGGER_SELECTOR)

                    click_target = None
                    if trigger_locator.count() > 0:
                        diagnostics["state"] = "trigger_present"
                        click_target = trigger_locator.first
                        # productLocatorTriggerLink_<SKU> — confirmed
                        # (2026-07-28) more reliable than the generic
                        # page-content regex above: a real run showed
                        # the regex grabbing a DIFFERENT SKU
                        # (MG6Q4HN/A) than this exact same trigger's
                        # own attribute had shown on an earlier run
                        # (MG6J4HN/A) for the identical product URL —
                        # the page likely has multiple "partNumber"
                        # occurrences (related/accessory items), and
                        # the regex takes whichever appears first, not
                        # necessarily the one this specific widget is
                        # actually checking. This attribute is tied
                        # directly to the trigger we're about to
                        # click, so it takes priority when available.
                        try:
                            trigger_data_autom = click_target.get_attribute("data-autom")
                            if trigger_data_autom and trigger_data_autom.startswith("productLocatorTriggerLink_"):
                                trigger_sku = trigger_data_autom[len("productLocatorTriggerLink_"):]
                                if trigger_sku:
                                    diagnostics["sku"] = trigger_sku
                                    diagnostics["sku_source"] = "trigger_data_autom"
                        except Exception:
                            pass  # falls back to the page-content regex result already set above
                    elif resolved_locator.count() > 0:
                        diagnostics["state"] = "already_resolved"
                        click_target = resolved_locator.first
                    else:
                        diagnostics["state"] = "neither"

                    if click_target is not None:
                        try:
                            click_target.click(timeout=5000)
                        except Exception as exc:
                            diagnostics["click_error"] = str(exc)
                            logger.error(f"[check-pickup-availability] trigger click failed for {url}: {exc}")

                        if diagnostics["click_error"] is None:
                            try:
                                page.wait_for_selector(_PICKUP_OVERLAY_SELECTOR, timeout=10000)
                            except PlaywrightTimeoutError:
                                diagnostics["overlay_wait_timed_out"] = True
                            except Exception:
                                diagnostics["overlay_wait_timed_out"] = True

                            if not diagnostics["overlay_wait_timed_out"]:
                                try:
                                    zip_input = page.locator(_PICKUP_ZIPCODE_SELECTOR).first
                                    zip_input.wait_for(timeout=8000)
                                    zip_input.clear()
                                    for ch in pincode:
                                        zip_input.press_sequentially(ch, delay=50)
                                except Exception as exc:
                                    diagnostics["zip_fill_error"] = str(exc)
                                    logger.error(f"[check-pickup-availability] zipCode fill failed for {url}: {exc}")

                                if diagnostics["zip_fill_error"] is None:
                                    # Enter keypress (2026-07-28,
                                    # added after a real intermittent
                                    # non-firing case): the ONE
                                    # confirmed-successful diagnostic
                                    # run also pressed Enter shortly
                                    # after typing (its checkpoint F) —
                                    # "typing alone triggers it" was
                                    # inferred from that run's overall
                                    # success, never actually isolated
                                    # from whether Enter played a
                                    # part. Best-effort; a failure here
                                    # doesn't block the poll below.
                                    try:
                                        page.keyboard.press("Enter")
                                    except Exception as exc:
                                        diagnostics["enter_key_error"] = str(exc)

                                    # Poll for either response to
                                    # arrive rather than a fixed
                                    # dwell — Continue's own state is
                                    # irrelevant now, so there's
                                    # nothing else to wait on. Deadline
                                    # raised 8000->15000ms (2026-07-28)
                                    # — the one confirmed-successful
                                    # diagnostic run stayed open for
                                    # 7+ seconds AFTER typing across
                                    # its own checkpoint dwells before
                                    # giving up, longer than this
                                    # function's old 8s total; a
                                    # shorter window here couldn't
                                    # actually rule out "just needs
                                    # more time" as part of the
                                    # explanation for intermittent
                                    # non-firing.
                                    waited_ms = 0
                                    step_ms = 250
                                    deadline_ms = 15000
                                    while (
                                        waited_ms < deadline_ms
                                        and captured_bodies["pickup_message_recommendations"] is None
                                        and captured_bodies["fulfillment_messages"] is None
                                    ):
                                        page.wait_for_timeout(step_ms)
                                        waited_ms += step_ms

                            diagnostics["pickup_message_response_seen"] = (
                                captured_bodies["pickup_message_recommendations"] is not None
                            )
                            diagnostics["fulfillment_messages_response_seen"] = (
                                captured_bodies["fulfillment_messages"] is not None
                            )
                            if not diagnostics["pickup_message_response_seen"] and not diagnostics["fulfillment_messages_response_seen"]:
                                diagnostics["response_wait_timed_out"] = True

            if diagnostics["sku"] is None:
                diagnostics["parse_error"] = diagnostics["sku_error"] or "SKU could not be extracted from the page"
            elif captured_bodies["pickup_message_recommendations"] is not None:
                diagnostics["used_endpoint"] = "pickup_message_recommendations"
                available, matching_stores, diagnostics["parse_error"], diagnostics["parse_info"] = _parse_pickup_stores(
                    captured_bodies["pickup_message_recommendations"],
                    ["body", "PickupMessage", "stores"],
                    diagnostics["sku"],
                    _PICKUP_MESSAGE_RECOMMENDATIONS_AVAILABLE_VALUES,
                )
            elif captured_bodies["fulfillment_messages"] is not None:
                diagnostics["used_endpoint"] = "fulfillment_messages"
                available, matching_stores, diagnostics["parse_error"], diagnostics["parse_info"] = _parse_pickup_stores(
                    captured_bodies["fulfillment_messages"],
                    ["body", "content", "pickupMessage", "stores"],
                    diagnostics["sku"],
                    _FULFILLMENT_MESSAGES_AVAILABLE_VALUES,
                )

            logger.info(
                f"[check-pickup-availability] {url} pincode={pincode!r}: available={available} "
                f"used_endpoint={diagnostics['used_endpoint']} matching_stores={len(matching_stores)} "
                f"state={diagnostics['state']} diagnostics={diagnostics}"
            )
            return {
                "available": available,
                "matching_stores": matching_stores,
                "diagnostics": diagnostics,
                "git_commit_sha": GIT_COMMIT_SHA,
            }
        finally:
            browser.close()


def _check_pickup_availability(url: str, pincode: str) -> dict:
    """
    Retry wrapper around _check_pickup_availability_once (2026-07-28,
    added as a pragmatic safety net for genuinely inconsistent Apple-side
    behavior confirmed across multiple real runs — sometimes pickUpDetails
    renders slowly [pickup_details_wait_timed_out], sometimes the pincode
    lookup's backend call takes variable time or never fires in the
    allotted window [response_wait_timed_out], sometimes a response body
    arrives in a shape _parse_pickup_stores doesn't expect [parse_error].
    Each failure mode individually confirmed via diagnostics on separate
    real runs, not assumed — chasing every one as its own timing edge
    case stopped being productive, so this retries the WHOLE check
    (fresh browser + fresh navigation per attempt, not just the in-page
    type/wait step — a stale page/session is itself a plausible part of
    the intermittency) up to APPLE_PICKUP_MAX_ATTEMPTS times.

    Modeled on bot.py's run_apple_cookie_refresh_cycle
    (APPLE_COOKIE_REFRESH_MAX_ATTEMPTS / _RETRY_DELAY_*): stops early the
    moment one attempt returns a CONCLUSIVE result (available is True or
    False — both real signals in this endpoint's contract, see the
    module note above _parse_pickup_stores on why False is meaningful
    here specifically). If every attempt stays inconclusive (available
    stays None), the LAST attempt's full result is returned as the floor
    — same "genuinely tried to do better first, but never worse than a
    single attempt" behavior as the cookie-refresh precedent.

    Acquires _check_semaphore ONCE for the whole attempt sequence (not
    once per attempt) — this is one logical caller-facing check, and
    holding the slot across retries avoids re-queuing behind unrelated
    concurrent checks between attempts.

    diagnostics["attempt"] / diagnostics["max_attempts"] are added to
    the returned result (on top of whatever _check_pickup_availability_
    once already set) so a result is self-describing about how many
    attempts it took — same "make it directly diagnosable" motivation as
    all_responses_seen and git_commit_sha above.
    """
    acquired = _check_semaphore.acquire(timeout=SLOT_WAIT_TIMEOUT_SECONDS)
    if not acquired:
        raise RuntimeError(
            f"too many concurrent checks (max {MAX_CONCURRENT_CHECKS}) — "
            f"timed out after {SLOT_WAIT_TIMEOUT_SECONDS}s waiting for a free slot"
        )
    try:
        result: dict | None = None
        for attempt in range(1, APPLE_PICKUP_MAX_ATTEMPTS + 1):
            result = _check_pickup_availability_once(url, pincode)
            result["diagnostics"]["attempt"] = attempt
            result["diagnostics"]["max_attempts"] = APPLE_PICKUP_MAX_ATTEMPTS

            if result["available"] is not None:
                logger.info(
                    f"[check-pickup-availability] {url} pincode={pincode!r}: attempt {attempt}/"
                    f"{APPLE_PICKUP_MAX_ATTEMPTS} conclusive (available={result['available']}) — "
                    f"no further attempts needed."
                )
                return result

            logger.info(
                f"[check-pickup-availability] {url} pincode={pincode!r}: attempt {attempt}/"
                f"{APPLE_PICKUP_MAX_ATTEMPTS} inconclusive (available=None)"
            )
            if attempt < APPLE_PICKUP_MAX_ATTEMPTS:
                time.sleep(APPLE_PICKUP_RETRY_DELAY_SECONDS)

        logger.warning(
            f"[check-pickup-availability] {url} pincode={pincode!r}: all {APPLE_PICKUP_MAX_ATTEMPTS} "
            f"attempts stayed inconclusive — returning the last attempt's result as the floor."
        )
        return result
    finally:
        _check_semaphore.release()


# ---------------------------------------------------------------------------
# /debug-zipcode-validation: evidence-gathering diagnostic for WHY
# Continue stays disabled after typing pincode into zipCode
# (_check_pickup_availability's remaining open bug, 2026-07-28) — the
# keystroke-simulation fix (press_sequentially instead of fill()) did NOT
# resolve it, confirmed via a real deploy (git_commit_sha-verified). A
# per-keystroke trajectory capture then RULED OUT the typing-truncation
# theory too: a real run showed all 6 characters landing correctly
# ("110017" exact), focus staying on the field throughout, and NO visible
# error text — yet Continue stayed disabled across every blur/timing
# checkpoint. This tests, in order, stopping at the first checkpoint
# where Continue actually becomes enabled:
#   A. immediately after typing (reproduces the confirmed-still-broken
#      production behavior)
#   F. a real Enter keypress — NOT yet tested before 2026-07-28 round 2;
#      the standard way to CONFIRM a typed value in a combobox/
#      autocomplete-style field, distinct from blur/tab. Tested right
#      after A since it's the most natural next action a real user takes
#      after finishing typing.
#   B. after an extra 500ms dwell with no further interaction (tests
#      "debounced validation, no blur needed")
#   E. after a much longer 5s dwell, STILL no blur (isolates "just needs
#      more time" — e.g. a slow async validation call — from "needs a
#      blur/focus-change event specifically", which B's 500ms alone
#      can't rule out)
#   C. after an explicit programmatic .blur() on the zipCode input
#      (tests "needs blur specifically")
#   D. after a REAL keyboard Tab press away from the field — tests
#      whether React's synthetic blur handling differs for a genuine
#      user-driven focus change vs. a programmatic .blur() call. NOT a
#      positional click (an earlier version clicked near the overlay's
#      top-left corner, which very likely landed on the backdrop's
#      click-to-dismiss zone and closed the whole overlay instead of
#      blurring the field — a bug in the diagnostic itself, not a
#      discovery about Apple's page; Tab has no coordinates to get wrong)
# A network-response listener is also registered before typing starts,
# capturing every XHR/fetch response seen from that point on (method,
# URL, status — no keyword filter, since the whole point is not having
# to guess an endpoint name in advance) — tests whether Continue's
# enablement is gated on an async backend call rather than purely local
# field validation.
#
# Every checkpoint's full state is captured (not just enabled/disabled),
# plus STATIC evidence independent of the sequence:
#   - zipCode's own HTML validation attributes (pattern/maxlength/
#     minlength/inputmode/type — rules a format mismatch in vs out)
#   - React's own attached event-handler prop names on the zipCode
#     element (via its internal __reactProps*/__reactEventHandlers* key —
#     confirms which synthetic events the page actually listens for)
#   - combobox/autocomplete evidence (2026-07-28 round 2, after correct,
#     error-free typing STILL left Continue disabled): zipCode's own
#     role/aria-autocomplete/aria-expanded/aria-activedescendant/
#     aria-controls attributes, plus whether any [role="listbox"]/
#     [role="option"] elements exist anywhere on the page — tests
#     whether this is actually a combobox requiring a SUGGESTION to be
#     selected, not free text accepted as typed
#   - a full inventory of every input/select/button inside the overlay
#     (tag, type, data-autom, name, value/checked, disabled) — rules out
#     "something OTHER than zipCode also gates Continue" without having
#     to guess what to look for
# ---------------------------------------------------------------------------

_ZIPCODE_VALIDATION_STATE_JS = """(continueSelector) => {
    const continueBtn = document.querySelector(continueSelector);
    const zipInput = document.querySelector('[data-autom="zipCode"]');

    function reactHandlerKeys(el) {
        if (!el) return [];
        const key = Object.keys(el).find(
            (k) => k.startsWith('__reactProps') || k.startsWith('__reactEventHandlers')
        );
        if (!key) return [];
        const props = el[key];
        if (!props) return [];
        return Object.keys(props).filter((k) => k.toLowerCase().startsWith('on'));
    }

    let nearbyErrorText = null;
    if (zipInput) {
        const container = zipInput.closest('form') || zipInput.parentElement;
        if (container) {
            const candidates = container.querySelectorAll('[class*="error" i], [class*="invalid" i], [role="alert"]');
            for (const c of candidates) {
                const t = (c.innerText || '').trim();
                if (t) { nearbyErrorText = t; break; }
            }
        }
    }

    // Autocomplete/combobox pattern check (2026-07-28): a real run
    // showed correctly-typed, validly-formatted, error-free input STILL
    // leaves Continue disabled — one plausible explanation is the field
    // is actually a combobox requiring a SUGGESTION to be selected, not
    // free text accepted as-is. aria-autocomplete/aria-expanded/role on
    // the input itself, plus any [role="listbox"]/[role="option"]
    // elements anywhere on the page, would confirm or rule this out.
    const listboxEl = document.querySelector('[role="listbox"]');
    const optionEls = listboxEl ? listboxEl.querySelectorAll('[role="option"]') : [];

    // Full form-control inventory (2026-07-28): rules out "something
    // OTHER than zipCode also gates Continue" (a checkbox, a second
    // field, a store-selection step) without having to guess what to
    // look for.
    const overlay = document.querySelector('[data-autom="plOverlayContainer"]');
    const formElements = [];
    if (overlay) {
        overlay.querySelectorAll('input, select, button').forEach((el) => {
            if (formElements.length >= 20) return;
            formElements.push({
                tag: el.tagName,
                type: el.getAttribute('type'),
                data_autom: el.getAttribute('data-autom'),
                name: el.getAttribute('name'),
                value: el.tagName === 'SELECT' ? el.value : (el.value !== undefined ? el.value : null),
                checked: el.checked !== undefined ? el.checked : null,
                disabled: el.disabled,
            });
        });
    }

    return {
        continue_disabled_attr: continueBtn ? continueBtn.disabled : null,
        continue_aria_disabled: continueBtn ? continueBtn.getAttribute('aria-disabled') : null,
        continue_class: continueBtn ? continueBtn.className : null,
        zip_value: zipInput ? zipInput.value : null,
        zip_is_active_element: document.activeElement === zipInput,
        zip_pattern: zipInput ? zipInput.getAttribute('pattern') : null,
        zip_maxlength: zipInput ? zipInput.getAttribute('maxlength') : null,
        zip_minlength: zipInput ? zipInput.getAttribute('minlength') : null,
        zip_inputmode: zipInput ? zipInput.getAttribute('inputmode') : null,
        zip_type: zipInput ? zipInput.getAttribute('type') : null,
        zip_role: zipInput ? zipInput.getAttribute('role') : null,
        zip_aria_autocomplete: zipInput ? zipInput.getAttribute('aria-autocomplete') : null,
        zip_aria_expanded: zipInput ? zipInput.getAttribute('aria-expanded') : null,
        zip_aria_activedescendant: zipInput ? zipInput.getAttribute('aria-activedescendant') : null,
        zip_aria_controls: zipInput ? zipInput.getAttribute('aria-controls') : null,
        zip_react_handler_keys: reactHandlerKeys(zipInput),
        nearby_error_text: nearbyErrorText,
        listbox_present: !!listboxEl,
        listbox_option_count: optionEls.length,
        listbox_option_texts: Array.from(optionEls).slice(0, 10).map((o) => (o.innerText || '').trim()),
        overlay_form_elements: formElements,
    };
}"""


def _diagnose_zipcode_validation(url: str, pincode: str) -> dict:
    """
    Reproduces _check_pickup_availability's flow exactly up through
    typing `pincode` (same navigate -> click trigger/resolved-state ->
    wait overlay -> clear), then types it ONE CHARACTER AT A TIME
    (instead of a single bulk press_sequentially(pincode) call),
    capturing the field's value + focus state after EVERY keystroke —
    added after a real run showed the final value stuck at "1" (only the
    first character), to see exactly where that happens: growing then
    reset, or focus lost after the first character so nothing after it
    registers. Then runs a sequence of checkpoints to find WHICH
    intervention (if any) actually enables Continue — see the module
    note above for what each checkpoint tests.

    Returns {"checkpoints": {name: state_dict, ...}, "enabled_at": name
    or None, "typing_trajectory": [{"char", "value_after",
    "zip_focused_after"}, ...], "network_requests": [...],
    "network_response_bodies": [...], "diagnostics": {...}} —
    "enabled_at" is the first checkpoint name where continue_disabled_attr
    was False, or None if it never became enabled at any checkpoint
    tried. Every checkpoint that WAS reached is included in
    "checkpoints", even after "enabled_at" is found, up to (and
    including) the one that succeeded — later checkpoints are skipped
    once one succeeds, since there's nothing further to learn by
    continuing to poke at an already-fixed state. "network_requests" is
    every XHR/fetch response's url/method/status seen from just before
    typing onward, regardless of outcome. "network_response_bodies" is
    the FULL body (capped at _MAX_BODY_CHARS) for the subset of those
    matching _NETWORK_CAPTURE_KEYWORDS — the same keyword list
    /debug-network already uses, not a separate guess — added 2026-07-28
    after a real run showed fulfillment-messages AND pickup-message-
    recommendations both firing successfully (200 OK) after typing, from
    the page's own JS, independent of whether Continue ever enables.

    Never raises; a failure at any step is recorded in "diagnostics" and
    whatever checkpoints were reached are still returned.
    """
    acquired = _check_semaphore.acquire(timeout=SLOT_WAIT_TIMEOUT_SECONDS)
    if not acquired:
        raise RuntimeError(
            f"too many concurrent checks (max {MAX_CONCURRENT_CHECKS}) — "
            f"timed out after {SLOT_WAIT_TIMEOUT_SECONDS}s waiting for a free slot"
        )
    try:
        with sync_playwright() as pw:
            browser, context = _new_browser_and_context(
                pw, user_agent=_apple_user_agent_for, headless=APPLE_PICKUP_HEADLESS,
            )
            try:
                page = context.new_page()
                diagnostics: dict = {
                    "goto_error": None,
                    "pickup_details_wait_timed_out": None,
                    "state": None,
                    "click_error": None,
                    "overlay_wait_timed_out": None,
                    "zip_fill_error": None,
                }
                checkpoints: dict = {}
                enabled_at: str | None = None

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                except Exception as exc:
                    diagnostics["goto_error"] = str(exc)
                    return {"checkpoints": checkpoints, "enabled_at": None, "diagnostics": diagnostics}

                try:
                    page.wait_for_load_state("networkidle", timeout=SIGNAL_WAIT_TIMEOUT_MS)
                except Exception:
                    pass
                try:
                    page.wait_for_selector(_PICKUP_DETAILS_SELECTOR, timeout=10000)
                    diagnostics["pickup_details_wait_timed_out"] = False
                except Exception:
                    diagnostics["pickup_details_wait_timed_out"] = True
                    return {"checkpoints": checkpoints, "enabled_at": None, "diagnostics": diagnostics}
                page.wait_for_timeout(3000)

                trigger_locator = page.locator(_PICKUP_TRIGGER_SELECTOR)
                resolved_locator = page.locator(_ALREADY_RESOLVED_TRIGGER_SELECTOR)
                click_target = None
                if trigger_locator.count() > 0:
                    diagnostics["state"] = "trigger_present"
                    click_target = trigger_locator.first
                elif resolved_locator.count() > 0:
                    diagnostics["state"] = "already_resolved"
                    click_target = resolved_locator.first
                else:
                    diagnostics["state"] = "neither"
                    return {"checkpoints": checkpoints, "enabled_at": None, "diagnostics": diagnostics}

                try:
                    click_target.click(timeout=5000)
                except Exception as exc:
                    diagnostics["click_error"] = str(exc)
                    return {"checkpoints": checkpoints, "enabled_at": None, "diagnostics": diagnostics}

                try:
                    page.wait_for_selector(_PICKUP_OVERLAY_SELECTOR, timeout=10000)
                except Exception:
                    diagnostics["overlay_wait_timed_out"] = True
                    return {"checkpoints": checkpoints, "enabled_at": None, "diagnostics": diagnostics}

                # Network capture, registered BEFORE typing so it catches
                # anything fired by the type itself, not just afterward —
                # tests whether Continue's enablement is gated on an async
                # backend call (e.g. a store-availability lookup for this
                # pincode) rather than purely local field validation. Every
                # XHR/fetch response is recorded (not filtered by keyword —
                # the whole point is not having to guess the endpoint name
                # in advance), capped so one noisy page can't blow up the
                # response.
                network_requests: list[dict] = []
                network_response_bodies: list[dict] = []
                _MAX_NETWORK_REQUESTS = 40

                def _on_response(response):
                    try:
                        resource_type = response.request.resource_type
                    except Exception:
                        resource_type = "?"
                    if resource_type not in ("xhr", "fetch"):
                        return
                    if len(network_requests) < _MAX_NETWORK_REQUESTS:
                        network_requests.append({
                            "url": response.url,
                            "method": response.request.method,
                            "status": response.status,
                        })
                    # Full body ONLY for keyword-matching responses (2026-07-28,
                    # after a real run showed fulfillment-messages AND
                    # pickup-message-recommendations both firing successfully
                    # from the page's own JS after typing, with 200 OK — the
                    # actual availability data this whole checker exists to
                    # find, independent of whether Continue ever enables).
                    # _NETWORK_CAPTURE_KEYWORDS is the SAME list /debug-network
                    # already uses, not a separate guess.
                    url_lower = response.url.lower()
                    if not any(kw in url_lower for kw in _NETWORK_CAPTURE_KEYWORDS):
                        return
                    try:
                        body = response.text()
                    except Exception as exc:
                        body = f"<could not read response body: {exc}>"
                    if body and len(body) > _MAX_BODY_CHARS:
                        body = body[:_MAX_BODY_CHARS] + f"...(truncated, {len(body)} chars total)"
                    network_response_bodies.append({
                        "url": response.url,
                        "method": response.request.method,
                        "status": response.status,
                        "body": body,
                    })

                page.on("response", _on_response)

                # Typed ONE character at a time (not a single bulk
                # press_sequentially(pincode) call) so the value + focus
                # state can be captured after EVERY keystroke — a real run
                # showed the final value stuck at "1" (only the first
                # character), and this is the fastest way to see exactly
                # WHERE that happens: does the value grow correctly then
                # get reset, or does typing just stop registering after
                # character 1 (e.g. because focus moved off the element —
                # CDP dispatches keystrokes to whatever has OS-level
                # focus, not a specific DOM node, so if React replaces the
                # input on its first re-render, every keystroke after
                # that would go nowhere).
                typing_trajectory: list[dict] = []
                try:
                    zip_input = page.locator(_PICKUP_ZIPCODE_SELECTOR).first
                    zip_input.wait_for(timeout=8000)
                    zip_input.clear()
                    for ch in pincode:
                        step: dict = {"char": ch}
                        try:
                            zip_input.press_sequentially(ch, delay=50)
                        except Exception as exc:
                            step["press_error"] = str(exc)
                            typing_trajectory.append(step)
                            continue
                        try:
                            step["value_after"] = zip_input.input_value()
                        except Exception as exc:
                            step["value_after_error"] = str(exc)
                        try:
                            step["zip_focused_after"] = page.evaluate(
                                "(sel) => document.activeElement === document.querySelector(sel)",
                                _PICKUP_ZIPCODE_SELECTOR,
                            )
                        except Exception as exc:
                            step["zip_focused_after_error"] = str(exc)
                        typing_trajectory.append(step)
                except Exception as exc:
                    diagnostics["zip_fill_error"] = str(exc)
                    return {
                        "checkpoints": checkpoints, "enabled_at": None,
                        "typing_trajectory": typing_trajectory, "diagnostics": diagnostics,
                    }

                def _capture(name: str) -> dict:
                    try:
                        state = page.evaluate(_ZIPCODE_VALIDATION_STATE_JS, _PICKUP_CONTINUE_SELECTOR)
                    except Exception as exc:
                        state = {"capture_error": str(exc)}
                    checkpoints[name] = state
                    return state

                # Checkpoint A: immediately after typing.
                state = _capture("A_immediately_after_typing")
                if state.get("continue_disabled_attr") is False:
                    enabled_at = "A_immediately_after_typing"

                # Checkpoint F: Enter keypress — a real run showed
                # correctly-typed, error-free, format-valid input still
                # leaves Continue disabled across every blur/timing
                # checkpoint tried. Enter is the standard way to CONFIRM a
                # typed value in a combobox/autocomplete-style field
                # (distinct from blur/tab, and not yet tested at all) —
                # tested right after A since it's the most natural next
                # action a real user takes after finishing typing.
                if enabled_at is None:
                    try:
                        page.keyboard.press("Enter")
                    except Exception:
                        pass
                    page.wait_for_timeout(500)
                    state = _capture("F_after_enter_keypress")
                    if state.get("continue_disabled_attr") is False:
                        enabled_at = "F_after_enter_keypress"

                # Checkpoint B: extra dwell, no blur — tests debounce alone.
                if enabled_at is None:
                    page.wait_for_timeout(500)
                    state = _capture("B_after_500ms_dwell_no_blur")
                    if state.get("continue_disabled_attr") is False:
                        enabled_at = "B_after_500ms_dwell_no_blur"

                # Checkpoint E: a MUCH longer dwell, still no blur — isolates
                # "just needs more time" (e.g. a slow async validation call)
                # from "specifically needs a blur/focus-change event", which
                # B's 500ms alone can't rule out.
                if enabled_at is None:
                    page.wait_for_timeout(5000)
                    state = _capture("E_after_5s_extended_dwell_no_blur")
                    if state.get("continue_disabled_attr") is False:
                        enabled_at = "E_after_5s_extended_dwell_no_blur"

                # Checkpoint C: programmatic .blur() — tests "needs blur".
                if enabled_at is None:
                    try:
                        zip_input.evaluate("el => el.blur()")
                    except Exception:
                        pass
                    page.wait_for_timeout(500)
                    state = _capture("C_after_programmatic_blur")
                    if state.get("continue_disabled_attr") is False:
                        enabled_at = "C_after_programmatic_blur"

                # Checkpoint D: a REAL keyboard-driven Tab away from the
                # field — tests whether React's synthetic blur handling
                # differs for a genuine user-driven focus change vs. a
                # programmatic .blur() call. Deliberately NOT a positional
                # click on the overlay (an earlier version clicked near the
                # overlay's top-left corner, which very likely landed on
                # the backdrop's click-to-dismiss zone and closed the whole
                # overlay instead of just blurring the field — a bug in
                # this diagnostic, not a discovery about Apple's page).
                # Tab is a pure keyboard action with no coordinates to get
                # wrong, and is itself completely ordinary real-user
                # behavior (type, then Tab to the next field/button).
                if enabled_at is None:
                    try:
                        page.keyboard.press("Tab")
                    except Exception:
                        pass
                    page.wait_for_timeout(500)
                    state = _capture("D_after_tab_key_blur")
                    if state.get("continue_disabled_attr") is False:
                        enabled_at = "D_after_tab_key_blur"

                logger.info(
                    f"[debug-zipcode-validation] {url} pincode={pincode!r}: "
                    f"enabled_at={enabled_at} typing_trajectory={typing_trajectory} "
                    f"checkpoints={checkpoints} network_requests={network_requests} "
                    f"network_response_bodies_count={len(network_response_bodies)} diagnostics={diagnostics}"
                )
                return {
                    "checkpoints": checkpoints,
                    "enabled_at": enabled_at,
                    "typing_trajectory": typing_trajectory,
                    "network_requests": network_requests,
                    "network_response_bodies": network_response_bodies,
                    "diagnostics": diagnostics,
                    "git_commit_sha": GIT_COMMIT_SHA,
                }
            finally:
                browser.close()
    finally:
        _check_semaphore.release()


# ---------------------------------------------------------------------------
# /refresh-apple-cookies: launch a real headless-Chromium session, load an
# Apple product page, and trigger the SAME fulfillment-messages request a
# real visitor's own pickup-availability check performs — from INSIDE the
# page's own JS context via page.evaluate(fetch(...)), not a separate
# out-of-band request. Because it runs same-origin from a page that's
# already loaded and passed apple.com's own anti-bot checks, it carries the
# real browser's TLS fingerprint, headers, and cookies exactly the way
# Apple's own frontend JS would — this is the whole point of doing cookie
# acquisition via Playwright instead of the main bot's plain httpx calls
# (httpx's TLS fingerprint doesn't match a real browser's, which is part of
# why a manually-pasted cookie/UA pair was found dying after ~2 hours; see
# the main bot's checkers/apple.py and config.py for that history).
#
# Deliberately does NOT try to click through Apple's pickup-availability UI
# widget (a "See availability" link + pincode input + submit button) — that
# DOM structure isn't something this sandbox can verify live, and a
# selector guess would be fragile. Instead it reproduces exactly what that
# UI would do once submitted: call fulfillment-messages with the page's own
# SKU and the given pincode. Same endpoint, same query params
# (checkers/apple.py's _build_fulfillment_target), same authentication
# (page-context fetch = real browser Cookie/UA), without depending on
# unconfirmed CSS selectors.
# ---------------------------------------------------------------------------

_APPLE_SKU_PATTERN = re.compile(r'"partNumber"\s*:\s*"([A-Z0-9]{5,14}/[A-Z]{1,3})"')


def _extract_apple_sku(html: str) -> str | None:
    m = _APPLE_SKU_PATTERN.search(html)
    return m.group(1) if m else None


def _fulfillment_messages_url(product_url: str, sku: str, pincode: str) -> str | None:
    """Builds the same fulfillment-messages URL checkers/apple.py's
    _build_fulfillment_target does, deriving the locale-prefixed /shop/ path
    from the product URL itself (".../in/shop/buy-iphone/..." -> "/in/shop/
    fulfillment-messages") rather than hardcoding "/in/", so this works for
    any Apple storefront locale. Returns None if the product URL doesn't
    look like an apple.com /shop/ page at all.

    Param set verified 2026-07-27 against a live Chrome DevTools capture of
    a working request — see checkers/apple.py's _build_fulfillment_target
    for the full story: `little`/`mts.1`/`fts` are gone from Apple's
    current frontend request, replaced by `pl=true`. Keeping this in sync
    with that function matters here specifically — this URL is what
    pincode_check_confirmed's own validation probe below fetches, so a
    stale param set here would keep reporting "unconfirmed" even after the
    real checker in checkers/apple.py is fixed."""
    parsed = urlparse(product_url)
    segments = [s for s in parsed.path.split("/") if s]
    if "shop" not in segments:
        return None
    shop_idx = segments.index("shop")
    locale_prefix = "/".join(segments[:shop_idx])
    path = f"/{locale_prefix}/shop/fulfillment-messages" if locale_prefix else "/shop/fulfillment-messages"
    params = {
        "fae": "true", "pl": "true", "mts.0": "regular",
        "parts.0": sku, "location": pincode,
    }
    return f"{parsed.scheme}://{parsed.netloc}{path}?{urlencode(params)}"


_FULFILLMENT_FETCH_JS = """async (target) => {
    const resp = await fetch(target, {
        credentials: 'include',
        headers: { 'Accept': '*/*', 'x-skip-redirect': 'true' },
    });
    const text = await resp.text();
    return { status: resp.status, body: text.slice(0, 2000) };
}"""


def _abck_diagnostics(cookies_list: list[dict]) -> dict:
    """
    2026-07-27 — _abck's raw VALUE (not just its name/presence, unlike
    every other cookie in the jar — see _refresh_apple_cookies' own
    cookie_names diagnostic) is surfaced here specifically. Safe to:
    _abck is Akamai Bot Manager's own anti-bot sensor/telemetry marker,
    not an account/session credential — it grants no access to anything
    on apple.com by itself, unlike the real session cookies (dssid2,
    as_atb, mbox, etc.) that make up the rest of this jar, which stay
    name-only for exactly that reason.

    Motivation: akamai_markers_present only proves the cookie EXISTS, not
    that Akamai actually validated the session behind it — a cookie can
    be present but still reflect an unvalidated/rejected state. Community
    reverse-engineering of Akamai's _abck format (not officially
    published by Akamai, not verified against Apple's specific
    configuration — treat as a lead to check, not a confirmed fact)
    commonly describes it as roughly
    "<sensor_uid>~<validation_flag>~<payload>~<flag>~<flag>", with the
    SECOND field (right after the first "~") reportedly "-1" when the
    sensor's data hasn't been accepted/validated yet, vs. a different
    value once it has. Returned split on "~" so that field is directly
    visible per refresh cycle instead of guessing from presence alone.

    Returns {"abck_value_raw": str|None, "abck_segments": list[str]|None}
    — both None if no _abck cookie was captured at all this cycle.
    """
    abck_cookie = next((c for c in cookies_list if c["name"] == "_abck"), None)
    return {
        "abck_value_raw": abck_cookie["value"] if abck_cookie else None,
        "abck_segments": abck_cookie["value"].split("~") if abck_cookie else None,
    }


def _refresh_apple_cookies(url: str, pincode: str) -> dict:
    """Loads `url`, extracts its SKU, performs an in-page fetch of
    fulfillment-messages for `pincode` (see module note above), and returns
    the resulting session:
      {"cookies": "name=value; name2=value2; ...",
       "user_agent": <the UA this browser context used>,
       "pincode_check_confirmed": bool,  # fulfillment-messages returned a
                                          # 200 with a valid JSON body
       "diagnostics": {...}}
    cookies/user_agent are still returned even when pincode_check_confirmed
    is False (e.g. SKU extraction failed, or the fetch itself errored) —
    the page load alone still mints real anti-bot/session cookies, which
    may still be usable; the caller (the main bot) can decide whether an
    unconfirmed refresh is worth storing. Never raises for anything short
    of a hard Playwright/browser failure (navigation errors, missing SKU,
    and fetch failures are all captured in diagnostics instead).

    Deliberately slower than /check-stock's _render_page: diagnostic
    logging (see akamai_markers_present below) confirmed an earlier
    version of this function — which captured cookies immediately after
    domcontentloaded, blocked images/fonts/stylesheets, and closed the
    browser right after the fetch — produced a session with NONE of
    Akamai Bot Manager's own marker cookies (_abck/bm_sz/ak_bmsc) present
    at all. Akamai never validated the session in the first place, which
    is almost certainly why it died in ~9 minutes rather than the ~2
    hours a manually-copied, actively-used real browser session showed.
    This version gives that validation JS room to actually run: a
    bounded networkidle wait, a dwell period BEFORE the fulfillment
    fetch (not just before cookie extraction), no resource blocking (so
    every subresource the real page would load, loads), and a brief
    keep-alive after the fetch before the browser is torn down."""
    acquired = _check_semaphore.acquire(timeout=SLOT_WAIT_TIMEOUT_SECONDS)
    if not acquired:
        raise RuntimeError(
            f"too many concurrent checks (max {MAX_CONCURRENT_CHECKS}) — "
            f"timed out after {SLOT_WAIT_TIMEOUT_SECONDS}s waiting for a free slot"
        )
    try:
        with sync_playwright() as pw:
            browser, context = _new_browser_and_context(pw, user_agent=_apple_user_agent_for)
            try:
                page = context.new_page()
                # NO resource blocker here (unlike /check-stock and
                # /debug-network, which still use _make_resource_blocker
                # unchanged) — every image/font/stylesheet the real page
                # would load is allowed through, so this page load looks
                # as close to a real visitor's as possible to Akamai's own
                # fingerprinting, not a suspiciously partial one.

                diagnostics: dict = {
                    # The REAL, ground-truth bundled Chromium version this
                    # specific refresh actually ran on — added 2026-07-27
                    # alongside the Docker image version bump + dynamic
                    # User-Agent derivation (see _apple_user_agent_for's own
                    # note) so every future refresh's diagnostics directly
                    # confirm what engine minted THIS session, right next to
                    # akamai_markers_present/pincode_check_confirmed in the
                    # same log line — no need to separately verify a deploy
                    # landed or parse a version out of a UA string by hand.
                    "chromium_version": browser.version,
                    "goto_status": None,
                    "goto_error": None,
                    "networkidle_timed_out": False,
                    "networkidle_error": None,
                    "sku_extracted": None,
                    "fulfillment_url": None,
                    "fulfillment_status": None,
                    "fulfillment_error": None,
                }

                try:
                    main_response = page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                    if main_response is not None:
                        diagnostics["goto_status"] = main_response.status
                except Exception as exc:
                    diagnostics["goto_error"] = str(exc)
                    logger.error(f"[refresh-apple-cookies] page.goto failed for {url}: {exc}")

                # Bounded, best-effort — NOT the goto's own wait_until (a
                # hard networkidle gate risks the goto itself timing out on
                # a page with ongoing background activity, e.g. an
                # analytics beacon that never truly goes idle; mirrors the
                # identical pattern in _capture_network_calls above). A
                # timeout here just means "proceed with whatever loaded".
                if diagnostics["goto_error"] is None:
                    try:
                        page.wait_for_load_state("networkidle", timeout=APPLE_REFRESH_NETWORKIDLE_TIMEOUT_MS)
                    except PlaywrightTimeoutError:
                        diagnostics["networkidle_timed_out"] = True
                        logger.info(f"[refresh-apple-cookies] networkidle wait timed out for {url} — proceeding anyway")
                    except Exception as exc:
                        diagnostics["networkidle_error"] = str(exc)
                        logger.error(f"[refresh-apple-cookies] networkidle wait raised for {url}: {exc}")

                # Dwell BEFORE the fulfillment fetch (not just before cookie
                # extraction) — the fetch is the ONE request whose resulting
                # session actually matters, so Akamai's sensor/telemetry JS
                # needs this time to run before that request, not after it.
                page.wait_for_timeout(APPLE_REFRESH_DWELL_MS)

                html = ""
                try:
                    html = page.content()
                except Exception as exc:
                    logger.error(f"[refresh-apple-cookies] page.content() failed for {url}: {exc}")

                sku = _extract_apple_sku(html)
                diagnostics["sku_extracted"] = sku

                pincode_check_confirmed = False
                if sku:
                    fulfillment_url = _fulfillment_messages_url(url, sku, pincode)
                    diagnostics["fulfillment_url"] = fulfillment_url
                    if fulfillment_url:
                        try:
                            result = page.evaluate(_FULFILLMENT_FETCH_JS, fulfillment_url)
                            status = result.get("status")
                            diagnostics["fulfillment_status"] = status
                            if status == 200:
                                try:
                                    json.loads(result.get("body", ""))
                                    pincode_check_confirmed = True
                                except Exception:
                                    diagnostics["fulfillment_error"] = "non-JSON response body"
                            else:
                                diagnostics["fulfillment_error"] = f"HTTP {status}"
                        except Exception as exc:
                            diagnostics["fulfillment_error"] = str(exc)
                            logger.warning(
                                f"[refresh-apple-cookies] in-page fulfillment-messages "
                                f"fetch failed for {url}: {exc}"
                            )
                    else:
                        diagnostics["fulfillment_error"] = "product URL is not an apple.com /shop/ page"
                else:
                    diagnostics["fulfillment_error"] = "could not extract SKU/part number from product page"

                # Keep the context alive a bit longer after the fetch before
                # snapshotting cookies and closing the browser — any
                # post-fetch Set-Cookie rotation or sensor callback the page
                # triggers in response to that request gets a chance to land
                # instead of being cut off by an immediate browser.close().
                try:
                    page.evaluate("() => document.title")
                except Exception as exc:
                    logger.warning(f"[refresh-apple-cookies] post-fetch keep-alive evaluate failed for {url}: {exc}")
                page.wait_for_timeout(APPLE_REFRESH_POST_FETCH_WAIT_MS)

                cookies_list = context.cookies()
                cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies_list)

                # Diagnostic only — NAMES, never values (this Cookie header is
                # a live Apple session; values shouldn't land in Railway
                # logs). Added to investigate a session dying in ~9 minutes
                # (far faster than the ~2-hour manual-cookie pattern this
                # replaced) — _abck/bm_sz/ak_bmsc are Akamai Bot Manager's
                # own marker cookie names; their ABSENCE, or presence in an
                # under-validated state, would support the hypothesis that
                # this flow captures cookies before Akamai's own sensor/
                # telemetry JS has had time to validate the session (no dwell
                # time between page load and the in-page fetch, and the
                # browser closes immediately after — see the module note
                # above _refresh_apple_cookies). Not yet acted on — this is
                # observation only until the actual log output is reviewed.
                diagnostics["cookie_names"] = sorted(c["name"] for c in cookies_list)
                diagnostics["akamai_markers_present"] = {
                    name: (name in diagnostics["cookie_names"])
                    for name in ("_abck", "bm_sz", "ak_bmsc")
                }

                # 2026-07-27 — _abck's raw VALUE (not just its name/presence,
                # unlike every other cookie above — see cookie_names) is
                # logged here specifically. Safe to: _abck is Akamai Bot
                # Manager's own anti-bot sensor/telemetry marker, not an
                # account/session credential — it grants no access to
                # anything on apple.com by itself, unlike the real session
                # cookies (dssid2, as_atb, mbox, etc.) that make up the rest
                # of this jar, which stay name-only for exactly that reason.
                #
                # Motivation: akamai_markers_present above only proves the
                # cookie EXISTS, not that Akamai actually validated the
                # session behind it — a cookie can be present but still
                # reflect an unvalidated/rejected state. Community reverse-
                # engineering of Akamai's _abck format (not officially
                # published by Akamai, not verified against Apple's specific
                # configuration — treat as a lead to check, not a confirmed
                # fact) commonly describes it as roughly
                # "<sensor_uid>~<validation_flag>~<payload>~<flag>~<flag>",
                # with the SECOND field (right after the first "~")
                # reportedly "-1" when the sensor's data hasn't been
                # accepted/validated yet, vs. a different value once it has.
                # Logged split on "~" so that field is directly visible per
                # refresh cycle instead of guessing from presence alone.
                diagnostics.update(_abck_diagnostics(cookies_list))

                logger.info(
                    f"[refresh-apple-cookies] {url}: {len(cookies_list)} cookie(s) captured, "
                    f"pincode_check_confirmed={pincode_check_confirmed}, diagnostics={diagnostics}"
                )

                return {
                    "cookies": cookie_header,
                    # Recomputed from the same (already-launched) browser
                    # instance rather than a stored constant — a pure,
                    # deterministic function of browser.version, so this
                    # is guaranteed identical to whatever was actually
                    # applied to the context above.
                    "user_agent": _apple_user_agent_for(browser),
                    "pincode_check_confirmed": pincode_check_confirmed,
                    "diagnostics": diagnostics,
                }
            finally:
                browser.close()
    finally:
        _check_semaphore.release()


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/check-stock", methods=["POST"])
    def check_stock():
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        store = (data.get("store") or "").strip().lower()
        if not url or not store:
            return jsonify({"error": "url and store are required"}), 400
        if store not in CHECKERS:
            return jsonify({
                "error": f"unsupported store {store!r}",
                "supported_stores": sorted(CHECKERS),
            }), 400

        result = _fetch_and_check(url, store)
        return jsonify({"url": url, "store": store, **result}), 200

    @app.route("/debug-network", methods=["POST"])
    def debug_network():
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        pincode = (data.get("pincode") or "").strip() or DEFAULT_DEBUG_PINCODE
        if not url:
            return jsonify({"error": "url is required"}), 400

        try:
            result = _capture_network_calls(url, pincode)
        except Exception as exc:
            logger.error(f"[debug-network] failed for {url}: {exc}")
            return jsonify({"url": url, "pincode": pincode, "error": str(exc)}), 502

        return jsonify({"url": url, "pincode": pincode, **result}), 200

    @app.route("/debug-dom", methods=["POST"])
    def debug_dom():
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        click_text = (data.get("click_text") or "").strip() or None
        if not url:
            return jsonify({"error": "url is required"}), 400

        try:
            result = _capture_dom_elements(url, click_text=click_text)
        except Exception as exc:
            logger.error(f"[debug-dom] failed for {url}: {exc}")
            return jsonify({"url": url, "error": str(exc)}), 502

        return jsonify({"url": url, **result}), 200

    @app.route("/debug-pickup-flow", methods=["POST"])
    def debug_pickup_flow():
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        pincode = (data.get("pincode") or "").strip() or DEFAULT_DEBUG_PINCODE
        if not url:
            return jsonify({"error": "url is required"}), 400

        try:
            result = _capture_pickup_flow(url, pincode)
        except Exception as exc:
            logger.error(f"[debug-pickup-flow] failed for {url}: {exc}")
            return jsonify({"url": url, "pincode": pincode, "error": str(exc)}), 502

        return jsonify({"url": url, "pincode": pincode, **result}), 200

    @app.route("/check-pickup-availability", methods=["POST"])
    def check_pickup_availability():
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        pincode = (data.get("pincode") or "").strip()
        if not url or not pincode:
            return jsonify({"error": "url and pincode are required"}), 400

        try:
            result = _check_pickup_availability(url, pincode)
        except Exception as exc:
            logger.error(f"[check-pickup-availability] failed for {url}: {exc}")
            return jsonify({"url": url, "pincode": pincode, "error": str(exc)}), 502

        return jsonify({"url": url, "pincode": pincode, **result}), 200

    @app.route("/debug-zipcode-validation", methods=["POST"])
    def debug_zipcode_validation():
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        pincode = (data.get("pincode") or "").strip()
        if not url or not pincode:
            return jsonify({"error": "url and pincode are required"}), 400

        try:
            result = _diagnose_zipcode_validation(url, pincode)
        except Exception as exc:
            logger.error(f"[debug-zipcode-validation] failed for {url}: {exc}")
            return jsonify({"url": url, "pincode": pincode, "error": str(exc)}), 502

        return jsonify({"url": url, "pincode": pincode, **result}), 200

    @app.route("/refresh-apple-cookies", methods=["POST"])
    def refresh_apple_cookies():
        if INTERNAL_REFRESH_TOKEN:
            supplied = request.headers.get("X-Internal-Token", "")
            if supplied != INTERNAL_REFRESH_TOKEN:
                return jsonify({"error": "unauthorized"}), 401

        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        pincode = (data.get("pincode") or "").strip() or DEFAULT_DEBUG_PINCODE
        if not url:
            return jsonify({"error": "url is required"}), 400

        try:
            result = _refresh_apple_cookies(url, pincode)
        except Exception as exc:
            logger.error(f"[refresh-apple-cookies] failed for {url}: {exc}")
            return jsonify({"url": url, "pincode": pincode, "error": str(exc)}), 502

        return jsonify({"url": url, "pincode": pincode, **result}), 200

    @app.route("/health", methods=["GET"])
    def health():
        # playwright_version: the installed pip package version (cheap —
        # reads package metadata, no browser launch) — added 2026-07-27 to
        # make a Railway deploy's actual version directly verifiable via a
        # single GET instead of having to infer it from log lines. Kept in
        # lockstep with the Dockerfile's base image tag (see requirements.txt
        # — the two are pinned together deliberately), so this is strong
        # evidence of whether a Docker-image version bump actually deployed,
        # without needing the heavier confirmation of an actual browser
        # launch (see /refresh-apple-cookies's own chromium_version in its
        # diagnostics for that — the real, ground-truth bundled engine
        # version, from a browser that's already being launched anyway for
        # that endpoint's real work).
        try:
            playwright_version = importlib.metadata.version("playwright")
        except Exception:
            playwright_version = None
        return jsonify({
            "ok": True,
            "max_concurrent_checks": MAX_CONCURRENT_CHECKS,
            "proxy_configured": _proxy_config() is not None,
            "supported_stores": sorted(CHECKERS),
            "apple_cookie_refresh_auth_required": bool(INTERNAL_REFRESH_TOKEN),
            "playwright_version": playwright_version,
            "git_commit_sha": GIT_COMMIT_SHA,
        })

    return app


def main() -> None:
    app = create_app()
    from waitress import serve
    logger.info(f"[http] serving on 0.0.0.0:{PORT} (max_concurrent_checks={MAX_CONCURRENT_CHECKS})")
    serve(app, host="0.0.0.0", port=PORT, threads=MAX_CONCURRENT_CHECKS + 2)


if __name__ == "__main__":
    main()
