"""Shared utilities for all site checkers."""

import asyncio
import json
import logging
import os
from urllib.parse import urlparse, urlencode

import httpx

import zyte_client
from config import SUPPORTED_SITES, SCRAPING_PROVIDER

logger = logging.getLogger(__name__)

SCRAPEDO_API_URL = "https://api.scrape.do/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}


def detect_site(url: str) -> str | None:
    host = urlparse(url).netloc.lower().replace("www.", "")
    for site_key, domains in SUPPORTED_SITES.items():
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return site_key
    return None


def build_scraper_url(
    url: str,
    render_js: bool = False,
    set_cookies: str | None = None,
    custom_headers: bool = False,
    wait_until: str | None = None,
    custom_wait_ms: int | None = None,
    super_proxy: bool = False,
    play_with_browser: list[dict] | None = None,
) -> str:
    # Read at call time so Railway's runtime env var is always used,
    # regardless of when this module was first imported.
    token = os.environ.get("SCRAPEDO_KEY", "")
    params = {
        "token": token,
        "url": url,
        "geoCode": "in",
    }
    if render_js:
        params["render"] = "true"
    if set_cookies:
        params["setCookies"] = set_cookies
    if custom_headers:
        # Scrape.do's "Custom Headers" feature: when set, it forwards every
        # header on the request TO Scrape.do straight through to the target
        # site, instead of using its own defaults. Needed for endpoints that
        # require caller-supplied auth headers (e.g. Croma's authenticated
        # inventory API) rather than a plain page fetch.
        params["customHeaders"] = "true"
    if wait_until:
        # Scrape.do's Puppeteer-backed render wait condition (e.g.
        # "networkidle0") — only meaningful alongside render_js=True. Opt-in,
        # unused by every existing call site, so this is a no-op for them.
        params["waitUntil"] = wait_until
    if custom_wait_ms:
        # Extra fixed wait (ms) after waitUntil fires — a fallback buffer for
        # pages whose JS keeps mutating the DOM after the network goes idle.
        # Also opt-in/unused by existing call sites.
        params["customWait"] = str(custom_wait_ms)
    if super_proxy:
        # Scrape.do's premium/residential proxy pool ("Super Proxy") — costs
        # more credits per request than the default proxy tier and may not
        # be available on every plan. Opt-in, unused by existing call sites.
        params["super"] = "true"
    if play_with_browser:
        # Scrape.do's browser-interaction action chain (Click/Fill/Wait/
        # WaitSelector/Execute/...) — confirmed to exist via Scrape.do's
        # own documentation (scrape.do/documentation/headless-browser/
        # browser-interactions/, cross-checked across two independent
        # search-result extractions since a direct fetch of that page
        # from this sandbox returned HTTP 403). Only meaningful alongside
        # render_js=True (requires the headless browser). Passed as a
        # JSON array under the "playWithBrowser" query parameter;
        # urlencode() below applies the required URL-encoding. Whether
        # this combines with super_proxy, and whether it's available on
        # every plan, is NOT confirmed — untested beyond what the docs
        # describe. Opt-in, unused by every existing call site.
        params["playWithBrowser"] = json.dumps(play_with_browser)
    return f"{SCRAPEDO_API_URL}?{urlencode(params)}"


