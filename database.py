import json
import os
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from config import DB_PATH, SHARE_TRIAL_ROUNDS_REQUIRED, ADMIN_USER_ID
# Single source of truth for valid language codes lives in translations.LANGS
# — imported rather than duplicated here so the two can never drift.
from translations import LANGS as _VALID_LANGS

logger = logging.getLogger(__name__)

# India Standard Time (UTC+5:30). SQLite's datetime('now') returns UTC, which
# was being stored and shown to users verbatim — 5:30 behind local time.
IST = timezone(timedelta(hours=5, minutes=30))


def now_ist_str() -> str:
    """Current time in IST as a 'YYYY-MM-DD HH:MM:SS' string for storage."""
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def parse_ist(raw: str) -> datetime:
    """Parse a stored 'YYYY-MM-DD HH:MM:SS' IST string back into an aware datetime."""
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)


def get_connection() -> sqlite3.Connection:
    # WAL + busy_timeout added so the bot's asyncio thread and the admin
    # dashboard's Flask worker threads can read/write the same SQLite file
    # concurrently without intermittent "database is locked" errors. WAL lets
    # readers and a writer coexist; busy_timeout makes a blocked writer wait
    # (up to 10s) for a lock instead of erroring immediately. Each caller still
    # opens its own connection and uses it within one thread, so the default
    # check_same_thread stays safe. journal_mode=WAL persists at the DB level;
    # re-asserting it per connection is cheap and harmless.
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db():
    """Initialize the database schema, creating the data directory if needed."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                name         TEXT    NOT NULL,
                url          TEXT    NOT NULL,
                site         TEXT    NOT NULL,
                in_stock     INTEGER NOT NULL DEFAULT 0,
                last_checked TEXT,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, url)
            )
        """)
        # Migration: add target_price column for Amazon price-gated alerts.
        # ALTER TABLE silently fails on older DBs that already have the column.
        try:
            conn.execute("ALTER TABLE products ADD COLUMN target_price REAL")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

        # Migration: RelianceDigital's internal inventory-API "article_id",
        # auto-extracted from the live product page once at /add time (see
        # checkers/reliancedigital.py's fetch_and_extract_article_id and
        # handlers.py's _resolve_reliance_article_id) rather than scraped on
        # every check cycle. NULL means extraction failed or hasn't run for
        # this row (e.g. every non-reliancedigital product, or one added
        # before this migration) — stock_checker.check_stock() treats a
        # missing article_id as inconclusive (returns None), same as
        # checkers/croma.py does for a missing itemID.
        try:
            conn.execute("ALTER TABLE products ADD COLUMN reliance_article_id TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_products_user_id ON products(user_id)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pin_codes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                pin_code   TEXT    NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, pin_code)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pin_codes_user_id ON pin_codes(user_id)
        """)

        # ── Plans (admin-configurable, no code changes needed to adjust) ─────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL UNIQUE,
                price         REAL    NOT NULL,
                max_items     INTEGER NOT NULL,
                sites         TEXT    NOT NULL DEFAULT 'all',
                is_trial_plan INTEGER NOT NULL DEFAULT 0,
                is_active     INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migration: add default_duration_days so a plan carries its own
        # billing period. This pre-fills the "days" field on /approve and the
        # dashboard approve/extend forms, so the admin no longer has to
        # remember and retype each plan's duration every time. Defaults to 30
        # for every existing plan; the seed below sets it explicitly for new DBs.
        try:
            conn.execute("ALTER TABLE plans ADD COLUMN default_duration_days INTEGER NOT NULL DEFAULT 30")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        # Seed default plan once; idempotent (INSERT OR IGNORE on UNIQUE name).
        # is_trial_plan=1 makes this the plan whose limits apply during the
        # free trial by default — admin can move that flag to a different
        # plan later via /editplan without any code change.
        existing_plan = conn.execute("SELECT 1 FROM plans WHERE name = 'Standard'").fetchone()
        if not existing_plan:
            conn.execute(
                """
                INSERT INTO plans (name, price, max_items, sites, is_trial_plan, default_duration_days, created_at)
                VALUES ('Standard', 999, 20, 'all', 1, 30, ?)
                """,
                (now_ist_str(),),
            )

        # ── Users (access/subscription state) ─────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id             INTEGER PRIMARY KEY,
                username            TEXT,
                first_name          TEXT,
                plan_id             INTEGER,
                is_trial            INTEGER NOT NULL DEFAULT 1,
                access_until        TEXT,
                blocked             INTEGER NOT NULL DEFAULT 0,
                reminder_sent_until TEXT,
                created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (plan_id) REFERENCES plans(id)
            )
        """)
        # Migration: add share_trial_used flag for the WhatsApp-share-gated
        # free trial (see /freetrial in handlers.py) — one claim per account.
        try:
            conn.execute("ALTER TABLE users ADD COLUMN share_trial_used INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

        # Migration: add share_trial_rounds_done counter for the 5-round
        # share+confirm cycle /freetrial requires before the bonus can be
        # claimed. Stored in the DB (not FSM/in-memory state) so progress
        # survives a bot restart or the user taking a break between shares.
        try:
            conn.execute("ALTER TABLE users ADD COLUMN share_trial_rounds_done INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

        # Migration: add share_trial_requested flag. Completing the 5-round
        # share+confirm cycle no longer auto-grants the trial — it creates a
        # pending request the admin must approve (see request_share_trial),
        # so /freetrial has the same manual-approval checkpoint as any other
        # new user. Cleared by grant_access/reject_user once the admin acts.
        try:
            conn.execute("ALTER TABLE users ADD COLUMN share_trial_requested INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

        # Migration: add per-user language preference for the /language feature
        # (see translations.LANGS for the full supported set). Defaults to
        # 'en' so existing users are unchanged until they pick a language.
        try:
            conn.execute("ALTER TABLE users ADD COLUMN lang TEXT NOT NULL DEFAULT 'en'")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

        # Migration: per-user "pause checking" flag for the admin Pause/Resume
        # Service feature (/pauseservice, /resumeservice — see
        # set_users_checks_paused/list_paused_user_ids below). A paused
        # user's tracked items stay saved in the DB exactly as before — only
        # the background check cycle skips them (no Scrape.do/Zyte API calls
        # for their products), and NO notification is sent to them (silent,
        # admin-only visibility, per the feature's design). Distinct from
        # `blocked` (access.py's STATUS_LOCKED — that also blocks the user
        # from using the bot at all); pausing only stops background checks,
        # the user's own /check still works normally.
        try:
            conn.execute("ALTER TABLE users ADD COLUMN checks_paused INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            conn.execute("ALTER TABLE users ADD COLUMN checks_paused_at TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_checks_paused ON users(checks_paused)
        """)

        # ── Service status (GLOBAL pause/resume — a singleton row) ────────────
        # A single on/off switch for the ENTIRE background check cycle,
        # separate from the per-user checks_paused flag above (see
        # bot.py's run_stock_check_cycle: when paused=1 here, the cycle
        # returns immediately — no get_all_products() call, no grouping, no
        # Scrape.do/Zyte requests at all — rather than iterating every user
        # and skipping each one individually, which would still cost a DB
        # read per cycle for no benefit). paused_at is the IST timestamp of
        # the most recent pause; cleared back to NULL on resume.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS service_status (
                id        INTEGER PRIMARY KEY CHECK (id = 1),
                paused    INTEGER NOT NULL DEFAULT 0,
                paused_at TEXT
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO service_status (id, paused, paused_at) VALUES (1, 0, NULL)"
        )

        # ── Approvals (full approve/reject/extend/block/unblock audit trail) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                plan_id    INTEGER,
                days       INTEGER,
                amount     REAL,
                action     TEXT    NOT NULL,
                reason     TEXT,
                admin_id   INTEGER NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_approvals_user_id ON approvals(user_id)
        """)

        # ── Site locks (admin store/feature restriction) ─────────────────────
        # A lock disables a store either globally or for one specific user.
        #   user_id IS NULL  → global lock: nobody may /add that store, and
        #                      automatic alerts for it are suppressed for all.
        #   user_id = <id>   → per-user lock: only that user is blocked from it.
        # This is the dashboard-configurable equivalent of the code-level
        # UNRELIABLE_SITES / SUPPORTED_SITES edits used to pull Croma/Zepto —
        # no redeploy needed. Two partial unique indexes keep it idempotent:
        # SQLite treats NULLs as distinct, so a plain UNIQUE(user_id, site)
        # wouldn't stop duplicate global rows — hence the split indexes.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS site_locks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                site       TEXT    NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_site_locks_global
            ON site_locks(site) WHERE user_id IS NULL
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_site_locks_user
            ON site_locks(user_id, site) WHERE user_id IS NOT NULL
        """)

        # ── Zyte API usage log (proxy-metric cost tracking) ───────────────────
        # One row per fetch actually served by Zyte API (see checkers/common.py's
        # fetch_page, zyte_client.py). Zyte's own /v1/extract response does NOT
        # include a per-request dollar-cost field — confirmed via documentation
        # research (docs.zyte.com/zyte-api/usage/reference.html's response
        # field list has no "cost"/"billing" field; that data only exists in a
        # SEPARATE Stats API requiring its own authenticated call) and via
        # /debugzyte's "raw" mode, which dumps a real response for a human to
        # double-check. So cost here is an ESTIMATE derived from response_bytes
        # + render_js (Zyte's own published pricing splits HTTP vs
        # browser-rendered requests into very different price bands — browser
        # rendering costs roughly 5-15x more per request) — see
        # get_zyte_usage_summary's cost-range calculation. site is nullable:
        # ad-hoc/debug fetches (e.g. /debugzyte on an arbitrary URL) that have
        # no tracked-product "site" concept are logged under NULL, grouped
        # separately from real per-store production traffic.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS zyte_usage_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                site           TEXT,
                render_js      INTEGER NOT NULL DEFAULT 0,
                response_bytes INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migration: add actions_used — whether this request included a
        # playWithBrowser/Zyte "actions" chain (click/type/wait...), tracked
        # separately from render_js since actions add extra cost/latency on
        # TOP of the browser tier they require, not just the browser tier
        # itself. Defaults to 0 for every pre-existing row.
        try:
            conn.execute("ALTER TABLE zyte_usage_log ADD COLUMN actions_used INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_zyte_usage_log_site ON zyte_usage_log(site)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_zyte_usage_log_created_at ON zyte_usage_log(created_at)
        """)

        # ── WhatsApp channel forwarding (per-user, admin-approved) ────────────
        # Each user may register their OWN WhatsApp Channel/Community invite
        # link; once the admin manually joins it and approves the registration
        # here, "back in stock" alerts for that user ALSO forward to their
        # channel (see whatsapp_client.py). status:
        #   pending  → registered, awaiting admin approval (not yet forwarding)
        #   active   → approved, alerts forward to invite_link
        #   disabled → admin revoked; kept (not deleted) so the user's link and
        #              history aren't lost if re-enabled later
        # One row per user (a user has exactly one registered channel at a time
        # — re-running /setwhatsapp overwrites it and resets to pending).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_channels (
                user_id            INTEGER PRIMARY KEY,
                invite_link        TEXT    NOT NULL,
                status             TEXT    NOT NULL DEFAULT 'pending',
                registered_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                approved_at        TEXT,
                approved_by_admin  INTEGER
            )
        """)
        # Migration: add group_name so the forwarder can open a chat by
        # searching WhatsApp Web's own sidebar (reliable — the account is
        # already a member) instead of always navigating to the invite link
        # fresh (unreliable — WhatsApp sometimes shows an interstitial
        # landing page there instead of the chat). Nullable: resolved
        # best-effort during admin approval via the forwarder's
        # /resolve-name endpoint; forwarding falls back to invite-link
        # navigation whenever this is empty. See whatsapp_forwarder/main.py.
        try:
            conn.execute("ALTER TABLE whatsapp_channels ADD COLUMN group_name TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.commit()

        # ── Apple Store pickup-availability tracking (separate from `products` —
        # see checkers/apple.py's pickup section + bot.run_pickup_check_cycle) ──
        # One row per /trackpickup call: a product (URL + cached SKU, extracted
        # once at track-time so the recurring check cycle only needs to call the
        # fulfillment-messages API, no repeat product-page fetch) plus its list
        # of tracked pincodes. `pincodes` is a JSON-encoded list of strings;
        # `pincode_status` is a JSON-encoded {pincode: 0/1} dict of the last
        # known "any store showed pickup-available" result per pincode, used
        # solely to detect the unavailable→available transition the feature
        # alerts on (see bot.py) — never as a stock-status source of truth the
        # way `products.in_stock` is.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pickup_tracking (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                name           TEXT    NOT NULL,
                url            TEXT    NOT NULL,
                sku            TEXT    NOT NULL,
                pincodes       TEXT    NOT NULL,
                pincode_status TEXT    NOT NULL DEFAULT '{}',
                created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, url)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pickup_tracking_user_id ON pickup_tracking(user_id)
        """)
        conn.commit()

        # ── Durable pickup-alert event log (2026-07-29) ────────────────────────
        # Built after a real "genuine availability confirmed multiple times,
        # zero alerts received" report where every candidate root cause
        # (silent exception in send_pickup_alert, is_site_locked/
        # is_forwarding_paused suppression persisting pincode_status=True
        # regardless of whether the alert sent, an unguarded update_pickup_
        # status write) was invisible from the outside — Railway logs aren't
        # accessible, and a DB-status snapshot (/debugpickupstatus) only shows
        # CURRENT state, which self-erases if a pincode's availability later
        # flips back to False. This table is the durable record of what
        # actually happened at each step, independent of both: every True
        # transition, every alert attempt's outcome (sent/suppressed/error),
        # and every fetch/persist exception in check_pickup_row/check_
        # channel_pickup_row/the official-store pickup cycle gets a row here,
        # queryable later via /debugpickupevents even after the moment (and
        # any transient DB-status evidence of it) has passed.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pickup_alert_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT    NOT NULL DEFAULT (datetime('now')),
                source     TEXT    NOT NULL,
                row_id     INTEGER,
                pincode    TEXT,
                event      TEXT    NOT NULL,
                detail     TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pickup_alert_log_ts ON pickup_alert_log(ts)
        """)
        conn.commit()

        # ── Apple official-store pickup auto-check (separate feature/table
        # from pickup_tracking above — see checkers/apple.py's "official-
        # store pickup checker" module note + bot.run_apple_official_
        # pickup_cycle). Keyed by URL (not product_id/user_id): the 6
        # official-store pincodes are FIXED/global, so every user tracking
        # the identical apple.com URL shares ONE row/check per cycle
        # instead of one per (user, product) pair — a deliberate cross-user
        # dedup, since re-checking the same fixed pincodes once per tracker
        # would multiply Scrape.do/Zyte cost for no additional information.
        # sku is cached here after first successful extraction (mirrors
        # pickup_tracking.sku) so steady-state cycles don't need a repeat
        # product-page fetch just to re-derive it.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apple_official_pickup_status (
                url            TEXT    PRIMARY KEY,
                sku            TEXT,
                pincode_status TEXT    NOT NULL DEFAULT '{}',
                last_checked   TEXT,
                created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        # ── Channel-forwarding stock alerts (single global admin-controlled
        # channel — see admin_handlers.py's /setchannel, /addchannel,
        # /stopforwarding, /listforwarding, and bot.run_channel_forward_
        # check_cycle). Deliberately NOT per-user (unlike whatsapp_channels
        # above) — this is an operator-run announcement feed, not a
        # consumer-facing feature, so there is only ever ONE registered
        # channel for the whole bot (id fixed at 1) and only the admin can
        # manage it (every admin_handlers.py command is already gated to
        # ADMIN_USER_ID at the router level).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS forward_channel (
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                chat_id       INTEGER NOT NULL,
                chat_title    TEXT,
                chat_username TEXT,
                registered_at TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        # Products set to forward their stock alerts to the single
        # forward_channel above, checked on the same CHECK_INTERVAL cadence
        # as the regular products table (see bot.run_channel_forward_check_
        # cycle) but otherwise fully independent of it — a SEPARATE table
        # (not a flag on products) since these aren't owned by any
        # individual user and their alerts route to the channel only, never
        # to a user's own chat. UNIQUE(url): the same product is only ever
        # forwarded once, regardless of how many times /addchannel is run
        # for it. reliance_article_id mirrors products.reliance_article_id
        # (see checkers/reliancedigital.py's migration) — RelianceDigital
        # products added here need the exact same one-time extraction;
        # database.get_reliance_article_id_for_url checks BOTH tables so a
        # product already tracked by a regular user (or vice versa) never
        # pays for a second extraction fetch.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_forward_tracking (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                name                 TEXT    NOT NULL,
                url                  TEXT    NOT NULL UNIQUE,
                site                 TEXT    NOT NULL,
                in_stock             INTEGER NOT NULL DEFAULT 0,
                last_checked         TEXT,
                reliance_article_id  TEXT,
                created_at           TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        # Global on/off switch for channel-forwarding ALERTS specifically —
        # separate from service_status above (which pauses the entire bot's
        # background checking). See admin_handlers.py's /forwardingtoggle.
        # Background checks for channel-forward items keep running either
        # way (so /listforwarding and /checkforwarding stay accurate) —
        # only the actual alert-send is gated by this, mirroring
        # service_status's own single-row/paused/paused_at shape.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS forwarding_status (
                id        INTEGER PRIMARY KEY CHECK (id = 1),
                paused    INTEGER NOT NULL DEFAULT 0,
                paused_at TEXT
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO forwarding_status (id, paused, paused_at) VALUES (1, 0, NULL)"
        )
        conn.commit()

        # Admin-configured default pincode(s) for the channel-forwarding
        # system (see admin_handlers.py's /setchannelpincode and bot.py's
        # run_channel_forward_check_cycle). channel_forward_tracking/
        # channel_forward_pickup_tracking rows have no owning user, so
        # there's no per-user pincode (unlike run_stock_check_cycle's own
        # get_user_primary_pincode lookup) to feed pincode-dependent
        # checkers (Croma, RelianceDigital, Apple's refine_with_pincode) —
        # this is the admin-wide substitute. Single global row (id fixed
        # at 1), same "one row" shape as forward_channel/forwarding_status
        # above — there's only ever one channel, so only one set of
        # forwarding pincodes makes sense. pincodes is a JSON-encoded list
        # of strings, same encoding as pickup_tracking.pincodes; empty
        # list ('[]') means "not configured yet", preserving today's
        # existing pincode=None/inconclusive behavior until an admin runs
        # /setchannelpincode.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channel_forward_pincode_config (
                id       INTEGER PRIMARY KEY CHECK (id = 1),
                pincodes TEXT    NOT NULL DEFAULT '[]'
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO channel_forward_pincode_config (id, pincodes) VALUES (1, '[]')"
        )
        conn.commit()

        # Apple pickup-availability channel-forwarding — the pickup sibling
        # of channel_forward_tracking above (see admin_handlers.py's
        # /addchannelpickup, /stopforwardingpickup, and checkers/apple.py's
        # check_channel_pickup_row). ONE ROW PER URL, carrying a list of
        # pincodes + a per-pincode status dict — mirrors pickup_tracking's
        # own shape exactly (pincodes/pincode_status columns, same JSON
        # encoding), so /addchannelpickup can track multiple pincodes per
        # product the same way /trackpickup already does for per-user
        # tracking. sku is cached after first successful extraction, same
        # convention as apple_official_pickup_status/pickup_tracking.
        #
        # Migration: this table originally shipped as ONE ROW PER PINCODE
        # (columns: pincode TEXT, available INTEGER, UNIQUE(url, pincode)).
        # Detect that old shape and fold any existing rows into the new
        # one-row-per-url shape (grouping by url, merging each row's single
        # pincode into the new pincode_status dict) rather than silently
        # dropping data on deploy.
        existing_pickup_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(channel_forward_pickup_tracking)")
        }
        if "pincode" in existing_pickup_cols:
            old_rows = conn.execute("SELECT * FROM channel_forward_pickup_tracking").fetchall()
            migrated: dict[str, dict] = {}
            for old_row in old_rows:
                d = dict(old_row)
                entry = migrated.setdefault(d["url"], {
                    "name": d["name"], "sku": d["sku"], "pincode_status": {}, "last_checked": None,
                })
                entry["pincode_status"][d["pincode"]] = bool(d["available"])
                if d["sku"]:
                    entry["sku"] = d["sku"]
                if d["last_checked"] and (entry["last_checked"] is None or d["last_checked"] > entry["last_checked"]):
                    entry["last_checked"] = d["last_checked"]
            conn.execute("DROP TABLE channel_forward_pickup_tracking")
            conn.commit()
            conn.execute("""
                CREATE TABLE channel_forward_pickup_tracking (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    name           TEXT    NOT NULL,
                    url            TEXT    NOT NULL UNIQUE,
                    sku            TEXT,
                    pincodes       TEXT    NOT NULL,
                    pincode_status TEXT    NOT NULL DEFAULT '{}',
                    last_checked   TEXT,
                    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.commit()
            for url, entry in migrated.items():
                conn.execute(
                    """
                    INSERT INTO channel_forward_pickup_tracking
                        (name, url, sku, pincodes, pincode_status, last_checked)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry["name"], url, entry["sku"],
                        json.dumps(list(entry["pincode_status"].keys())),
                        json.dumps(entry["pincode_status"]), entry["last_checked"],
                    ),
                )
            conn.commit()
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_forward_pickup_tracking (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    name           TEXT    NOT NULL,
                    url            TEXT    NOT NULL UNIQUE,
                    sku            TEXT,
                    pincodes       TEXT    NOT NULL,
                    pincode_status TEXT    NOT NULL DEFAULT '{}',
                    last_checked   TEXT,
                    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.commit()

        # Auto-refreshed Apple session (cookies + matching User-Agent), minted
        # periodically by a real headless-Chromium session in the separate
        # playwright_scraper service (see checkers/apple.py's
        # get_apple_session_cookies/set_apple_session_cookies and bot.py's
        # apple_cookie_refresh_loop) instead of a manually-pasted
        # APPLE_COOKIES/APPLE_USER_AGENT Railway env var. Single-row table
        # (id fixed at 1) — there is only ever one "current" Apple session,
        # never one per user/product. Falls back to the env vars only when
        # this table has never been populated (fresh deploy, or the
        # Playwright refresher unavailable/failing) — see checkers/apple.py.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apple_session_cookies (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                cookies     TEXT    NOT NULL,
                user_agent  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        # Migration: every pre-existing user (found via products/pin_codes) who
        # doesn't yet have a `users` row is switched into the access-control
        # system "as if their trial just ended" — access_until = now, giving
        # the admin a full GRACE_PERIOD_DAYS window to review/approve each one
        # via /approve before their tracked items are purged. Nobody is
        # auto-grandfathered; is_trial=0 since they're past the trial framing.
        standard_row = conn.execute("SELECT id FROM plans WHERE name = 'Standard'").fetchone()
        standard_plan_id = standard_row["id"] if standard_row else None

        existing_user_ids = set()
        for row in conn.execute("SELECT DISTINCT user_id FROM products"):
            existing_user_ids.add(row["user_id"])
        for row in conn.execute("SELECT DISTINCT user_id FROM pin_codes"):
            existing_user_ids.add(row["user_id"])
        # The admin is exempt from the trial/expiry system entirely (see the
        # permanent-access reset below) — never migrate them into a
        # time-limited row just because they tracked a product/pin themselves.
        existing_user_ids.discard(ADMIN_USER_ID)

        migrated = 0
        for uid in existing_user_ids:
            already = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone()
            if already:
                continue
            conn.execute(
                """
                INSERT INTO users (user_id, plan_id, is_trial, access_until, blocked, created_at)
                VALUES (?, ?, 0, ?, 0, ?)
                """,
                (uid, standard_plan_id, now_ist_str(), now_ist_str()),
            )
            migrated += 1
        conn.commit()
        if migrated:
            logger.info(
                f"Migrated {migrated} pre-existing user(s) into the access-control "
                f"system as expired/awaiting-approval — review via /pending or /approve"
            )

        # Self-heal: the admin should never be subject to the trial/expiry
        # system, but a users row can still exist for them (e.g. from the
        # migration above, before ADMIN_USER_ID was excluded from it, if the
        # admin ever tracked a product/pin themselves) with a real,
        # time-limited access_until — which then ages through expiry
        # reminders and eventual lockout for the very account meant to
        # bypass all of this. If such a row exists, reset it to a
        # 100-years-out access_until every startup: simpler and safer than
        # teaching compute_access (otherwise a pure function of stored data)
        # to special-case ADMIN_USER_ID, and functionally equivalent to
        # "never expires" for every reminder/grace/purge check that reads it.
        admin_row = conn.execute("SELECT * FROM users WHERE user_id = ?", (ADMIN_USER_ID,)).fetchone()
        if admin_row:
            permanent_until = (datetime.now(IST) + timedelta(days=365 * 100)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE users SET access_until = ?, is_trial = 0, blocked = 0 WHERE user_id = ?",
                (permanent_until, ADMIN_USER_ID),
            )
            conn.commit()
            logger.info(f"Admin user {ADMIN_USER_ID} access_until reset to permanent ({permanent_until})")
    logger.info(f"Database initialized at {DB_PATH}")


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

def add_product(
    user_id: int,
    name: str,
    url: str,
    site: str,
    target_price: float | None = None,
    reliance_article_id: str | None = None,
) -> tuple[bool, str]:
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO products (user_id, name, url, site, target_price, reliance_article_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, name, url, site, target_price, reliance_article_id),
            )
            conn.commit()
        return True, "Product added successfully."
    except sqlite3.IntegrityError:
        return False, "You are already tracking this URL."
    except Exception as e:
        logger.error(f"add_product error: {e}")
        return False, "Database error while adding product."


def get_reliance_article_id_for_url(url: str) -> str | None:
    """
    Returns the article_id already extracted for this EXACT url string by
    ANY user's row in `products` OR any row in `channel_forward_tracking`
    (see that table's own comment for why the latter needs this too — a
    product added only via /addchannel has no products row at all), or
    None if neither has one yet. Lets a second/third user tracking the
    identical RelianceDigital URL — or the channel-forward feature tracking
    the same URL a regular user already has — reuse the first extraction
    instead of paying for another Zyte/Scrape.do fetch — mirrors
    apple_official_pickup_status's own cross-user dedup reasoning (checking
    the same thing twice produces no additional information, only cost).
    """
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT reliance_article_id FROM products
            WHERE url = ? AND reliance_article_id IS NOT NULL
            UNION
            SELECT reliance_article_id FROM channel_forward_tracking
            WHERE url = ? AND reliance_article_id IS NOT NULL
            LIMIT 1
            """,
            (url, url),
        ).fetchone()
    return row["reliance_article_id"] if row else None


