# Mangopickup

A standalone extraction of the Apple Store pickup-availability tracking
feature from Tracker-alert, kept as its own repo for easier editing. This
is a fully independent deployment: its own Telegram bot token, its own
SQLite database, its own `playwright_scraper` service — no shared state
with Tracker-alert.

## What's here

- `checkers/apple.py`, `checkers/common.py` — the Apple pickup checker
  (unchanged from Tracker-alert).
- `worker.py` — the background loops: `apple_pickup_check_loop` (which runs
  `run_pickup_check_cycle`, `run_apple_official_pickup_cycle`, and
  `run_channel_forward_pickup_check_cycle`) and `apple_cookie_refresh_loop`.
  Also the process entrypoint — registers `apple_admin_handlers.router` and
  starts polling.
- `apple_admin_handlers.py` — the `/debugpickup*`, `/debugzipcodevalidation`,
  `/debugpickupmessage*`, `/debugpickupstatus`, `/debugpickupevents` admin
  commands.
- `notifications.py` — `send_pickup_alert` / `send_channel_pickup_alert`
  (trimmed from Tracker-alert's notifications.py to just these two).
- `database.py`, `config.py`, `translations.py`, `zyte_client.py` — copied
  unchanged from Tracker-alert (shared infrastructure these modules need to
  import/run). `database.py` still defines every Tracker-alert table (users,
  plans, WhatsApp, regular stock tracking, etc.) even though this repo only
  reads/writes the pickup-related ones — copied whole rather than trimmed so
  the schema and pickup functions behave identically, per your call.
- `playwright_scraper/` — the browser-automation service the pickup checker
  and cookie refresher call over HTTP. Deploy as its own Railway service.

## Known gap: nothing here ADDS tracking rows yet

The commands that let a user populate the tables these loops check —
`/trackpickup` (personal `pickup_tracking` rows), `/add` (the `products`
table `run_apple_official_pickup_cycle` scans for `site="apple"` rows), and
`/addchannelpickup` (`channel_forward_pickup_tracking`) — all live in
Tracker-alert's `handlers.py`/`admin_handlers.py` and were **not** part of
the requested copy list (only the `/debugpickup*` diagnostic commands were).

As deployed right now, `worker.py`'s loops will run on schedule and simply
find empty tables — they won't alert on anything until something inserts
rows into `pickup_tracking`, `products`, or `channel_forward_pickup_tracking`.
You'll want to either port over `/trackpickup`/`/add`/`/addchannelpickup`
next, or insert rows some other way, before this is useful end-to-end.

## Railway services to create

1. **mangopickup-worker** — this repo's root, `nixpacks` builder (already
   configured via `railway.toml`), `python worker.py`. Needs a persistent
   volume mounted at the directory containing `DB_PATH` (default
   `/app/data`).
2. **mangopickup-playwright-scraper** — point Railway at the
   `playwright_scraper/` subfolder (Dockerfile-based, already configured via
   `playwright_scraper/railway.toml`). Give the worker service's
   `PLAYWRIGHT_SCRAPER_URL` this service's public Railway URL.

## Environment variables

See `.env.example` for the full list with defaults/notes. In short:

- `BOT_TOKEN` — a **new** Telegram bot token, dedicated to this service
  (not Tracker-alert's token).
- `ADMIN_USER_ID` — your Telegram user id, gates the `/debugpickup*` commands.
- `DB_PATH` — this repo's own SQLite file (fresh, empty to start).
- `SCRAPING_PROVIDER` + `ZYTE_API_KEY` (or `SCRAPEDO_KEY`) — for fetching
  Apple product pages (SKU extraction, etc.) outside the pickup-availability
  check itself.
- `APPLE_PICKUP_PINCODES`, `APPLE_OFFICIAL_PICKUP_ALERTS_ENABLED`,
  `APPLE_PICKUP_CHECK_INTERVAL` — official-store auto-check tuning.
- `PLAYWRIGHT_SCRAPER_URL`, `PLAYWRIGHT_SCRAPER_INTERNAL_TOKEN` — must point
  at your `mangopickup-playwright-scraper` service; the token must match
  that service's own `INTERNAL_REFRESH_TOKEN`.
- `APPLE_COOKIE_REFRESH_*` — cookie auto-refresher tuning.
- `APPLE_COOKIES`, `APPLE_USER_AGENT` — manual fallback session, used only
  until the auto-refresher stores its first DB session.
