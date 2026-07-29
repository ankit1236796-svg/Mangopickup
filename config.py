import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DB_PATH = os.getenv("DB_PATH", "/app/data/stock_alerts.db")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))  # seconds (5 min default)

# ---------------------------------------------------------------------------
# Admin / monetization
# ---------------------------------------------------------------------------
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "5004721766"))
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "2"))
GRACE_PERIOD_DAYS = int(os.getenv("GRACE_PERIOD_DAYS", "7"))
REMINDER_HOURS_BEFORE_EXPIRY = int(os.getenv("REMINDER_HOURS_BEFORE_EXPIRY", "6"))
# How often the access-maintenance loop (reminders + grace-period purge) runs.
# Independent of CHECK_INTERVAL (stock checking) — needs finer granularity than
# once/day so the 6-hour-before-expiry reminder window isn't missed, but purging
# is idempotent so running it this often is harmless.
ACCESS_CHECK_INTERVAL = int(os.getenv("ACCESS_CHECK_INTERVAL", "1800"))  # 30 min default

# Number of WhatsApp share+confirm rounds required before /freetrial's
# one-time trial bonus can be claimed — see database.py's
# increment_share_trial_round / activate_share_trial.
SHARE_TRIAL_ROUNDS_REQUIRED = int(os.getenv("SHARE_TRIAL_ROUNDS_REQUIRED", "5"))

# Telegram never notifies a bot when a `url`-type inline button (like "Share
# on WhatsApp") is tapped — there's no event to detect it. This delay is the
# closest available approximation: the "Done" button for each /freetrial
# round is withheld for this many seconds after the share button appears, so
# it can't be spammed through instantly without at least a brief pause. It
# does NOT verify an actual share happened.
SHARE_TRIAL_TAP_DELAY_SECONDS = int(os.getenv("SHARE_TRIAL_TAP_DELAY_SECONDS", "10"))

# Sites whose results are confirmed unreliable enough that AUTOMATIC alerts
# must be suppressed until root-caused and fixed — Croma was here after
# being observed flipping between correct and fully-inverted results
# across two manual checks ~8-9 minutes apart, for the same tracked
# products (the old HTML-scraping checker). Manual /check still runs and
# shows a result, but with an explicit reliability warning (see
# handlers.py) rather than silently trusting it. Removed once a site's
# checker is fixed AND validated against real traffic — Croma's checker
# was replaced entirely with Croma's own internal inventory API (see
# SUPPORTED_SITES' Croma comment below) and manually verified accurate
# against the live site for both in-stock and out-of-stock cases, so it's
# no longer listed here; automatic "back in stock" alerts fire for it again.
UNRELIABLE_SITES = set()

# Playwright settings
PLAYWRIGHT_HEADLESS = True
PLAYWRIGHT_TIMEOUT = 30000  # ms

# ---------------------------------------------------------------------------
# Scraping provider (Scrape.do vs Zyte API)
# ---------------------------------------------------------------------------
# Scrape.do's credits ran out — Zyte API (https://api.zyte.com/v1/extract) is
# now the PRIMARY provider every checker fetch routes through (see
# checkers/common.py's fetch_page(), the central function all checkers and
# /debug* commands call, and zyte_client.py for the actual Zyte request/
# response handling). Scrape.do's own code path (build_scraper_url + a plain
# GET) is left FULLY INTACT and simply unused while this is "zyte" — flip it
# back to "scrapedo" here (or via the SCRAPING_PROVIDER env var) the moment
# Scrape.do credits are recharged; no code changes needed either way.
SCRAPING_PROVIDER = os.getenv("SCRAPING_PROVIDER", "zyte").strip().lower()
# ZYTE_API_KEY itself is read directly via os.environ in zyte_client.py at
# call time, not through this module — mirroring how SCRAPEDO_KEY is read
# directly via os.environ in checkers/common.py rather than through
# config.py, so a Railway env var change takes effect without an
# import-order dependency.