# ---------------------------------------------------------------------------
# Channel-forwarding stock alerts (see forward_channel / channel_forward_
# tracking tables above) — single global admin-controlled channel + the
# list of products whose alerts forward there.
# ---------------------------------------------------------------------------

def set_forward_channel(chat_id: int, chat_title: str | None, chat_username: str | None) -> None:
    """Registers (or replaces) the single forwarding channel. Re-running
    /setchannel with a different chat simply overwrites the previous one —
    there is only ever one at a time."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO forward_channel (id, chat_id, chat_title, chat_username, registered_at)
            VALUES (1, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                chat_id = excluded.chat_id,
                chat_title = excluded.chat_title,
                chat_username = excluded.chat_username,
                registered_at = excluded.registered_at
            """,
            (chat_id, chat_title, chat_username),
        )
        conn.commit()


def get_forward_channel() -> dict | None:
    """The registered forwarding channel, or None if /setchannel has never
    been run (or /addchannel etc. should treat forwarding as unconfigured)."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM forward_channel WHERE id = 1").fetchone()
    return dict(row) if row else None


def add_channel_forward_product(
    name: str, url: str, site: str, reliance_article_id: str | None = None,
) -> tuple[bool, str]:
    """Adds one product to the channel-forward list. Returns (False, ...)
    on a duplicate URL (UNIQUE constraint) — mirrors add_product's own
    IntegrityError handling."""
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO channel_forward_tracking (name, url, site, reliance_article_id)
                VALUES (?, ?, ?, ?)
                """,
                (name, url, site, reliance_article_id),
            )
            conn.commit()
        return True, "Product added to the forward list."
    except sqlite3.IntegrityError:
        return False, "This URL is already forwarding to the channel."
    except Exception as e:
        logger.error(f"add_channel_forward_product error: {e}")
        return False, "Database error while adding to the forward list."


