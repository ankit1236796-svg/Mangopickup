# playwright_scraper

Self-hosted Playwright + Chromium service with two independent jobs:

1. **iQOO/Vivo stock-check pilot** — a self-hosted alternative to their
   current Scrape.do `render=true` checks (which burn render credits at
   scale), behind an optional metered residential proxy. **Not wired into
   the main bot** — `checkers/iqoo.py`/`checkers/vivo.py` (Scrape.do-based)
   are completely untouched and remain live in production. This part
   exists purely for you to test standalone — hit `/check-stock` directly,
   compare its results against real stock status, and decide whether/how
   to integrate it later. If it crashes, gets blocked, or a proxy runs dry,
   nothing about the main bot changes.
2. **Apple cookie auto-refresher — IS wired into the main bot.**
   `/refresh-apple-cookies` is called on a timer by the main bot's own
   `bot.py` (`apple_cookie_refresh_loop`, every `APPLE_COOKIE_REFRESH_
   INTERVAL`, default 75 min) to replace manually-pasted `APPLE_COOKIES`/
   `APPLE_USER_AGENT` Railway env vars with a periodic real-browser
   session. See "Apple cookie auto-refresh" below. If this fails or the
   service is down, the main bot just keeps using whatever Apple session
   it already has (or the env var fallback) — never fatal.

## Deploying on Railway

Same pattern as `whatsapp_forwarder/`:

1. **New Service → GitHub Repo** → same repo, **Root Directory**
   `playwright_scraper/`.