# Supported sites
#
# Vijay Sales (vijaysales.com) deliberately NOT added — investigated and
# skipped, not "not yet built". Four separate diagnostic passes (via
# test_new_store_signals.py against real confirmed-OOS/in-stock product pages)
# each found a real reliability problem: JSON-LD offers.availability is
# static/stale (reads "InStock" on a confirmed-OOS page too); page-wide OOS
# text ("currently unavailable", "notify me") appears near the buy box on BOTH
# OOS and in-stock pages (likely a generic price-drop-alert widget, not real
# stock text); the Add to Cart button's disabled-attribute was INCONSISTENT
# across render modes for the same product (disabled=None at render=false,
# disabled='' at render=true) — a headless-browser hydration-timing artifact,
# not a real signal (the same trap that caused Croma's flip-flopping); and a
# dedicated stock-status class element found on the page ("instock__text")
# turned out to be identical + hidden (display:none) on both OOS and in-stock
# pages, i.e. dead/unused markup. A targeted search for Magento/Adobe
# Commerce's own internal GraphQL+MSI field names (stock_status, is_salable,
# salable_quantity) — the last, most specific hypothesis tried — also came
# back completely empty at both render modes, meaning the real stock data is
# most likely fetched via a separate XHR call after page load that's invisible
# to any HTML fetch regardless of render mode. Shipping a checker on any of
# the four unreliable signals would produce false alerts; skip until a better
# diagnostic approach is found or Vijay Sales changes its site architecture.
#
# Croma: RE-ADDED here. It was previously pulled from /add and /stores
# entirely (see git history) after its old HTML-scraping checker was
# confirmed to flip between correct and fully-inverted results across
# consecutive checks, then later degrade to reporting every product OOS,
# with no root cause ever identified — shipping that would produce false
# alerts (or false silence) for users. checkers/croma.py's scraping-based
# check() has since been replaced ENTIRELY with a direct call to Croma's
# own free internal inventory API (checkers.croma.check_via_api) — a
# structured JSON response, not page-text/DOM heuristics, so the specific
# failure mode above (an HTML-signal guess quietly going wrong) no longer
# applies the same way. Manually verified accurate against the live site
# for both in-stock and out-of-stock cases, so it's no longer in
# UNRELIABLE_SITES above either — automatic alerts are back on.
SUPPORTED_SITES = {
    "amazon":          ["amazon.in", "amazon.com"],
    "flipkart":        ["flipkart.com"],
    "zepto":           ["zeptonow.com", "zepto.com"],
    "bigbasket":       ["bigbasket.com"],
    "blinkit":         ["blinkit.com"],
    "instamart":       ["swiggy.com"],
    "myntra":          ["myntra.com"],
    "jiomart":         ["jiomart.com"],
    "reliancedigital": ["reliancedigital.in"],
    "apple":           ["apple.com"],
    "oneplus":         ["oneplus.in"],
    "tataneu":         ["tataneu.com"],
    "croma":           ["croma.com"],
    # Brand storefronts (BBK group, like OnePlus). Bare-domain entries so the
    # shopping subdomains resolve via detect_site's endswith("."+domain) check
    # (e.g. mshop.vivo.com → vivo.com, mshop.iqoo.com/shop.iqoo.com → iqoo.com).
    "vivo":            ["vivo.com"],
    "iqoo":            ["iqoo.com"],
    "unicornstore":     ["unicornstore.in"],
    "vijaysales":       ["vijaysales.com"],
    "inventstore":      ["inventstore.in"],
    "sangeethamobiles": ["sangeethamobiles.com"],
    "shopatsc":         ["shopatsc.com"],
}

# Domains handled specially in /add with a "Coming Soon" message instead of
# the generic "unsupported site" one — see handlers.py's _coming_soon_message.
# croma.com REMOVED from here now that it's back in SUPPORTED_SITES above.
COMING_SOON_DOMAINS: set[str] = set()