def remove_channel_forward_product(url: str) -> bool:
    """Returns False if that URL wasn't in the forward list at all."""
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM channel_forward_tracking WHERE url = ?", (url,))
        conn.commit()
    return cursor.rowcount > 0


def list_channel_forward_products() -> list[dict]:
    """Every product currently forwarding, most recently added first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM channel_forward_tracking ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_channel_forward_status(row_id: int, in_stock: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE channel_forward_tracking SET in_stock = ?, last_checked = ? WHERE id = ?",
            (1 if in_stock else 0, now_ist_str(), row_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Channel-forwarding global on/off switch (see forwarding_status table
# above) — mirrors service_status/get_service_pause_info/set_service_paused
# exactly, but scoped to just the channel-forward alert send, not the
# entire bot's background checking.
# ---------------------------------------------------------------------------

def get_forwarding_pause_info() -> dict:
    """{'paused': bool, 'paused_at': str | None}."""
    with get_connection() as conn:
        row = conn.execute("SELECT paused, paused_at FROM forwarding_status WHERE id = 1").fetchone()
    if row is None:
        return {"paused": False, "paused_at": None}
    return {"paused": bool(row["paused"]), "paused_at": row["paused_at"]}


def is_forwarding_paused() -> bool:
    """Fast-path check for the channel-forward alert-send step (bot.py's
    _apply_result_to_channel_forward_row and checkers/apple.py's
    check_channel_pickup_row) — True means skip sending the alert, but the
    underlying check + status persistence still happens either way."""
    return get_forwarding_pause_info()["paused"]


def set_forwarding_paused(paused: bool) -> dict:
    paused_at = now_ist_str() if paused else None
    with get_connection() as conn:
        conn.execute(
            "UPDATE forwarding_status SET paused = ?, paused_at = ? WHERE id = 1",
            (1 if paused else 0, paused_at),
        )
        conn.commit()
    return {"paused": paused, "paused_at": paused_at}


def get_channel_forward_pincodes() -> list[str]:
    """The admin-configured default pincode(s) for channel-forwarding (see
    admin_handlers.py's /setchannelpincode) — [] if never configured, in
    which case callers should preserve today's existing pincode=None
    behavior for pincode-dependent checkers."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT pincodes FROM channel_forward_pincode_config WHERE id = 1"
        ).fetchone()
    if row is None:
        return []
    try:
        return json.loads(row["pincodes"])
    except (TypeError, ValueError):
        return []