2. **Builder: Dockerfile** (ships its own Dockerfile based on Playwright's
   official image — has Chromium and every system dependency already
   installed; Railway's default nixpacks builder does not).
3. No volume needed — this service is fully stateless.
4. Environment variables (all optional, sensible defaults):
   - `MAX_CONCURRENT_CHECKS` (default `2`) — how many browser instances may
     run at once. Each headless Chromium instance can use 150-300MB+ RAM;
     this bounds total memory use under concurrent load. Requests beyond
     the limit queue for a free slot (up to `SLOT_WAIT_TIMEOUT_SECONDS`,
     default 60s) rather than spawning unbounded browsers.
   - `MAX_RETRIES` (default `3`) — retry attempts before giving up and
     returning a "check failed" result (`in_stock: null`), never a guessed
     `false`.
   - `RETRY_DELAY_SECONDS` (default `2`) — pause between retry attempts.
   - `NAV_TIMEOUT_MS` (default `20000`) — page navigation timeout.
   - `SIGNAL_WAIT_TIMEOUT_MS` (default `8000`) — how long to wait for the
     primary stock signal (a JSON-LD `<script>` tag) to appear before
     proceeding anyway with whatever HTML rendered (fallback signals still
     run against it).
   - `PLAYWRIGHT_HEADLESS` (default `true`) — leave as default on Railway.
   - `PROXY_HOST`, `PROXY_PORT`, `PROXY_USERNAME`, `PROXY_PASSWORD` —
     Webshare (or any HTTP-auth proxy) credentials. **All optional** — with
     `PROXY_HOST`/`PROXY_PORT` unset, requests go out directly, so you can
     test this locally or on Railway before buying a proxy plan.
   - `INTERNAL_REFRESH_TOKEN` (default unset = no auth) — shared secret
     required as an `X-Internal-Token` header on `/refresh-apple-cookies`
     only. Set this (and the matching `PLAYWRIGHT_SCRAPER_INTERNAL_TOKEN`
     env var on the **main bot's** Railway service) once this handles real
     session cookies, so a stray public request can't trigger cookie
     refreshes or read back a session.
   - `APPLE_REFRESH_NETWORKIDLE_TIMEOUT_MS` (default `8000`),
     `APPLE_REFRESH_DWELL_MS` (default `5000`),
     `APPLE_REFRESH_POST_FETCH_WAIT_MS` (default `2000`) — timing knobs for
     `/refresh-apple-cookies` only, added after a live refresh's session
     died in ~9 minutes with Akamai Bot Manager's own marker cookies
     (`_abck`/`bm_sz`/`ak_bmsc`) completely absent — see "Apple cookie
     auto-refresh" below for the full story. Tune these if
     `akamai_markers_present` still comes back all-`false` in the logs.
5. On the **main bot's** own Railway service, set `PLAYWRIGHT_SCRAPER_URL`
   to this service's URL (its public `https://<service>.up.railway.app` or
   Railway's private-networking address, e.g.
   `http://playwright-scraper.railway.internal:8080`) to enable the Apple
   cookie auto-refresher — see "Apple cookie auto-refresh" below. Left
   unset, the main bot behaves exactly as before (manual `APPLE_COOKIES`/
   `APPLE_USER_AGENT` env var refresh only).

## HTTP surface

```
POST /check-stock
Body: {"url": "<product url>", "store": "iqoo" | "vivo"}
Response: {
  "url": "...", "store": "iqoo",
  "in_stock": true | false | null,   // null = check failed, see "signal"
  "signal": "JSON-LD offers.availability='https://schema.org/InStock'",
  "attempts": 1
}
```

```
POST /debug-network
Body: {"url": "<product url>", "pincode": "110001"}   // pincode optional
Response: {
  "url": "...", "pincode": "110001",
  "matched_requests": [
    {"url": "https://.../api/serviceability?pin=110001", "method": "GET",
     "status": 200, "body": "{\"serviceable\":true,...}"},
    ...
  ],
  "total_requests_seen": 12,     // every XHR/fetch response observed, for context
  "matched_count": 2,            // how many matched the capture keywords
  "all_responses_seen": [        // lightweight (no body) list of EVERY response,
    {"url": "...", "status": 200, "resource_type": "document"}, ...   // capped at 100
  ],
  "all_responses_truncated": false,
  "diagnostics": {
    "goto_status": 200, "goto_error": null,        // page.goto()'s own result/exception
    "final_url": "...", "page_title": "...",        // where navigation actually ended up
    "page_crashed": false,
    "networkidle_timed_out": false, "networkidle_error": null,
    "html_length": 45213, "html_snippet": "<!DOCTYPE html>...",
    "response_listener_errors": 0
  }
}
```
No auth on this endpoint (matches `/check-stock` — this whole service has
none, by design, since it's an internal pilot). Applies `pincode` as a
cookie named `pincode` on the target domain before navigating, then records
every XHR/fetch response whose URL contains `serviceability`, `delivery`,
`pincode`, `availability`, `stock`, or `fulfillment` (case-insensitive).
Built for `/debugreliance` on the main-bot side (RelianceDigital's stock
signal appears to live behind a pincode-gated API call rather than in the
page's own embedded JSON), but works against any URL.

**A live run against two real RelianceDigital URLs came back with
`total_requests_seen: 1` for both** — only the document itself, no
scripts/XHR at all, which pointed at either a silently swallowed
navigation error or an anti-bot challenge page being served instead of the
real site. Two things changed in response:
1. **Anti-detection measures** were added to every browser this service
   launches (not just this endpoint) — a realistic desktop Chrome
   user-agent, a normal 1280×800 viewport, and an init script patching the
   standard headless-Chromium tells (`navigator.webdriver`, `window.chrome`,
   `navigator.plugins`, `navigator.languages`). Vanilla headless Chromium is
   commonly fingerprinted and served a stripped-down page instead of the
   real one; this exact symptom (near-empty response, minimal further
   activity) was already confirmed and fixed the same way for
   `whatsapp_forwarder`'s WhatsApp Web automation.
2. **Every step that could silently fail is now caught and reported** in
   `diagnostics` instead of just producing a suspiciously low count with no
   explanation: `page.goto()`'s own error (if any) and HTTP status, the
   final URL after any redirects, the page title, whether the page crashed,
   whether the network-idle wait timed out or raised, and the first 500
   chars of whatever HTML actually loaded. `all_responses_seen` lists every
   response observed (not just keyword matches) so you can see exactly what
   *did* load even when nothing matched the capture keywords.

**The pincode-as-cookie approach is still a best-effort guess**, not a
confirmed mechanism — this sandbox has no live network access to check how
RelianceDigital's frontend actually reads a selected pincode (a cookie is
the most common convention, matching what the main bot's own quick-commerce
checkers already do, but it could instead be `localStorage`, a request
header, or something only set after a UI interaction like typing into a
pincode widget and clicking a button). If `matched_count` still comes back
0 despite `total_requests_seen` now being a realistic number, check
`all_responses_seen` for anything serviceability-shaped that just didn't
match the keyword list, and `diagnostics.page_title`/`html_snippet` for
signs of a login wall, captcha, or region-redirect page. Report back what's
actually observed so the approach can be adjusted, same as every other
live-tuning step this pilot has needed.

**Update: for RelianceDigital specifically, `diagnostics.goto_status` came
back `403` on both product URLs** — not a page-fingerprint issue the
anti-detection measures above could fix, but an Akamai WAF block on
Railway's outbound IP itself, at the network edge, before the page (real
or challenge) is even served. Direct-Playwright-from-Railway is a dead end
for this specific site; RelianceDigital checks have gone back to
Scrape.do (whose proxy pool, at least with `super=true`, gets past this
block — see the main bot's `/debugreliance <url> [pincode]` admin command
and `checkers/reliancedigital.py`). `/debug-network` itself is unchanged
and still useful for other sites/diagnostics that aren't behind an
IP-level WAF block like this one.

```
POST /refresh-apple-cookies
Body: {"url": "https://www.apple.com/in/shop/buy-iphone/iphone-17", "pincode": "400051"}
Header (only if INTERNAL_REFRESH_TOKEN is set): X-Internal-Token: <token>
Response: {
  "url": "...", "pincode": "400051",
  "cookies": "dslang=US-EN; site=USA; s_vi=...; ...",   // semicolon-joined, ready as a Cookie header
  "user_agent": "Mozilla/5.0 (Windows NT 10.0; ...) Chrome/128.0.0.0 Safari/537.36",
  "pincode_check_confirmed": true,   // fulfillment-messages returned 200 + valid JSON
  "diagnostics": {
    "goto_status": 200, "goto_error": null,
    "networkidle_timed_out": false, "networkidle_error": null,
    "sku_extracted": "MG6M4HN/A",
    "fulfillment_url": "https://www.apple.com/in/shop/fulfillment-messages?...",
    "fulfillment_status": 200, "fulfillment_error": null,
    "cookie_names": ["_abck", "bm_sz", "dslang", "site", "..."],
    "akamai_markers_present": {"_abck": true, "bm_sz": true, "ak_bmsc": false}
  }
}
```
See `_refresh_apple_cookies` in `main.py` for the full mechanism. Loads the
product page, extracts its SKU (same `"partNumber":"..."` pattern
`checkers/apple.py` uses), then runs `fetch(fulfillment_url, {credentials:
'include'})` **from inside the page's own JS context** via
`page.evaluate()` — same-origin (apple.com calling apple.com), so no CORS
issue, and it carries the real browser's TLS fingerprint/headers/cookies
exactly as if Apple's own pickup-availability widget had triggered it.
Deliberately does NOT try to click through that widget's actual DOM (an
unconfirmed "See availability" link → pincode input → submit sequence) —
reproducing the underlying request directly is equivalent and doesn't
depend on guessed CSS selectors that could silently break on a page
redesign. `cookies`/`user_agent` are still returned even when
`pincode_check_confirmed` is `false` (e.g. SKU extraction failed) — the
page load alone still mints real session cookies, which may still be
usable; the caller decides whether an unconfirmed refresh is worth storing
(the main bot currently stores it either way — see `bot.py`'s
`run_apple_cookie_refresh_cycle` — since even an unconfirmed session is
never worse than the previous one or the env var fallback).