# Per-site display-name override for user-facing text (Telegram messages,
# product listings, /stores). Falls back to site.capitalize() via
# get_site_label() below for every site not listed here — most internal
# keys capitalize fine on their own (e.g. "amazon" -> "Amazon"), so this
# stays small and only covers sites where that would look wrong or omit
# useful context (e.g. "shopatsc" -> "Shopatsc" loses the fact that it's
# Sony India's official PS5 store).
SITE_DISPLAY_NAMES = {
    "shopatsc": "ShopAtSC (PS5 Official Site)",
}


def get_site_label(site: str) -> str:
    return SITE_DISPLAY_NAMES.get(site, site.capitalize())

# ---------------------------------------------------------------------------
# Affiliate-link conversion (EarnKaro / EK Affiliaters — see affiliate.py)
# ---------------------------------------------------------------------------
# Stores for which the bot attempts EarnKaro affiliate-link conversion on the
# "back in stock" alert. Conversion is best-effort: any failure falls back to
# the original URL, so a store listed here that EarnKaro doesn't actually
# support just won't convert (no harm). Amazon is ALWAYS excluded (handled
# separately via its own Associates tag) regardless of what's configured here.
# Override without a redeploy via the AFFILIATE_ENABLED_SITES env var
# (comma-separated site keys, e.g. "flipkart,myntra,ajio"); the default is the
# set confirmed working against the live API. The API key itself is read
# separately from EARNKARO_API_KEY at call time (see affiliate.py) and is never
# stored in code.
AFFILIATE_ENABLED_SITES = {
    s.strip().lower()
    for s in os.getenv("AFFILIATE_ENABLED_SITES", "flipkart,myntra").split(",")
    if s.strip()
}
AFFILIATE_ENABLED_SITES.discard("amazon")  # never, regardless of config

# ---------------------------------------------------------------------------
# WhatsApp channel forwarding (separate whatsapp_forwarder/ service — see
# that directory's README for deployment. NOT the same process as this bot;
# Playwright/browser-automation dependencies live entirely over there.)
# ---------------------------------------------------------------------------
# Base URL of the whatsapp_forwarder service's internal API (no trailing
# slash), e.g. "https://whatsapp-forwarder.up.railway.app". Left unset by
# default — whatsapp_client.py treats an empty value as "feature not
# configured" and never attempts a request, so the bot behaves identically to
# today until this is explicitly set.
WHATSAPP_FORWARDER_URL = os.getenv("WHATSAPP_FORWARDER_URL", "").rstrip("/")
# Shared secret the bot sends as a Bearer token on every forward request; must
# match the same value configured on the whatsapp_forwarder service.
WHATSAPP_FORWARDER_SECRET = os.getenv("WHATSAPP_FORWARDER_SECRET", "")

# ---------------------------------------------------------------------------
# Apple official-store pickup-availability auto-check (checkers/apple.py's
# check_pickup_at_official_stores + bot.run_apple_official_pickup_cycle) —
# a THIRD, separate Apple signal from the two that already exist:
#   1. checkers.apple.check() — the generic Add-to-Bag/JSON-LD page checker
#      (CHECKER_MAP, runs for every /add-tracked apple.com product).
#   2. checkers.apple.refine_with_pincode() — confirms in-stock via the
#      fulfillment-messages API for the USER'S OWN saved primary pincode
#      only (stock_checker.check_stock()'s site=="apple" branch).
#   3. THIS feature — automatically checks all 6 fixed official Apple
#      Store pincodes below for every /add-tracked apple.com product
#      (no user opt-in/pincode selection involved, unlike the separate
#      /trackpickup + pickup_tracking system, which lets a user track
#      pincodes of their own choosing). Reports which specific store(s)
#      show pickup available.
# These three don't interfere with each other; this one is purely additive.
#
# Fixed list of India's 6 physical Apple Store pincodes as of this writing
# (confirmed via WebSearch cross-referencing apple.com/in/retail/storelist/
# and each store's own listing — including 201301 for Apple Noida,
# specifically double-checked). Override via env var (comma-separated) if a
# new store opens or one of these needs correcting; no code change needed.
APPLE_PICKUP_PINCODES = [
    p.strip() for p in os.getenv(
        "APPLE_PICKUP_PINCODES", "110017,400051,400066,560092,411001,201301"
    ).split(",") if p.strip()
]