async def fetch_page(
    url: str,
    render_js: bool = False,
    set_cookies: str | None = None,
    custom_headers: bool = False,
    wait_until: str | None = None,
    custom_wait_ms: int | None = None,
    super_proxy: bool = False,
    play_with_browser: list[dict] | None = None,
    timeout: float = 60.0,
    site: str | None = None,
) -> httpx.Response:
    """
    THE central fetch function every checker and /debug* command routes
    through — provider-aware (config.SCRAPING_PROVIDER: "zyte" [default,
    PRIMARY since Scrape.do's credits ran out] or "scrapedo" [Scrape.do's
    original code path via build_scraper_url above, kept fully intact and
    simply unused while the flag is "zyte" — flip it back the moment
    Scrape.do credits are recharged, no code changes needed]).

    Same parameter meanings regardless of provider (render_js -> JS-rendered
    fetch, super_proxy -> the provider's "try harder against a block" tier,
    etc.), so every existing call site's render-tier logic (OnePlus's
    wait_until/custom_wait_ms, RelianceDigital's super_proxy, ShopAtSC's
    three-tier escalation, ...) is preserved unchanged no matter which
    provider is actually running underneath. Always returns a genuine
    httpx.Response — for zyte, synthesized from Zyte's JSON reply by
    zyte_client.fetch_page (see that module's docstring for the exact
    request/response field mapping) — so every caller's existing
    resp.text / resp.status_code / resp.json() / resp.raise_for_status()
    usage keeps working unchanged.

    `site` is purely for usage-cost tracking (database.record_zyte_usage,
    called from INSIDE zyte_client.fetch_page after every successful Zyte
    request) — optional, defaults to None (logged under "(debug/other)" —
    see get_zyte_usage_summary) for callers with no tracked-product "site"
    concept, e.g. /debugzyte on an arbitrary URL or Apple's fulfillment-
    messages API call. Only recorded on the zyte path; Scrape.do usage has
    its own separate dashboard the admin already monitors directly, so
    there's no matching tracking on that path.
    """
    if SCRAPING_PROVIDER == "zyte":
        return await zyte_client.fetch_page(
            url,
            render_js=render_js,
            super_proxy=super_proxy,
            wait_until=wait_until,
            custom_wait_ms=custom_wait_ms,
            set_cookies=set_cookies,
            custom_headers=custom_headers,
            play_with_browser=play_with_browser,
            timeout=timeout,
            site=site,
        )

    # scrapedo — Scrape.do's original code path, untouched, only reached
    # when SCRAPING_PROVIDER is explicitly set back to "scrapedo".
    scraper_url = build_scraper_url(
        url,
        render_js=render_js,
        set_cookies=set_cookies,
        custom_headers=custom_headers,
        wait_until=wait_until,
        custom_wait_ms=custom_wait_ms,
        super_proxy=super_proxy,
        play_with_browser=play_with_browser,
    )
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
        return await client.get(scraper_url)


# ---------------------------------------------------------------------------
# 502 retry helper — Scrape.do's premium/residential proxy pool
# occasionally returns HTTP 502 (seen with an ErrorType: "ROTATION_FAILED"
# header, but not guaranteed to carry it) when a proxy rotation fails.
# Retrying shortly after usually gets a different rotation. Written as a
# general reusable helper (any render_js/super_proxy/wait combination),
# but currently only wired in for Unicorn Store's render=true+super=true
# fallback tier — see stock_checker.py's _RETRY_502_SITES and
# admin_handlers.py's /debugunicorn.
# ---------------------------------------------------------------------------
_DEFAULT_502_RETRY_ATTEMPTS = 3
_DEFAULT_502_RETRY_WAIT_SECONDS = 4.0  # middle of the 3-5s range — gives
# Scrape.do's proxy pool a chance to hand back a different rotation
# without stalling the caller for too long.