def set_channel_forward_pincodes(pincodes: list[str]) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE channel_forward_pincode_config SET pincodes = ? WHERE id = 1",
            (json.dumps(pincodes),),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Apple pickup channel-forwarding (see channel_forward_pickup_tracking
# table above) — the pickup sibling of the channel_forward_tracking
# functions above.
# ---------------------------------------------------------------------------

def _row_to_channel_pickup_dict(row: sqlite3.Row) -> dict:
    """Parses the JSON-encoded pincodes/pincode_status columns into real
    Python objects — mirrors _row_to_pickup_dict's own decoding, since this
    table now shares pickup_tracking's pincodes/pincode_status shape."""
    d = dict(row)
    try:
        d["pincodes"] = json.loads(d["pincodes"])
    except (TypeError, ValueError):
        d["pincodes"] = []
    try:
        d["pincode_status"] = json.loads(d["pincode_status"])
    except (TypeError, ValueError):
        d["pincode_status"] = {}
    return d


def add_channel_forward_pickup(
    name: str, url: str, pincodes: list[str], sku: str | None = None,
) -> tuple[bool, str]:
    """Returns (False, ...) on a duplicate url — the same product is only
    ever forwarded once (UNIQUE(url)), mirroring channel_forward_tracking's
    own url-uniqueness for regular stock forwarding."""
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO channel_forward_pickup_tracking (name, url, sku, pincodes)
                VALUES (?, ?, ?, ?)
                """,
                (name, url, sku, json.dumps(pincodes)),
            )
            conn.commit()
        return True, "Pickup tracking added to the forward list."
    except sqlite3.IntegrityError:
        return False, "This URL is already forwarding pickup alerts to the channel."
    except Exception as e:
        logger.error(f"add_channel_forward_pickup error: {e}")
        return False, "Database error while adding to the pickup forward list."


def list_channel_forward_pickup() -> list[dict]:
    """Every pickup-forward entry, oldest first — stable, index-friendly
    ordering for admin_handlers.py's /stopforwardingpickup <index>."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM channel_forward_pickup_tracking ORDER BY created_at ASC"
        ).fetchall()
    return [_row_to_channel_pickup_dict(r) for r in rows]


def remove_channel_forward_pickup_by_id(row_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM channel_forward_pickup_tracking WHERE id = ?", (row_id,))
        conn.commit()
    return cursor.rowcount > 0


def remove_channel_forward_pickup_by_url(url: str) -> int:
    """Removes the entry for this URL (all of its tracked pincodes at
    once) — "stop forwarding pickup alerts for this product" rather than
    requiring the admin to know which specific pincode(s) to target.
    Returns how many rows were removed (0 or 1, since url is UNIQUE)."""
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM channel_forward_pickup_tracking WHERE url = ?", (url,))
        conn.commit()
    return cursor.rowcount


def update_channel_forward_pickup_status(row_id: int, pincode_status: dict, sku: str | None = None) -> None:
    """Persists the full per-pincode status dict and refreshes
    last_checked on every check (unconditionally, unlike pickup_tracking's
    update_pickup_status which callers only invoke `if changed`) so
    /listforwarding always shows an accurate last-checked time. sku is
    only overwritten when a non-None value is passed — mirrors
    upsert_apple_official_pickup_status's own "None never overwrites a
    cached value" convention, so a transient re-extraction failure never
    wipes out a previously-cached sku."""
    with get_connection() as conn:
        if sku is not None:
            conn.execute(
                "UPDATE channel_forward_pickup_tracking SET pincode_status = ?, last_checked = ?, sku = ? WHERE id = ?",
                (json.dumps(pincode_status), now_ist_str(), sku, row_id),
            )
        else:
            conn.execute(
                "UPDATE channel_forward_pickup_tracking SET pincode_status = ?, last_checked = ? WHERE id = ?",
                (json.dumps(pincode_status), now_ist_str(), row_id),
            )
        conn.commit()


def list_products(user_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def remove_product(user_id: int, product_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        )
        conn.commit()
    return cursor.rowcount > 0


def get_all_products() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM products").fetchall()
    return [dict(row) for row in rows]


def update_stock_status(product_id: int, in_stock: bool):
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE products
            SET in_stock = ?, last_checked = ?
            WHERE id = ?
            """,
            (1 if in_stock else 0, now_ist_str(), product_id),
        )
        conn.commit()


def get_product_by_id(product_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    return dict(row) if row else None


def search_products(user_id: int, keyword: str) -> list[dict]:
    """Case-insensitive partial match on product name, scoped to one user."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE user_id = ? AND LOWER(name) LIKE ? ORDER BY created_at DESC",
            (user_id, f"%{keyword.lower()}%"),
        ).fetchall()
    return [dict(row) for row in rows]


def get_product_by_id_for_user(product_id: int, user_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Pin codes
# ---------------------------------------------------------------------------

def add_pin_code(user_id: int, pin_code: str) -> tuple[bool, str]:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO pin_codes (user_id, pin_code) VALUES (?, ?)",
                (user_id, pin_code),
            )
            conn.commit()
        return True, "Pin code added."
    except sqlite3.IntegrityError:
        return False, "This pin code is already saved."
    except Exception as e:
        logger.error(f"add_pin_code error: {e}")
        return False, "Database error while adding pin code."


def remove_pin_code(user_id: int, pin_code: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM pin_codes WHERE user_id = ? AND pin_code = ?",
            (user_id, pin_code),
        )
        conn.commit()
    return cursor.rowcount > 0


def list_pin_codes(user_id: int) -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT pin_code FROM pin_codes WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()
    return [row["pin_code"] for row in rows]


def get_user_primary_pincode(user_id: int) -> str | None:
    """Return the user's first saved pin code, or None if they have none."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT pin_code FROM pin_codes WHERE user_id = ? ORDER BY created_at ASC LIMIT 1",
            (user_id,),
        ).fetchone()
    return row["pin_code"] if row else None


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

_EDITABLE_PLAN_FIELDS = {
    "name", "price", "max_items", "sites", "is_trial_plan", "is_active",
    "default_duration_days",
}


def add_plan(
    name: str,
    price: float,
    max_items: int,
    sites: str,
    default_duration_days: int = 30,
    is_trial_plan: bool = False,
    is_active: bool = True,
) -> tuple[bool, str]:
    """
    Create a plan with all configurable attributes. is_trial_plan is exclusive
    (only one plan may be the trial plan at a time), so setting it here clears
    the flag on every other plan first — mirroring edit_plan's behaviour.
    """
    try:
        with get_connection() as conn:
            if is_trial_plan:
                conn.execute("UPDATE plans SET is_trial_plan = 0")
            conn.execute(
                """
                INSERT INTO plans
                    (name, price, max_items, sites, is_trial_plan, is_active,
                     default_duration_days, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, price, max_items, sites, 1 if is_trial_plan else 0,
                 1 if is_active else 0, default_duration_days, now_ist_str()),
            )
            conn.commit()
        return True, "Plan created."
    except sqlite3.IntegrityError:
        return False, f"A plan named '{name}' already exists."
    except Exception as e:
        logger.error(f"add_plan error: {e}")
        return False, "Database error while creating plan."


def list_plans(active_only: bool = False) -> list[dict]:
    with get_connection() as conn:
        query = "SELECT * FROM plans"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY price ASC"
        rows = conn.execute(query).fetchall()
    return [dict(row) for row in rows]