# Pincode -> human-readable store label, for reporting when Apple's own
# fulfillment-messages response doesn't include a usable storeName for a
# given pincode (e.g. zero stores returned there) — lets a report/log line
# still say WHICH of the 6 official stores a pincode corresponds to.
APPLE_PICKUP_STORE_LABELS = {
    "110017": "Apple Saket, New Delhi",
    "400051": "Apple BKC, Mumbai",
    "400066": "Apple Borivali, Mumbai",
    "560092": "Apple Hebbal, Bengaluru",
    "411001": "Apple Koregaon Park, Pune",
    "201301": "Apple Noida",
}

# NOTE on the fulfillment-messages query itself: a location=<pincode>
# request via this codebase's cookie-based transport (checkers/apple.py's
# _cookie_auth_fetch) initially 541'd ("Page Not Found"), which was first
# mis-diagnosed as location= itself being rejected — a store=<code>-per-
# official-store detour was built (and then reverted) as a result. Real
# screenshots later confirmed the true cause was a request-header mismatch
# (wrong Accept/Referer, missing x-skip-redirect), not the query param.
# With the headers fixed, location=<pincode> works for all 6 pincodes
# above AND any arbitrary /trackpickup pincode — no per-store internal
# Apple code needed at all.

# The official-store checker (run_apple_official_pickup_cycle) and
# /trackpickup (run_pickup_check_cycle) run on THIS separate, longer
# interval instead of the main CHECK_INTERVAL (see bot.py's
# stock_checker_loop) — real-world testing found APPLE_COOKIES' session
# was being invalidated (HTTP 541) after ~2 hours, and request VOLUME was
# identified as a likely contributing factor: on the old shared 5-minute
# CHECK_INTERVAL, even a single tracked Apple product generated ~144
# automated fulfillment-messages requests every 2 hours, arriving in
# tight parallel bursts (6 pincodes at once, every cycle). Running these
# on a longer, dedicated interval cuts total request volume roughly 3x at
# the default values below, without slowing down the main stock-check
# cadence for every other site/feature. Pickup-availability data going a
# few extra minutes stale is an acceptable tradeoff for meaningfully
# fewer automated hits against a cookie session already shown to be
# pattern-sensitive — see checkers/apple.py's check_pickup_at_official_
# stores for the complementary sequential-with-jitter + shared-connection
# changes (also part of reducing this endpoint's bot-like footprint).
# Reverted back to a fast 3-min default now that cookie freshness is handled
# by the Playwright-based auto-refresher below (real-browser-minted cookies,
# refreshed every APPLE_COOKIE_REFRESH_INTERVAL) rather than relying on one
# manually-pasted session surviving hours of use — see APPLE_COOKIE_REFRESH_*
# below and checkers/apple.py's get_apple_session_cookies-based lookup.
#
# Briefly raised 180 -> 900 (2026-07-28) when run_pickup_check_cycle/
# run_channel_forward_check_cycle were first wired to checkers.apple's
# page-render pickup check (each pincode check launches a real headful
# Chromium browser via playwright_scraper, up to ~90s worst case per
# check with retries) — at the time, Apple pickup checking still ran
# INSIDE bot.py's sequential stock_checker_loop, so a slow pickup cycle
# would pause every other site's regular checking for its whole duration;
# 900s was chosen to keep that pause infrequent.
#
# Reverted back to 180 the same day once Apple pickup checking (run_
# pickup_check_cycle, run_apple_official_pickup_cycle, run_channel_
# forward_pickup_check_cycle) was decoupled into its own fully
# independent, concurrent task (bot.py's apple_pickup_check_loop, started
# alongside stock_checker_loop in main()) — see that loop's own module
# note. A slow pickup cycle can no longer block anything else regardless
# of this interval's value, so the original fast cadence is safe again.
# Note this now behaves as a FLOOR, not a guaranteed cadence: apple_
# pickup_check_loop sleeps this long AFTER each pass finishes (same
# self-pacing pattern as APPLE_COOKIE_REFRESH_INTERVAL's loop) rather than
# subtracting elapsed time, so a pass that runs longer than this interval
# just pushes the next one out further — it never overlaps itself and
# never affects any other loop's own cadence.
APPLE_PICKUP_CHECK_INTERVAL = int(os.getenv("APPLE_PICKUP_CHECK_INTERVAL", "180"))  # 3 min default