## Apple cookie auto-refresh

Replaces the main bot's old workflow (open DevTools in a real browser,
manually copy the `Cookie` header + `User-Agent`, paste into the
`APPLE_COOKIES`/`APPLE_USER_AGENT` Railway env vars, repeat every time the
session dies) with a fully automated loop:

1. Every `APPLE_COOKIE_REFRESH_INTERVAL` (default 75 min), the main bot's
   `bot.py` (`apple_cookie_refresh_loop`) calls this service's
   `/refresh-apple-cookies`.
2. This service launches a real headless-Chromium session, loads
   `APPLE_COOKIE_REFRESH_PRODUCT_URL` (default the iPhone 17 buy page), and
   triggers a genuine fulfillment-messages request for
   `APPLE_COOKIE_REFRESH_PINCODE` from inside that page — see the endpoint
   doc above.
3. On success, the main bot stores the returned cookies + User-Agent in its
   own SQLite DB (`database.set_apple_session_cookies`) — not an env var,
   so no Railway redeploy is needed to pick up a refresh.
4. The main bot's actual per-check requests (`checkers/apple.py`'s
   `check_pickup_at_official_stores`, `/trackpickup`, `refine_with_
   pincode`) read from that DB-stored session first
   (`_resolve_apple_session`), falling back to the `APPLE_COOKIES`/
   `APPLE_USER_AGENT` env vars only if the DB has never been populated (a
   fresh deploy before the first successful refresh) or the refresher is
   failing. Those checks are **unchanged otherwise** — still plain httpx,
   no Playwright in that hot path, so per-check speed stays ~1s.

