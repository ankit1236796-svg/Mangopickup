"""
notifications.py
~~~~~~~~~~~~~~~~~
Apple pickup alert senders only — extracted from Tracker-alert's full
notifications.py, which also handles stock alerts, WhatsApp forwarding,
plan/approval notices, etc. Those aren't needed here.
"""

import html
import logging

from aiogram import Bot

from database import get_user_lang, is_site_locked
from translations import t

logger = logging.getLogger(__name__)


async def _safe_send(bot: Bot, user_id: int, text: str) -> bool:
    """Send a plain HTML message to a user, logging (not raising) on failure —
    e.g. the user blocked the bot. Returns whether it succeeded."""
    try:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
        return True
    except Exception as exc:
        logger.error(f"Failed to message user {user_id}: {exc}")
        return False


async def send_pickup_alert(
    bot: Bot, user_id: int, product_name: str, pincode: str, stores: list[dict],
) -> str:
    """
    Send a pickup-availability notification — one call per (tracked product,
    pincode) unavailable→available transition. `stores` is
    checkers.apple.available_stores_for_pickup()'s return value: a list of
    {"store_name": ..., "location": ... | None} dicts.

    Returns a short status string: "sent", "suppressed_locked", "send_failed",
    or "error:<ExceptionType>: <msg>" — never raises.
    """
    try:
        if is_site_locked("apple", user_id):
            logger.info(
                f"[pickup-alert-suppressed] apple is locked (global or for user "
                f"{user_id}) — skipping pickup alert."
            )
            return "suppressed_locked"
        lang = get_user_lang(user_id)
        lines = []
        for store in stores:
            name = html.escape(store.get("store_name") or "(unnamed store)")
            location = store.get("location")
            if location:
                lines.append(f"🏬 <b>{name}</b> — {html.escape(location)}")
            else:
                lines.append(f"🏬 <b>{name}</b>")
        stores_block = "\n".join(lines) if lines else "🏬 (store details unavailable)"
        text = t(
            "pickup_alert", lang,
            name=html.escape(product_name), pincode=pincode, stores_block=stores_block,
        )
        sent = await _safe_send(bot, user_id, text)
        return "sent" if sent else "send_failed"
    except Exception as exc:
        logger.error(f"[pickup-alert] unexpected error building/sending alert for user {user_id}: {exc}")
        return f"error:{type(exc).__name__}: {exc}"


async def send_channel_pickup_alert(
    bot: Bot, chat_id: int, product_name: str, pincode: str, stores: list[dict],
) -> str:
    """
    Channel-forwarding sibling of send_pickup_alert, targeting a channel
    chat_id instead of a user_id. Same global-lock-only is_site_locked gate
    (no per-user lock concept — a pickup channel-forward row has no owning
    user).
    """
    try:
        if is_site_locked("apple"):
            logger.info(
                "[channel-forward][pickup-alert-suppressed] apple is globally "
                "locked — skipping channel pickup alert."
            )
            return "suppressed_locked"
        lines = []
        for store in stores:
            name = html.escape(store.get("store_name") or "(unnamed store)")
            location = store.get("location")
            if location:
                lines.append(f"🏬 <b>{name}</b> — {html.escape(location)}")
            else:
                lines.append(f"🏬 <b>{name}</b>")
        stores_block = "\n".join(lines) if lines else "🏬 (store details unavailable)"
        text = t(
            "pickup_alert", "en",
            name=html.escape(product_name), pincode=pincode, stores_block=stores_block,
        )
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            logger.info(f"[channel-forward][pickup] alert sent to channel {chat_id} for {product_name!r} pincode={pincode!r}")
            return "sent"
        except Exception as exc:
            logger.error(f"[channel-forward][pickup] failed to send alert to channel {chat_id}: {exc}")
            return "send_failed"
    except Exception as exc:
        logger.error(f"[channel-forward][pickup] unexpected error building/sending alert to channel {chat_id}: {exc}")
        return f"error:{type(exc).__name__}: {exc}"
