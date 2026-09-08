import logging
import os

import requests

log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def notify(message: str, *, html: bool = False) -> None:
    """Best-effort Telegram push - logs and returns instead of raising if
    unconfigured or if the request fails, so a notification problem can
    never take down the trading loop.

    `html=True` sends with Telegram's HTML parse_mode (<b>/<i> tags render
    as bold/italic) - callers passing it are responsible for html.escape()-ing
    any dynamic text they interpolate, so a stray "<" from e.g. a news
    headline can't break the tag structure or get silently dropped.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("Telegram not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) - notification skipped: %s", message)
        return
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    if html:
        payload["parse_mode"] = "HTML"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        log.exception("Failed to send Telegram notification")