# Croma's own dedicated check interval — same "isolate one site onto its own
# cadence" pattern as APPLE_PICKUP_CHECK_INTERVAL above (a next_croma_run
# timestamp gate in bot.py's stock_checker_loop, checked alongside
# next_apple_pickup_run). Croma's checker (checkers.croma.check_via_api)
# spends zero Zyte/Scrape.do credits — it calls Croma's own free inventory
# API directly — so running it more often than the shared CHECK_INTERVAL
# costs nothing extra in scraping-provider terms; this just lets Croma's
# stock data refresh faster than every other site without changing
# CHECK_INTERVAL itself.
CROMA_CHECK_INTERVAL = int(os.getenv("CROMA_CHECK_INTERVAL", "180"))  # 3 min default

# Feature-scoped alert gate — NOT config.UNRELIABLE_SITES, which would also
# suppress the two EXISTING, already-working Apple signals above (the
# generic checker + single-pincode refinement). This flag gates ONLY this
# new feature's own notifications: the check still RUNS every cycle either
# way (so accuracy can be watched via Railway logs / /debugapplestores),
# but send_pickup_alert is only actually called once this is explicitly
# turned on — mirrors how Croma's own rollout was gated (there, via
# UNRELIABLE_SITES; here, via a dedicated flag instead, since that
# mechanism is scoped to a whole SITE, not one feature within a site).
#
# Defaults to ENABLED during the current soft-launch/testing phase (real
# users aren't on the bot yet — only test accounts), so automatic alert
# behavior can be observed end-to-end rather than only inferred from logs.
# Flip back to "false" (or unset the env var) before onboarding real users
# until store-level accuracy has been manually verified against live data.
APPLE_OFFICIAL_PICKUP_ALERTS_ENABLED = os.getenv(
    "APPLE_OFFICIAL_PICKUP_ALERTS_ENABLED", "true"
).strip().lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Apple cookie auto-refresher (bot.py's apple_cookie_refresh_loop) — replaces
# manual DevTools cookie extraction + a Railway env var update with a
# periodic real-browser session, run by the separate playwright_scraper
# Railway service (POST /refresh-apple-cookies), since a genuine Chromium
# session carries a real, matching TLS fingerprint at the point Apple mints
# the cookies (httpx's own TLS fingerprint doesn't match a real browser's,
# which independently contributed to the ~2-hour session-death pattern that
# APPLE_PICKUP_CHECK_INTERVAL's pacing fix alone couldn't solve). The FAST
# per-check requests (check_pickup_at_official_stores, /trackpickup,
# refine_with_pincode) still go through plain httpx as before — Playwright
# only ever runs in this separate background service, never in that hot
# path. Refreshed cookies are stored in the main bot's own SQLite DB (see
# database.get_apple_session_cookies/set_apple_session_cookies) rather than
# an env var, so no Railway redeploy/env-var edit is needed to pick them up.
#
# Left unset by default — checkers/apple.py treats an empty
# PLAYWRIGHT_SCRAPER_URL as "feature not configured" (mirrors
# WHATSAPP_FORWARDER_URL's own convention) and the refresh loop simply never
# runs, so a deploy that hasn't set this up behaves exactly as before:
# APPLE_COOKIES/APPLE_USER_AGENT env vars, manually refreshed.
PLAYWRIGHT_SCRAPER_URL = os.getenv("PLAYWRIGHT_SCRAPER_URL", "").rstrip("/")
# Shared secret sent as the X-Internal-Token header on every refresh
# request; only enforced on the playwright_scraper side if IT also has this
# set (see playwright_scraper/main.py) — optional, since that service
# currently has no auth on any of its other endpoints either.
PLAYWRIGHT_SCRAPER_INTERNAL_TOKEN = os.getenv("PLAYWRIGHT_SCRAPER_INTERNAL_TOKEN", "")
# How often the background refresh runs. Real-world testing found Apple's
# session dying after ~2 hours even under light, paced request volume — this
# defaults to well inside that window with plenty of margin, not right up
# against it, since a slow/failed refresh cycle should never risk the stored
# session going stale before the next attempt.
APPLE_COOKIE_REFRESH_INTERVAL = int(os.getenv("APPLE_COOKIE_REFRESH_INTERVAL", "4500"))  # 75 min default
# Product page the refresher navigates to and the pincode it types into that
# page's pickup-availability check, to trigger a genuine fulfillment-messages
# request from inside the real browser session (establishing the session in
# the same way an actual visitor checking pickup availability would, not
# just a bare page load). Any real apple.com/in product buy page + valid
# Indian pincode works; these defaults need no override to function.
APPLE_COOKIE_REFRESH_PRODUCT_URL = os.getenv(
    "APPLE_COOKIE_REFRESH_PRODUCT_URL", "https://www.apple.com/in/shop/buy-iphone/iphone-17"
)
APPLE_COOKIE_REFRESH_PINCODE = os.getenv("APPLE_COOKIE_REFRESH_PINCODE", "400051")
# Retry-before-storing safety net (2026-07-27) — a refresh's own in-page
# validation (pincode_check_confirmed) turned out to be genuinely
# probabilistic, not deterministic: some cycles produce an
# Akamai-cleared session, others don't, even back-to-back. Rather than
# storing whatever the FIRST attempt produced regardless, bot.py's
# run_apple_cookie_refresh_cycle now retries up to this many total
# attempts, stopping early the moment one confirms — only falling back to
# storing an unconfirmed session (today's original behavior) if every
# attempt comes back unconfirmed. 3 total (1 + 2 retries) balances
# meaningfully improving the odds against each attempt already costing
# ~15-30s (full browser launch + dwell + fetch) and this cycle needing to
# comfortably finish within APPLE_COOKIE_REFRESH_INTERVAL.
APPLE_COOKIE_REFRESH_MAX_ATTEMPTS = int(os.getenv("APPLE_COOKIE_REFRESH_MAX_ATTEMPTS", "3"))
# Randomized delay BETWEEN retry attempts (not just relying on each
# attempt's own dwell time) — repeated rapid page loads from the same
# IP/session in a tight burst is itself a bot signal (see
# checkers.apple.check_pickup_at_official_stores's own pincode-delay
# reasoning, applied here for the same reason), so retrying too
# aggressively could make Akamai's assessment WORSE, not better.
APPLE_COOKIE_REFRESH_RETRY_DELAY_MIN_SECONDS = float(
    os.getenv("APPLE_COOKIE_REFRESH_RETRY_DELAY_MIN_SECONDS", "5")
)
APPLE_COOKIE_REFRESH_RETRY_DELAY_MAX_SECONDS = float(
    os.getenv("APPLE_COOKIE_REFRESH_RETRY_DELAY_MAX_SECONDS", "12")
)