def get_plan_by_id(plan_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    return dict(row) if row else None


def get_trial_plan() -> Optional[dict]:
    """The plan whose limits apply during the free trial (is_trial_plan=1).
    Falls back to the cheapest active plan if none is explicitly marked, so
    the system always has a usable trial plan even if misconfigured."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM plans WHERE is_trial_plan = 1 AND is_active = 1 LIMIT 1"
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM plans WHERE is_active = 1 ORDER BY price ASC LIMIT 1"
            ).fetchone()
    return dict(row) if row else None


def edit_plan(plan_id: int, field: str, value: str) -> tuple[bool, str]:
    if field not in _EDITABLE_PLAN_FIELDS:
        return False, f"Unknown field '{field}'. Editable: {', '.join(sorted(_EDITABLE_PLAN_FIELDS))}."
    try:
        with get_connection() as conn:
            # Only one plan may ever be flagged as the trial plan — setting
            # this one clears the flag on all others first.
            if field == "is_trial_plan" and str(value).lower() in ("1", "true", "yes"):
                conn.execute("UPDATE plans SET is_trial_plan = 0")
                value = 1
            cursor = conn.execute(f"UPDATE plans SET {field} = ? WHERE id = ?", (value, plan_id))
            conn.commit()
        if cursor.rowcount == 0:
            return False, f"No plan with id {plan_id}."
        return True, "Plan updated."
    except sqlite3.IntegrityError:
        return False, "That value conflicts with an existing plan (e.g. duplicate name)."
    except Exception as e:
        logger.error(f"edit_plan error: {e}")
        return False, "Database error while editing plan."


def delete_plan(plan_id: int) -> tuple[bool, str]:
    """Refuses to delete a plan that users are still assigned to, to avoid
    dangling references — reassign those users first via /setuserplan."""
    with get_connection() as conn:
        in_use = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE plan_id = ?", (plan_id,)
        ).fetchone()["n"]
        if in_use:
            return False, f"{in_use} user(s) are on this plan — reassign them first with /setuserplan."
        cursor = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        conn.commit()
    if cursor.rowcount == 0:
        return False, f"No plan with id {plan_id}."
    return True, "Plan deleted."


# ---------------------------------------------------------------------------
# Site locks (admin store restriction — global or per-user)
# ---------------------------------------------------------------------------

def is_site_locked(site: str, user_id: int | None = None) -> bool:
    """
    True if `site` is locked. A global lock (user_id IS NULL row) blocks
    everyone; when a user_id is supplied, a per-user lock for that user also
    counts. Site names are matched case-insensitively (stored lowercase).
    """
    site = site.lower()
    with get_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM site_locks WHERE user_id IS NULL AND site = ?", (site,)
        ).fetchone():
            return True
        if user_id is not None and conn.execute(
            "SELECT 1 FROM site_locks WHERE user_id = ? AND site = ?", (user_id, site)
        ).fetchone():
            return True
    return False


def set_global_site_lock(site: str, locked: bool) -> None:
    """Lock or unlock a store for ALL users. Idempotent."""
    site = site.lower()
    with get_connection() as conn:
        if locked:
            conn.execute(
                "INSERT OR IGNORE INTO site_locks (user_id, site, created_at) VALUES (NULL, ?, ?)",
                (site, now_ist_str()),
            )
        else:
            conn.execute("DELETE FROM site_locks WHERE user_id IS NULL AND site = ?", (site,))
        conn.commit()


def set_user_site_lock(user_id: int, site: str, locked: bool) -> None:
    """Lock or unlock a store for ONE specific user. Idempotent."""
    site = site.lower()
    with get_connection() as conn:
        if locked:
            conn.execute(
                "INSERT OR IGNORE INTO site_locks (user_id, site, created_at) VALUES (?, ?, ?)",
                (user_id, site, now_ist_str()),
            )
        else:
            conn.execute(
                "DELETE FROM site_locks WHERE user_id = ? AND site = ?", (user_id, site))
        conn.commit()


def list_global_site_locks() -> set[str]:
    """The set of globally-locked store names."""
    with get_connection() as conn:
        rows = conn.execute("SELECT site FROM site_locks WHERE user_id IS NULL").fetchall()
    return {r["site"] for r in rows}


def list_user_site_locks(user_id: int) -> set[str]:
    """The set of store names locked specifically for this user."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT site FROM site_locks WHERE user_id = ?", (user_id,)
        ).fetchall()
    return {r["site"] for r in rows}


# ---------------------------------------------------------------------------
# Zyte API usage log (proxy-metric cost tracking — see zyte_usage_log's
# schema comment in init_db for why this is an estimate, not exact billing)
# ---------------------------------------------------------------------------

# Zyte's own published pricing splits requests into an HTTP-only band and a
# much pricier browser-rendered band (see https://www.zyte.com/pricing/ —
# roughly $0.13-$1.27 per 1000 HTTP requests vs $1.00-$16.08 per 1000
# browser-rendered requests, the wide range coming from Zyte's own per-target-
# site "difficulty tier" classification, which isn't exposed to callers). Low/
# high ends of each band, in dollars per single request — used only to show a
# plausible ESTIMATED range, never a precise figure, since the actual
# difficulty tier per site is unknown to this codebase.
ZYTE_COST_PER_REQUEST_HTTP = (0.13 / 1000, 1.27 / 1000)
ZYTE_COST_PER_REQUEST_BROWSER = (1.00 / 1000, 16.08 / 1000)


def record_zyte_usage(
    site: str | None, render_js: bool, response_bytes: int, actions_used: bool = False,
) -> None:
    """
    Log one Zyte API fetch for later cost-estimation (see
    get_zyte_usage_summary). Called from zyte_client.py's fetch_page()
    after every successful Zyte-served fetch (i.e. Zyte's own endpoint
    returned HTTP 200 — a target-site error like a 404 still counts as
    "successful" here since Zyte still bills for it) — never from the
    Scrape.do code path, which has its own separate dashboard/billing the
    admin already monitors directly. site is whatever the caller knows (a
    tracked-product site key like "amazon", or None for an ad-hoc/debug
    fetch with no such concept, e.g. /debugzyte on an arbitrary URL).
    render_js means "the browser tier was used" (covers render_js,
    super_proxy, or an actions/custom_wait_ms request that forced it on).
    actions_used is tracked separately since a playWithBrowser/Zyte
    "actions" chain (click/type/wait...) adds cost/latency on TOP of the
    browser tier it requires, not just the browser tier alone.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO zyte_usage_log (site, render_js, response_bytes, actions_used, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (site, 1 if render_js else 0, response_bytes, 1 if actions_used else 0, now_ist_str()),
        )
        conn.commit()


def _month_start_ist() -> str:
    """The current IST calendar month's start, as a stored-format string —
    the default lower bound get_zyte_usage_summary uses, so the summary
    "resets" automatically at each month boundary without deleting any
    underlying log data (the full history stays queryable via since=None
    is NOT the same as all-time — pass since='' explicitly for that)."""
    now = datetime.now(IST)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def get_zyte_usage_summary(since: str | None = "__month__") -> list[dict]:
    """
    Per-site (site=None grouped as "(debug/other)") usage totals: request
    count, total response bytes, how many requests were browser-rendered
    vs plain HTTP, how many used a playWithBrowser/actions chain, and an
    ESTIMATED cost range in dollars derived from
    ZYTE_COST_PER_REQUEST_HTTP/BROWSER — NOT Zyte's actual billed amount
    (no per-request cost field exists in Zyte API's own response; see this
    module's zyte_usage_log schema comment). Ordered by request count,
    busiest site first.

    since controls the time window:
      "__month__" (default) — from the start of the current IST calendar
        month, i.e. a running counter that resets automatically every
        month without ever deleting the underlying log.
      None or ""            — all-time, every row ever logged.
      any other string      — rows with created_at >= that exact value
        (a stored-format 'YYYY-MM-DD HH:MM:SS' string, e.g. for a custom
        reporting window).
    """
    query = """
        SELECT
            site,
            COUNT(*) AS request_count,
            SUM(response_bytes) AS total_bytes,
            SUM(CASE WHEN render_js = 1 THEN 1 ELSE 0 END) AS browser_count,
            SUM(CASE WHEN render_js = 0 THEN 1 ELSE 0 END) AS http_count,
            SUM(CASE WHEN actions_used = 1 THEN 1 ELSE 0 END) AS actions_count
        FROM zyte_usage_log
    """
    params: tuple = ()
    if since == "__month__":
        query += " WHERE created_at >= ?"
        params = (_month_start_ist(),)
    elif since:
        query += " WHERE created_at >= ?"
        params = (since,)
    query += " GROUP BY site ORDER BY request_count DESC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    summary = []
    for row in rows:
        http_count = row["http_count"] or 0
        browser_count = row["browser_count"] or 0
        low = http_count * ZYTE_COST_PER_REQUEST_HTTP[0] + browser_count * ZYTE_COST_PER_REQUEST_BROWSER[0]
        high = http_count * ZYTE_COST_PER_REQUEST_HTTP[1] + browser_count * ZYTE_COST_PER_REQUEST_BROWSER[1]
        summary.append({
            "site": row["site"] or "(debug/other)",
            "request_count": row["request_count"],
            "total_bytes": row["total_bytes"] or 0,
            "browser_count": browser_count,
            "http_count": http_count,
            "actions_count": row["actions_count"] or 0,
            "estimated_cost_low": low,
            "estimated_cost_high": high,
        })
    return summary


# ---------------------------------------------------------------------------
# WhatsApp channel forwarding (per-user, admin-approved)
# ---------------------------------------------------------------------------

def register_whatsapp_channel(user_id: int, invite_link: str) -> None:
    """
    Register (or replace) a user's WhatsApp channel invite link. ALWAYS resets
    status to 'pending' — even re-registering an already-active channel with a
    NEW link requires fresh admin approval (the admin manually joins each
    link; a changed link means they haven't joined that one yet), and clears
    any prior approval bookkeeping. Also ALWAYS clears group_name: a changed
    invite_link may point at an entirely different group, so a previously
    resolved name would be actively misleading (sidebar-search delivery
    could match the WRONG chat by that stale name) rather than merely stale
    — it gets re-resolved via set_whatsapp_group_name during the next
    approval.
    """
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO whatsapp_channels (user_id, invite_link, status, group_name, registered_at, approved_at, approved_by_admin)
            VALUES (?, ?, 'pending', NULL, ?, NULL, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                invite_link = excluded.invite_link,
                status = 'pending',
                group_name = NULL,
                registered_at = excluded.registered_at,
                approved_at = NULL,
                approved_by_admin = NULL
            """,
            (user_id, invite_link, now_ist_str()),
        )
        conn.commit()


def get_whatsapp_channel(user_id: int) -> Optional[dict]:
    """This user's WhatsApp channel registration row, or None if never registered."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM whatsapp_channels WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def get_active_whatsapp_channel(user_id: int) -> Optional[dict]:
    """{'invite_link', 'group_name'} IF this user's registration is currently
    'active', else None. group_name may be None if it hasn't been resolved
    yet (see set_whatsapp_group_name) — callers should fall back to
    invite-link-only delivery in that case. The single lookup
    whatsapp_client.py uses before forwarding — pending/disabled/
    never-registered are all treated identically (no forward)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT invite_link, group_name FROM whatsapp_channels WHERE user_id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def approve_whatsapp_channel(user_id: int, admin_id: int) -> bool:
    """Mark a pending (or disabled) registration as active. Returns False if
    no registration exists for this user at all."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE whatsapp_channels
            SET status = 'active', approved_at = ?, approved_by_admin = ?
            WHERE user_id = ?
            """,
            (now_ist_str(), admin_id, user_id),
        )
        conn.commit()
    return cursor.rowcount > 0