Why a real browser mints the cookies but a fast library still uses them:
`httpx`'s TLS fingerprint doesn't match a real browser's TLS stack, which
was one of the suspected contributors (alongside request volume/burstiness)
to a manually-pasted session dying after ~2 hours even under paced,
sequential request traffic. A genuine Chromium session establishes cookies
with a real, matching fingerprint at the moment Apple issues them — but
there's no need to pay Playwright's per-request overhead (~5-30s vs.
httpx's ~1s) for every stock check just to keep that fingerprint consistent
on every single request; only the moment of cookie *acquisition* needs a
real browser, not every use of the resulting cookies afterward.

**Update — the initial version of this refresh flow wasn't slow enough.**
A live run confirmed the session it produced died in ~9 minutes, faster
than the ~2-hour manually-pasted-cookie pattern it replaced. Diagnostic
logging (`akamai_markers_present` in `_refresh_apple_cookies`'s
diagnostics, checking for Akamai Bot Manager's own `_abck`/`bm_sz`/
`ak_bmsc` marker cookie names) showed all three completely absent — the
session was never validated by Akamai's own sensor/telemetry JS at all,
because the original flow captured cookies and closed the browser within
a fraction of a second of loading the page, with images/fonts/stylesheets
blocked. The flow now:
- waits for `networkidle` (bounded, `APPLE_REFRESH_NETWORKIDLE_TIMEOUT_MS`,
  default 8s — best-effort, a timeout just proceeds anyway, same as
  `/debug-network`'s own networkidle wait; this is deliberately NOT the
  `page.goto()`'s own `wait_until`, since a hard networkidle gate risks the
  navigation itself timing out on a page with any ongoing background
  activity),
- dwells for `APPLE_REFRESH_DWELL_MS` (default 5s) BEFORE firing the
  fulfillment-messages fetch, not just before reading cookies,
- loads every subresource the real page would (no resource blocking for
  this flow specifically — `/check-stock` and `/debug-network` are
  unaffected, still blocked as before),
- and dwells again for `APPLE_REFRESH_POST_FETCH_WAIT_MS` (default 2s)
  after the fetch before the browser closes.

This makes a refresh noticeably slower (roughly 15-20s+ versus the
original ~1-3s) — acceptable since it only runs once per
`APPLE_COOKIE_REFRESH_INTERVAL` (default 75 min), well within the main
bot's 90s client timeout for this call. Check `akamai_markers_present` in
the logs after a refresh to confirm `_abck` (at minimum) now comes back
`true` before trusting the resulting session.

```
GET /health
Response: {"ok": true, "max_concurrent_checks": 2, "proxy_configured": false,
           "supported_stores": ["iqoo", "vivo"],
           "apple_cookie_refresh_auth_required": false}
```

Examples:
```bash
curl -X POST https://<your-service>.up.railway.app/check-stock \
  -H "Content-Type: application/json" \
  -d '{"url": "https://mshop.iqoo.com/in/product/...", "store": "iqoo"}'

curl -X POST https://<your-service>.up.railway.app/debug-network \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.reliancedigital.in/...", "pincode": "110001"}'

curl -X POST https://<your-service>.up.railway.app/refresh-apple-cookies \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: <token, only if INTERNAL_REFRESH_TOKEN is set>" \
  -d '{"url": "https://www.apple.com/in/shop/buy-iphone/iphone-17", "pincode": "400051"}'
```

## Bandwidth optimization

Every request is intercepted (`page.route("**/*", ...)`) and only
`document`, `script`, `xhr`, and `fetch` resource types are allowed through
— images, fonts, stylesheets, and media are aborted before they download.
A product page's images alone can be several MB; since only `page.content()`
(the rendered DOM) is ever read, none of that is needed. Each check logs how
many requests were allowed vs. blocked, so the actual savings are visible in
the logs rather than assumed. `/debug-network` reuses this same filter —
`xhr`/`fetch` responses are exactly what it needs to inspect, so nothing
extra had to be allowed through for it.

## Stock detection — ported, not freshly reverse-engineered

This sandbox has no live network access to inspect real iQOO/Vivo product
pages. Rather than guess new selectors blind, `check_iqoo_vivo_stock()` in
`main.py` is **ported verbatim** from `checkers/iqoo.py` and
`checkers/vivo.py` in the main bot — both already probe-confirmed reliable
(a prior diagnostic pass tested real in-stock and out-of-stock URLs for
both stores): JSON-LD `offers.availability` is the primary signal, an
embedded-JSON stock key is a fallback, explicit "out of stock"/"sold out"
text is a last resort.

**This needs live verification once deployed** — the signal was proven
reliable when fetched via Scrape.do's `render=true`; it should behave the
same via Playwright (both fully execute the page's JS before reading the
DOM), but that's an assumption, not a confirmed fact from this environment.
Test both an in-stock and an out-of-stock URL for each store and compare
`/check-stock`'s result + `signal` field against ground truth before trusting
it for anything real.

One deliberate difference from the main bot's checkers: `checkers/iqoo.py`/
`vivo.py` default to `False` (out of stock) when no signal is found at all,
reasoning that a missed alert is safer than a false one in production. This
pilot instead returns `in_stock: null` ("check failed") in that case — since
this is a service being actively tuned, an inconclusive read should surface
for investigation rather than silently reporting "out of stock" as if it
were confident.

## Local testing (no proxy, no real site — HTTP layer + logic only)

```bash
cd playwright_scraper
pip install -r requirements.txt
python main.py
# in another terminal:
curl localhost:8080/health
```

For end-to-end testing against real iQOO/Vivo URLs, deploy to Railway (or
run locally with a real Chromium + network access) and hit `/check-stock`
directly with real product URLs — I cannot do this from the sandbox that
built this service.