async def fetch_with_502_retry(
    url: str,
    render_js: bool = False,
    super_proxy: bool = False,
    set_cookies: str | None = None,
    wait_until: str | None = None,
    custom_wait_ms: int | None = None,
    max_attempts: int = _DEFAULT_502_RETRY_ATTEMPTS,
    retry_wait_seconds: float = _DEFAULT_502_RETRY_WAIT_SECONDS,
    timeout: float = 60.0,
    site: str | None = None,
) -> tuple[httpx.Response | None, list[dict]]:
    """
    Fetch url via Scrape.do, automatically retrying up to max_attempts
    TOTAL attempts whenever the response is HTTP 502 — Scrape.do's own
    proxy-rotation-failure symptom (an "ErrorType: ROTATION_FAILED"
    response header is logged when present, but retrying is triggered by
    the 502 status alone, since the header isn't guaranteed present).
    Waits retry_wait_seconds between attempts.

    Any OTHER outcome (a non-502 response, or an exception like a
    timeout/connection error) is returned/logged immediately with NO
    retry — this helper exists specifically for the "try again for a
    fresh proxy rotation" symptom, not as a general-purpose retry-on-
    anything wrapper. It never raises for a 502 (that's the condition
    being retried around); an exception during the request IS captured
    in the attempts log and stops the loop rather than being retried, so
    a persistent network failure can't spin for max_attempts * wait_seconds
    before surfacing.

    The fetch itself now goes through fetch_page() (provider-aware — see
    that function), so this works under Zyte too, but the RETRY CONDITION
    (status_code == 502) is still exactly Scrape.do's proxy-rotation
    symptom specifically and hasn't been re-tuned for Zyte's own ban signal
    (HTTP 520 from Zyte's endpoint — see zyte_client.py). Under
    SCRAPING_PROVIDER="zyte" this degrades gracefully to "no retry, return
    whatever fetch_page() gave back" rather than looping on the wrong
    status code — safe, just not yet doing anything extra for a Zyte-side
    ban. Re-tune if that's confirmed to need retrying too.

    Returns (response, attempts):
      - response: the LAST httpx.Response obtained (a genuine non-502
        response, or the final still-502 response if every attempt was a
        502), or None if an exception was raised before any response was
        received.
      - attempts: a list of per-attempt diagnostic dicts, in order:
        {"attempt": int, "status_code": int | None, "error": str | None}.

    Never crashes or hangs indefinitely: exactly max_attempts network
    calls at most, each bounded by `timeout`, with retry_wait_seconds of
    sleep between 502s — the caller decides what to do with an
    exhausted-retries (still-502) or errored response by inspecting it
    and `attempts`, rather than this helper raising or looping forever.
    """
    attempts: list[dict] = []
    response: httpx.Response | None = None

    for attempt_num in range(1, max_attempts + 1):
        try:
            response = await fetch_page(
                url, render_js=render_js, super_proxy=super_proxy, set_cookies=set_cookies,
                wait_until=wait_until, custom_wait_ms=custom_wait_ms, timeout=timeout, site=site,
            )
        except Exception as exc:
            attempts.append({"attempt": attempt_num, "status_code": None, "error": f"{type(exc).__name__}: {exc}"})
            logger.warning(f"fetch_with_502_retry: attempt {attempt_num}/{max_attempts} failed (non-502): {exc}")
            return response, attempts

        if response.status_code != 502:
            attempts.append({"attempt": attempt_num, "status_code": response.status_code, "error": None})
            return response, attempts

        error_type = response.headers.get("ErrorType") or response.headers.get("errortype")
        error_desc = f"HTTP 502{f' (ErrorType: {error_type})' if error_type else ''}"
        attempts.append({"attempt": attempt_num, "status_code": 502, "error": error_desc})
        logger.warning(f"fetch_with_502_retry: attempt {attempt_num}/{max_attempts} got {error_desc}")

        if attempt_num < max_attempts:
            await asyncio.sleep(retry_wait_seconds)

    return response, attempts


# ---------------------------------------------------------------------------
# Generic marketplace stock-check waterfall — shared by newer checkers for
# sites this codebase has no live-network access to individually verify
# against real pages yet (see each caller module for its own per-site
# pattern choices). Ported from checkers/reliancedigital.py, whose own
# AggregateOffer/disabled-class handling grew out of real production
# misreads — this generic version starts from that same lesson rather than
# a naive fresh implementation, but has NOT itself been verified against
# real pages for any site using it. Treat its result as a tuning starting
# point once real /check results (or a dedicated debug command, following
# the /debugreliance precedent) are available for that site.
# ---------------------------------------------------------------------------

_DISABLED_CLASS_MARKERS = ("disable", "inactive")


def _element_is_disabled(el) -> bool:
    """True if a BS4 element is visually/semantically disabled — via the
    `disabled` attribute, `aria-disabled="true"`, or a
    _DISABLED_CLASS_MARKERS substring in its class list."""
    if el.get("disabled") is not None:
        return True
    if el.get("aria-disabled", "").lower() == "true":
        return True
    classes = " ".join(el.get("class", [])).lower()
    return any(marker in classes for marker in _DISABLED_CLASS_MARKERS)