def disable_whatsapp_channel(user_id: int) -> bool:
    """Revoke forwarding for this user (kept, not deleted, so the link/history
    survive a later re-approval). Returns False if no registration exists."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE whatsapp_channels SET status = 'disabled' WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
    return cursor.rowcount > 0


def set_whatsapp_group_name(user_id: int, group_name: str) -> bool:
    """Store the resolved display name for sidebar-search-based delivery
    (see whatsapp_forwarder's /resolve-name endpoint, normally called during
    admin approval). Returns False if no registration exists for this user."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE whatsapp_channels SET group_name = ? WHERE user_id = ?",
            (group_name, user_id),
        )
        conn.commit()
    return cursor.rowcount > 0


def list_pending_whatsapp_channels() -> list[dict]:
    """All registrations awaiting admin approval, oldest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM whatsapp_channels WHERE status = 'pending' ORDER BY registered_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def list_all_whatsapp_channels() -> list[dict]:
    """Every registration regardless of status, most recently registered first
    — for the dashboard's full list view."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM whatsapp_channels ORDER BY registered_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Apple Store pickup-availability tracking (see pickup_tracking table above)
# ---------------------------------------------------------------------------

def _row_to_pickup_dict(row: sqlite3.Row) -> dict:
    """Parses the JSON-encoded pincodes/pincode_status columns into real
    Python objects — every caller wants the decoded form, never the raw
    JSON text, so this is applied uniformly rather than repeated at each
    call site."""
    d = dict(row)
    try:
        d["pincodes"] = json.loads(d["pincodes"])
    except (TypeError, ValueError):
        d["pincodes"] = []
    try:
        d["pincode_status"] = json.loads(d["pincode_status"])
    except (TypeError, ValueError):
        d["pincode_status"] = {}
    return d


def add_pickup_tracking(user_id: int, name: str, url: str, sku: str, pincodes: list[str]) -> tuple[bool, str]:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO pickup_tracking (user_id, name, url, sku, pincodes) VALUES (?, ?, ?, ?, ?)",
                (user_id, name, url, sku, json.dumps(pincodes)),
            )
            conn.commit()
        return True, "Pickup tracking added successfully."
    except sqlite3.IntegrityError:
        return False, "You are already tracking pickup availability for this URL."
    except Exception as e:
        logger.error(f"add_pickup_tracking error: {e}")
        return False, "Database error while adding pickup tracking."


def list_pickup_tracking(user_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM pickup_tracking WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_pickup_dict(row) for row in rows]


def get_all_pickup_tracking() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM pickup_tracking").fetchall()
    return [_row_to_pickup_dict(row) for row in rows]


def update_pickup_status(tracking_id: int, pincode_status: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE pickup_tracking SET pincode_status = ? WHERE id = ?",
            (json.dumps(pincode_status), tracking_id),
        )
        conn.commit()


def log_pickup_alert_event(
    source: str, row_id: int | None, pincode: str | None, event: str, detail: str | None = None,
) -> None:
    """
    Durable, DB-persisted record of one step in the pickup-alert pipeline —
    see pickup_alert_log's own table comment for why this exists (Railway
    logs aren't accessible, and pincode_status snapshots self-erase once
    availability flips back). `source` is 'personal' | 'channel' |
    'official_stores'; `event` is a short fixed tag (e.g.
    'transition_true', 'alert_sent', 'alert_suppressed_locked',
    'alert_suppressed_paused', 'alert_send_error', 'fetch_error',
    'status_persist_error') — free text in `detail` only, never used for
    filtering logic, so new event tags can be added without a schema
    change.

    MUST NEVER RAISE: every call site is inside (or immediately after) an
    exception handler for the ACTUAL failure being recorded — an exception
    here must never replace or mask that original error, or interrupt the
    check cycle over a logging failure. Swallows and logs any DB error via
    the standard logger instead (Railway-log-only in that one edge case,
    same as before this table existed)."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO pickup_alert_log (ts, source, row_id, pincode, event, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now_ist_str(), source, row_id, pincode, event, detail),
            )
            conn.commit()
    except Exception as exc:
        logger.error(f"[pickup-alert-log] failed to record event source={source!r} event={event!r}: {exc}")


def get_recent_pickup_alert_events(limit: int = 30, substring: str | None = None) -> list[dict]:
    """Most recent pickup_alert_log rows, newest first. `substring` (if
    given) case-insensitively matches against `detail` OR `pincode` —
    detail includes sku/url text at every call site below, so a sku/url
    substring search works the same way /debugpickupstatus's does."""
    query = "SELECT * FROM pickup_alert_log"
    params: list = []
    if substring:
        query += " WHERE detail LIKE ? OR pincode LIKE ?"
        like = f"%{substring}%"
        params.extend([like, like])
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def remove_pickup_tracking(user_id: int, tracking_id: int) -> bool:
    """Deletes one pickup_tracking row — mirrors remove_product's shape
    exactly. Scoped to (id, user_id) so a user can never remove another
    user's tracked pickup item. Only ever touches pickup_tracking; the
    regular products table (and its own /remove flow) is untouched."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM pickup_tracking WHERE id = ? AND user_id = ?",
            (tracking_id, user_id),
        )
        conn.commit()
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Apple official-store pickup auto-check (see apple_official_pickup_status
# table above — a separate feature/table from pickup_tracking, keyed by
# URL rather than user_id/id, for the cross-user dedup reasoning in that
# table's own comment).
# ---------------------------------------------------------------------------

def get_apple_official_pickup_status(url: str) -> dict | None:
    """Returns {'url', 'sku', 'pincode_status' (decoded dict),
    'last_checked', 'created_at'} for one tracked Apple product URL, or
    None if it's never been seen before."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM apple_official_pickup_status WHERE url = ?", (url,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["pincode_status"] = json.loads(d["pincode_status"])
    except (TypeError, ValueError):
        d["pincode_status"] = {}
    return d


