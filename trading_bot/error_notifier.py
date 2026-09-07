import logging
import os

import requests

from trading_bot.timeutil import now_ist

log = logging.getLogger(__name__)

# Deliberately a SEPARATE bot/chat from notifier.py's trading-activity alerts
# - so error triage doesn't get lost in the noise of routine entry/exit/
# briefing messages. Every message is tagged with the IST date so scrolling
# the error chat makes clear which trading day each issue belongs to.
ERROR_TELEGRAM_BOT_TOKEN = os.environ.get("ERROR_TELEGRAM_BOT_TOKEN", "")
ERROR_TELEGRAM_CHAT_ID = os.environ.get("ERROR_TELEGRAM_CHAT_ID", "")


def notify_error(message: str) -> None:
    """Best-effort push to the dedicated error-alert bot/chat - logs and
    returns instead of raising if unconfigured or the request fails, same
    contract as notifier.notify()."""
    tagged = f"[{now_ist().strftime('%Y-%m-%d %H:%M IST')}] ERROR: {message}"
    if not ERROR_TELEGRAM_BOT_TOKEN or not ERROR_TELEGRAM_CHAT_ID:
        log.info("Error-alert Telegram not configured (ERROR_TELEGRAM_BOT_TOKEN/ERROR_TELEGRAM_CHAT_ID) - skipped: %s", tagged)
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{ERROR_TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": ERROR_TELEGRAM_CHAT_ID, "text": tagged},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        log.exception("Failed to send error-alert Telegram notification")
