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
  Also the process entrypoint — registers both routers below and starts
  polling.
- `apple_admin_handlers.py` — admin-only commands (router filtered to
  `ADMIN_USER_ID`): `/debugpickup*`, `/debugzipcodevalidation`,
  `/debugpickupmessage*`, `/debugpickupstatus`, `/debugpickupevents`,
  `/addchannelpickup`, and `/setchannel` (added beyond the original request —
  see the file's own docstring for why).
- `pickup_handlers.py` — user-facing commands: `/trackpickup`, `/mypickups`,
  `/untrackpickup` (unchanged from Tracker-alert), and `/add` — a
  **minimal, apple.com-only** version (see the file's own docstring): just
  validates the URL and inserts it into `database.products` with
  `site="apple"`. Tracker-alert's real `/add` also pulls in the whole
  plan/trial/item-limit system, bulk-add, and an Amazon target-price
  sub-flow — none of that exists here, by design.
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

## Not included

`/stopforwardingpickup`, `/listforwarding`, `/setchannelpincode` — useful
for managing/inspecting channel-forward pickup rows once they exist, but
not required for `/addchannelpickup` itself to work, so left out. `/list`
and `/remove` for the plain `/add`-tracked apple.com products also aren't
here — there's currently no way to view or delete a row added via `/add`
short of direct DB access.

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