def upsert_apple_official_pickup_status(
    url: str, sku: str | None, pincode_status: dict, mark_checked: bool = True,
) -> None:
    """
    Creates or updates the row for `url`. `sku` is only overwritten when a
    non-None value is passed (so a transient re-extraction failure never
    wipes out a previously cached SKU — mirrors get_or_create_user's own
    "None never overwrites a stored value" convention). mark_checked sets
    last_checked to now; pass False when persisting a status change
    without this being a full pass over every pincode (not currently used,
    kept for symmetry/future callers — the production caller always
    passes the default True).
    """
    now = now_ist_str() if mark_checked else None
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT sku FROM apple_official_pickup_status WHERE url = ?", (url,)
        ).fetchone()
        final_sku = sku if sku is not None else (existing["sku"] if existing else None)
        conn.execute(
            """
            INSERT INTO apple_official_pickup_status (url, sku, pincode_status, last_checked)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                sku = excluded.sku,
                pincode_status = excluded.pincode_status,
                last_checked = COALESCE(excluded.last_checked, apple_official_pickup_status.last_checked)
            """,
            (url, final_sku, json.dumps(pincode_status), now),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Apple auto-refreshed session cookies (see apple_session_cookies table
# above) — the Playwright-based replacement for manually-pasted
# APPLE_COOKIES/APPLE_USER_AGENT env vars. Single row, id=1.
# ---------------------------------------------------------------------------

def get_apple_session_cookies() -> dict | None:
    """Returns {'cookies', 'user_agent', 'updated_at'} from the most recent
    Playwright-based refresh, or None if the refresher has never
    successfully run yet (checkers/apple.py falls back to the
    APPLE_COOKIES/APPLE_USER_AGENT env vars in that case)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT cookies, user_agent, updated_at FROM apple_session_cookies WHERE id = 1"
        ).fetchone()
    return dict(row) if row else None


def set_apple_session_cookies(cookies: str, user_agent: str) -> None:
    """Overwrites the single stored Apple session with a freshly-minted
    one. Called only after a successful Playwright refresh (see bot.py's
    apple_cookie_refresh_loop) — a failed/timed-out refresh never calls
    this, so a transient Playwright failure leaves the previous
    (still-usable) session in place rather than wiping it out."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO apple_session_cookies (id, cookies, user_agent, updated_at)
            VALUES (1, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                cookies = excluded.cookies,
                user_agent = excluded.user_agent,
                updated_at = excluded.updated_at
            """,
            (cookies, user_agent),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Users / access control
# ---------------------------------------------------------------------------

def get_or_create_user(
    user_id: int, username: str | None = None, first_name: str | None = None
) -> dict:
    """
    Returns the user's access row, creating a bare row with NO access granted
    on first sight (plan_id/access_until stay NULL) — no automatic trial.
    The only ways to gain access are the /freetrial WhatsApp-share flow
    (see activate_share_trial) or manual admin approval (see grant_access).
    Refreshes username/first_name when a NON-None value is supplied and it
    differs from what's stored. Critically, a None argument NEVER overwrites a
    stored value: many callers (the access middleware via get_access_info,
    check_can_add_item, etc.) invoke this with just a user_id and no profile
    data — previously that wiped the username/first_name captured at /start to
    NULL on the user's very next interaction, which is why most users showed
    only their ID. Now those calls preserve the stored profile, and any call
    that DOES supply fresh profile data (e.g. the middleware forwarding the
    Telegram user) refreshes it — so previously-wiped users self-heal the next
    time they interact.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, first_name, plan_id, is_trial, access_until, blocked, created_at)
                VALUES (?, ?, ?, NULL, 0, NULL, 0, ?)
                """,
                (user_id, username, first_name, now_ist_str()),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            logger.info(f"New user {user_id} created with no access — must use /freetrial or await admin approval")
        else:
            new_username = username if username is not None else row["username"]
            new_first_name = first_name if first_name is not None else row["first_name"]
            if new_username != row["username"] or new_first_name != row["first_name"]:
                conn.execute(
                    "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                    (new_username, new_first_name, user_id),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row)


def get_user(user_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None



def get_user_lang(user_id: int) -> str:
    """Return the user's language preference (one of translations.LANGS),
    defaulting to 'en' for unknown users or an unrecognized stored value. Safe
    to call from any thread (opens its own connection), so the dashboard and the
    background alert loop can both resolve a recipient's language."""
    with get_connection() as conn:
        row = conn.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row and row["lang"] in _VALID_LANGS:
        return row["lang"]
    return "en"


def set_user_lang(user_id: int, lang: str) -> bool:
    """Set the user's language preference. Returns False for an invalid lang or
    a user row that doesn't exist yet."""
    if lang not in _VALID_LANGS:
        return False
    with get_connection() as conn:
        cursor = conn.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, user_id))
        conn.commit()
    return cursor.rowcount > 0


def list_all_users() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [dict(row) for row in rows]