def _offer_availability(offers) -> str:
    """Extract the first availability string from a JSON-LD 'offers' value
    that may be a single Offer dict, an AggregateOffer dict wrapping a
    nested offers list, or a plain list of Offer dicts. Returns "" when
    none is found."""
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


_GENERIC_IN_STOCK_JSON_KEYS = (
    '"inStock":true', '"in_stock":true', '"isAvailable":true',
    '"available":true', '"sellable":true', '"is_available":true',
)
_GENERIC_OUT_OF_STOCK_JSON_KEYS = (
    '"inStock":false', '"in_stock":false', '"isAvailable":false',
    '"available":false', '"sellable":false', '"is_available":false',
)


def generic_marketplace_check(
    soup, html: str, add_patterns: list[str], oos_patterns: list[str], site_label: str,
) -> bool:
    """
    Best-guess generic retail-marketplace stock check: JSON-LD availability
    -> embedded-JSON stock key -> negative (OOS) text -> active
    add-to-cart button/attribute -> default False. `add_patterns`/
    `oos_patterns` are lowercase substrings the caller supplies per site;
    `site_label` is used only for log-line prefixes. Defaults to False
    (out of stock) when no signal is found at all, per this codebase's
    standing principle that a missed alert is safer than a false one.
    """
    html_lower = html.lower()

    # ── JSON-LD ──────────────────────────────────────────────────────────
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict):
                continue
            avail = _offer_availability(item.get("offers", {}))
            if "InStock" in avail:
                logger.info(f"[{site_label}] JSON-LD: InStock → True")
                return True
            if "OutOfStock" in avail or "Discontinued" in avail:
                logger.info(f"[{site_label}] JSON-LD: OutOfStock/Discontinued → False")
                return False

    # ── Embedded JSON ────────────────────────────────────────────────────
    for key in _GENERIC_IN_STOCK_JSON_KEYS:
        if key in html:
            logger.info(f"[{site_label}] embedded JSON {key!r} → True")
            return True
    for key in _GENERIC_OUT_OF_STOCK_JSON_KEYS:
        if key in html:
            logger.info(f"[{site_label}] embedded JSON {key!r} → False")
            return False

    # ── Negative signals (checked before buttons) ───────────────────────
    for pattern in oos_patterns:
        if pattern in html_lower:
            logger.info(f"[{site_label}] OOS signal: '{pattern}' → False")
            return False

    # ── Buttons — skip disabled ──────────────────────────────────────────
    for btn in soup.find_all("button"):
        if _element_is_disabled(btn):
            continue
        text = btn.get_text(strip=True).lower()
        if any(p in text for p in add_patterns):
            logger.info(f"[{site_label}] active button '{text[:40]}' → True")
            return True

    # ── Attrs — skip disabled ────────────────────────────────────────────
    for attr in ("data-testid", "aria-label", "id"):
        for el in soup.find_all(attrs={attr: True}):
            if _element_is_disabled(el):
                continue
            val = (el.get(attr) or "").lower()
            if "add-to-cart" in val or "addtocart" in val or any(p in val for p in add_patterns):
                logger.info(f"[{site_label}] active attr {attr}='{val[:40]}' → True")
                return True

    logger.info(f"[{site_label}] no conclusive signal → defaulting OUT OF STOCK (False)")
    return False


# ---------------------------------------------------------------------------
# Generic product-name extraction — used by the channel-forwarding feature's
# /addchannel auto-naming (admin_handlers.py) when no custom name was
# given. Only checkers/apple.py had a name extractor before this
# (_extract_product_name, for its separate pickup-tracking feature) — no
# other checker returns a name at all (check_stock() itself only ever
# returns (in_stock, price), never a name). Same JSON-LD-then-<title>
# heuristic as Apple's own version, kept as a separate site-agnostic copy
# here rather than importing checkers/apple.py's (which is scoped to a
# different feature) — this one is meant to work across every site.
# ---------------------------------------------------------------------------

def extract_generic_product_name(soup) -> str | None:
    """Best-effort product display name from an already-fetched page: tries
    JSON-LD's "name" field first, then the page <title> tag. Returns None
    if neither is found — callers should fall back to a URL-derived name
    in that case, never show a blank name."""
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