def set_user_plan(user_id: int, plan_id: int) -> bool:
    """
    Assign a user to a plan (admin /setuserplan). ALSO clears is_trial=0:
    putting someone on a formal plan is an explicit admin action that ends
    any trial framing, so trial and paid-plan can never run concurrently.
    Without this, a user reassigned mid-trial kept is_trial=1 while carrying
    a paid plan_id, and compute_access — which keys status purely off is_trial —
    still reported them as "trial" while enforcing the paid plan's limits. A
    user is either on a trial (is_trial=1) or on a plan (is_trial=0), never
    both. access_until is deliberately left untouched: this only reassigns the
    plan, it doesn't grant or extend time (that's /approve and /extend's job).
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE users SET plan_id = ?, is_trial = 0 WHERE user_id = ?", (plan_id, user_id)
        )
        conn.commit()
    return cursor.rowcount > 0


def grant_access(user_id: int, plan_id: int | None, days: int, admin_id: int) -> dict:
    """
    Approve/extend a user's access. STACKS on remaining access rather than
    overwriting: if access_until is still in the future, `days` is added on
    top of it (so two 30-day gift cards correctly extend to 60 days from the
    current expiry); otherwise the new period starts from now. Clears
    `blocked`, sets is_trial=0 (formally approved, no longer just "trialing"),
    resets reminder_sent_until so the expiry reminder can fire again for the
    new deadline, and clears share_trial_requested (a no-op unless this
    approval is resolving a share-trial pending request) so it stops showing
    in /pending once acted on. Records the approval and returns the updated
    user row.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        now = datetime.now(IST)
        base = now
        if row and row["access_until"]:
            try:
                current_until = parse_ist(row["access_until"])
                if current_until > now:
                    base = current_until
            except ValueError:
                pass
        new_until = (base + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

        if plan_id is None and row:
            plan_id = row["plan_id"]

        if row is None:
            conn.execute(
                """
                INSERT INTO users (user_id, plan_id, is_trial, access_until, blocked, created_at)
                VALUES (?, ?, 0, ?, 0, ?)
                """,
                (user_id, plan_id, new_until, now_ist_str()),
            )
        else:
            conn.execute(
                """
                UPDATE users
                SET plan_id = ?, is_trial = 0, access_until = ?, blocked = 0,
                    reminder_sent_until = NULL, share_trial_requested = 0
                WHERE user_id = ?
                """,
                (plan_id, new_until, user_id),
            )
        conn.commit()
        updated = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    plan = get_plan_by_id(plan_id) if plan_id else None
    amount = plan["price"] if plan else None
    _record_approval(user_id, plan_id, days, amount, "approve", None, admin_id)
    return dict(updated)


def reject_user(user_id: int, admin_id: int, reason: str | None = None) -> bool:
    row = get_user(user_id)
    if row is None:
        return False
    # Clears any pending share-trial request too, so a rejected request stops
    # showing in /pending — share_trial_used stays 1 (the one-time bonus is
    # still considered consumed; a reject doesn't refund another attempt).
    with get_connection() as conn:
        conn.execute("UPDATE users SET share_trial_requested = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
    _record_approval(user_id, None, None, None, "reject", reason, admin_id)
    return True


def extend_access(user_id: int, days: int, admin_id: int) -> Optional[dict]:
    """Add days to the user's current access_until without changing plan."""
    row = get_user(user_id)
    if row is None:
        return None
    return grant_access(user_id, row["plan_id"], days, admin_id)


def has_used_share_trial(user_id: int) -> bool:
    """Whether this user has already claimed the WhatsApp-share-gated trial bonus."""
    row = get_user(user_id)
    return bool(row and row.get("share_trial_used"))


def get_share_trial_rounds(user_id: int) -> int:
    """How many of the SHARE_TRIAL_ROUNDS_REQUIRED share+confirm rounds this
    user has completed so far (0 for a user who hasn't started, or has none)."""
    row = get_user(user_id)
    return (row.get("share_trial_rounds_done") or 0) if row else 0


def increment_share_trial_round(user_id: int) -> int:
    """
    Advance this user's share-trial round counter by 1, capped at
    SHARE_TRIAL_ROUNDS_REQUIRED. Returns the new count. Stored in the DB
    (not FSM/in-memory state) so a user can close the app between shares and
    resume exactly where they left off, even across a bot restart.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT share_trial_rounds_done FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        current = (row["share_trial_rounds_done"] or 0) if row else 0
        new_count = min(current + 1, SHARE_TRIAL_ROUNDS_REQUIRED)
        conn.execute(
            "UPDATE users SET share_trial_rounds_done = ? WHERE user_id = ?",
            (new_count, user_id),
        )
        conn.commit()
    return new_count


def reset_share_trial_rounds(user_id: int) -> None:
    """Reset the share-trial round counter to 0 (used by /freetrial's Retry button)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET share_trial_rounds_done = 0 WHERE user_id = ?", (user_id,)
        )
        conn.commit()


def request_share_trial(user_id: int) -> tuple[bool, Optional[dict]]:
    """
    Called when a user completes all SHARE_TRIAL_ROUNDS_REQUIRED share+confirm
    rounds and taps Confirm (see /freetrial in handlers.py). Does NOT grant
    access directly: it marks share_trial_requested=1, which surfaces the
    request in /pending (admin_handlers.py) and the dashboard's Pending list
    alongside regular pending approvals — the admin still grants the trial
    manually via /approve or the dashboard, exactly like any other new user.
    This gives the admin final say even over the share-gated path.

    share_trial_used is set here too (not only at grant time) so the
    one-time bonus can't be requested a second time while awaiting approval
    or after being rejected — grant_access/reject_user clear
    share_trial_requested once the admin acts, but share_trial_used stays 1
    permanently, consistent with the offer being one-time per account.

    Returns (requested, row): requested=False (row unchanged) if this user
    already claimed/requested it before, hasn't completed all rounds yet, or
    has no user row at all — the check-and-set happens inside this one DB
    call so it's the single source of truth (safe against a double-tapped
    confirm button), not just the handler-level UX checks.
    """
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None or row["share_trial_used"]:
            return False, (dict(row) if row else None)
        if (row["share_trial_rounds_done"] or 0) < SHARE_TRIAL_ROUNDS_REQUIRED:
            return False, dict(row)

        conn.execute(
            "UPDATE users SET share_trial_used = 1, share_trial_requested = 1 WHERE user_id = ?",
            (user_id,),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    logger.info(f"[share_trial] user {user_id} completed share rounds — request pending admin approval")
    return True, dict(updated)


def set_blocked(user_id: int, blocked: bool, admin_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE users SET blocked = ? WHERE user_id = ?", (1 if blocked else 0, user_id)
        )
        conn.commit()
    if cursor.rowcount == 0:
        return False
    _record_approval(user_id, None, None, None, "block" if blocked else "unblock", None, admin_id)
    return True


def mark_reminder_sent(user_id: int, access_until: str):
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET reminder_sent_until = ? WHERE user_id = ?",
            (access_until, user_id),
        )
        conn.commit()


def purge_user_data(user_id: int) -> int:
    """Permanently delete a user's tracked products (called once the grace
    period elapses with no renewal). Returns the number of products deleted."""
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM products WHERE user_id = ?", (user_id,))
        conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Admin bulk actions: "Stop Tracking" + "Stop Plan" panel (Telegram
# /managetracking and the dashboard's matching page) and the "tracked links
# grouped by store" view (/linksbystore and its dashboard equivalent).
# ---------------------------------------------------------------------------

def list_users_with_products_summary() -> list[dict]:
    """
    Every user who currently has at least 1 tracked product, joined with
    their access-row fields (plan_id, is_trial, access_until, blocked) and a
    product_count. Built from `products` as the base table (LEFT JOIN users)
    so a user is never missed even if their `users` row somehow doesn't
    exist yet — defensive rather than assumed, since normally every
    product-tracking user already has one (see init_db's migration/self-heal
    comments). Ordered by product_count DESC so the busiest trackers are
    seen first.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.user_id,
                   u.username, u.first_name, u.plan_id, u.is_trial,
                   u.access_until, u.blocked, u.checks_paused, u.checks_paused_at,
                   COUNT(p.id) AS product_count
            FROM products p
            LEFT JOIN users u ON u.user_id = p.user_id
            GROUP BY p.user_id
            ORDER BY product_count DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def bulk_stop_tracking(user_ids: list[int]) -> dict[int, list[str]]:
    """
    Admin bulk action: delete ALL tracked products for each of the given
    user_ids. Returns {user_id: [deleted product names]}, only for user_ids
    that actually had something deleted, so callers can both report a
    summary AND notify each affected user with the exact items removed
    (mirroring dashboard.py's existing single-user _bulk_remove/
    items_removed_text notification pattern).
    """
    if not user_ids:
        return {}
    removed: dict[int, list[str]] = {}
    with get_connection() as conn:
        for uid in user_ids:
            rows = conn.execute("SELECT name FROM products WHERE user_id = ?", (uid,)).fetchall()
            if not rows:
                continue
            conn.execute("DELETE FROM products WHERE user_id = ?", (uid,))
            removed[uid] = [r["name"] for r in rows]
        conn.commit()
    return removed


def cancel_user_plan(user_id: int, admin_id: int) -> bool:
    """
    Admin 'Stop Plan' action: immediately ends this user's current access
    period by setting access_until to now. Reuses the SAME expiry mechanism
    a plan naturally reaching its end date already goes through (grace
    period, then locked — see access.compute_access), rather than
    introducing a separate punitive semantic like set_blocked's admin lock.
    plan_id and is_trial are left untouched, so a later grant_access/
    /approve for this user resumes cleanly against the same plan. Returns
    False if the user has no row at all. Recorded in the approvals audit
    trail as action='cancel'.
    """
    row = get_user(user_id)
    if row is None:
        return False
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET access_until = ? WHERE user_id = ?",
            (now_ist_str(), user_id),
        )
        conn.commit()
    _record_approval(user_id, row.get("plan_id"), None, None, "cancel", "Admin bulk plan cancellation", admin_id)
    return True


def bulk_cancel_plan(user_ids: list[int], admin_id: int) -> list[int]:
    """Cancel access for each of the given user_ids (see cancel_user_plan).
    Returns the subset that were actually found and cancelled."""
    return [uid for uid in user_ids if cancel_user_plan(uid, admin_id)]


def list_tracked_links_by_store() -> dict[str, list[dict]]:
    """
    Every currently-tracked product URL, grouped by site and deduplicated
    across users: {site: [{site, url, tracker_count, name}, ...]},
    most-tracked link first within each site. `name` is the display name
    from whichever tracking user added it earliest for that (site, url) pair
    — different users can type different names for the same URL, so the
    earliest-added one is used as a stable, deterministic choice.
    tracker_count counts DISTINCT users tracking that exact URL — the
    admin's "how many users track this link" view (Telegram /linksbystore
    and the dashboard's matching page).
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.site, p.url,
                   COUNT(DISTINCT p.user_id) AS tracker_count,
                   (
                       SELECT name FROM products
                       WHERE products.site = p.site AND products.url = p.url
                       ORDER BY created_at ASC LIMIT 1
                   ) AS name
            FROM products p
            GROUP BY p.site, p.url
            ORDER BY p.site ASC, tracker_count DESC, name ASC
            """
        ).fetchall()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["site"], []).append(dict(row))
    return grouped


def list_pickup_tracking_links_by_store() -> list[dict]:
    """
    Sibling of list_tracked_links_by_store above, for Apple pickup tracking
    (database.pickup_tracking — /trackpickup) specifically, which lives in
    its own table entirely separate from `products` and so was invisible
    on /links-by-store (2026-07-28 gap: the dashboard page and its
    Telegram /linksbystore counterpart both only ever queried `products`).

    Same [{url, tracker_count, name}, ...] shape as one of
    list_tracked_links_by_store's per-site lists — most-tracked link
    first — but returned as a flat list (not grouped by site) since
    pickup_tracking has no `site` column at all (it's implicitly always
    Apple pickup). Callers that want it alongside the regular per-site
    sections attach their own "apple pickup" label.

    Deduplicates by URL only, same as the products-table version — a
    row's own `pincodes` list (which pincodes THIS user chose) isn't
    relevant to "how many distinct users track this product", the same
    way per-user price targets aren't relevant to that same question for
    regular products.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT p.url,
                   COUNT(DISTINCT p.user_id) AS tracker_count,
                   (
                       SELECT name FROM pickup_tracking
                       WHERE pickup_tracking.url = p.url
                       ORDER BY created_at ASC LIMIT 1
                   ) AS name
            FROM pickup_tracking p
            GROUP BY p.url
            ORDER BY tracker_count DESC, name ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Pause/Resume Service — global on/off switch (service_status) + per-user
# pause (users.checks_paused). See init_db's schema comments for the full
# design rationale. Both are checked by bot.py's run_stock_check_cycle;
# neither ever touches a user's saved tracked items or sends them anything.
# ---------------------------------------------------------------------------

def get_service_pause_info() -> dict:
    """{'paused': bool, 'paused_at': str | None} — the GLOBAL switch's
    current state. paused_at is the IST timestamp of the most recent pause
    (None while running)."""
    with get_connection() as conn:
        row = conn.execute("SELECT paused, paused_at FROM service_status WHERE id = 1").fetchone()
    if row is None:
        return {"paused": False, "paused_at": None}
    return {"paused": bool(row["paused"]), "paused_at": row["paused_at"]}


def is_service_paused() -> bool:
    """Fast-path check for bot.py's run_stock_check_cycle — True means skip
    the ENTIRE cycle (no get_all_products(), no grouping, no provider
    calls at all)."""
    return get_service_pause_info()["paused"]


def set_service_paused(paused: bool) -> dict:
    """Flip the GLOBAL switch. Sets paused_at to now on pause, clears it to
    NULL on resume. Returns the updated {'paused', 'paused_at'} dict."""
    paused_at = now_ist_str() if paused else None
    with get_connection() as conn:
        conn.execute(
            "UPDATE service_status SET paused = ?, paused_at = ? WHERE id = 1",
            (1 if paused else 0, paused_at),
        )
        conn.commit()
    return {"paused": paused, "paused_at": paused_at}


def is_user_checks_paused(user_id: int) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT checks_paused FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return bool(row and row["checks_paused"])


def list_paused_user_ids() -> list[int]:
    """Every user_id currently individually paused — bot.py's
    run_stock_check_cycle excludes their products from the check cycle
    (as a set membership test, cheap even for a large user base)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT user_id FROM users WHERE checks_paused = 1").fetchall()
    return [r["user_id"] for r in rows]


def set_users_checks_paused(user_ids: list[int], paused: bool) -> int:
    """Bulk set/clear the per-user pause flag for the given user_ids (admin
    'Pause SELECTED users' / 'Resume SELECTED users' action — see
    /pauseservice, /resumeservice, and the dashboard's matching page).
    Returns how many existing user rows were actually updated. Never
    touches a paused user's tracked products or sends them anything — see
    this section's module comment."""
    if not user_ids:
        return 0
    paused_at = now_ist_str() if paused else None
    with get_connection() as conn:
        placeholders = ",".join("?" for _ in user_ids)
        cursor = conn.execute(
            f"UPDATE users SET checks_paused = ?, checks_paused_at = ? "
            f"WHERE user_id IN ({placeholders})",
            (1 if paused else 0, paused_at, *user_ids),
        )
        conn.commit()
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Approvals (audit trail for approve/reject/extend/block/unblock)
# ---------------------------------------------------------------------------

def _record_approval(
    user_id: int,
    plan_id: int | None,
    days: int | None,
    amount: float | None,
    action: str,
    reason: str | None,
    admin_id: int,
):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO approvals (user_id, plan_id, days, amount, action, reason, admin_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, plan_id, days, amount, action, reason, admin_id, now_ist_str()),
        )
        conn.commit()


def get_approval_history(user_id: int) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_approvals_since(since_str: str) -> list[dict]:
    """Approvals with action='approve' at/after the given IST timestamp string
    — used for /stats' revenue-this-month calculation."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE action = 'approve' AND created_at >= ? ORDER BY created_at DESC",
            (since_str,),
        ).fetchall()
    return [dict(row) for row in rows]
