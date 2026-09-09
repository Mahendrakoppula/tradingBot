import logging
import os

import requests

from trading_bot.timeutil import now_ist

log = logging.getLogger(__name__)

# Deliberately a SEPARATE bot/chat from notifier.py/error_notifier.py's -
# this is the second (technical-indicator) bot's own dedicated channel for
# BOTH routine activity and errors (user set up one bot/chat for it, not a
# further routine/error split like the daily bot has), so its alerts don't
# mix into the daily bot's chat and vice versa.
TECH_TELEGRAM_BOT_TOKEN = os.environ.get("TECH_TELEGRAM_BOT_TOKEN", "")
TECH_TELEGRAM_CHAT_ID = os.environ.get("TECH_TELEGRAM_CHAT_ID", "")


def notify(message: str, *, html: bool = False) -> None:
    """Best-effort Telegram push to the technical bot's own chat - logs and
    returns instead of raising if unconfigured or the request fails, same
    contract as notifier.notify()."""
    if not TECH_TELEGRAM_BOT_TOKEN or not TECH_TELEGRAM_CHAT_ID:
        log.info("Technical-bot Telegram not configured (TECH_TELEGRAM_BOT_TOKEN/TECH_TELEGRAM_CHAT_ID) - notification skipped: %s", message)
        return
    payload = {"chat_id": TECH_TELEGRAM_CHAT_ID, "text": message}
    if html:
        payload["parse_mode"] = "HTML"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TECH_TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        log.exception("Failed to send technical-bot Telegram notification")


def notify_error(message: str) -> None:
    """Best-effort push to the same technical-bot chat, date-tagged and
    prefixed ERROR - same contract as error_notifier.notify_error()."""
    tagged = f"[{now_ist().strftime('%Y-%m-%d %H:%M IST')}] ERROR: {message}"
    if not TECH_TELEGRAM_BOT_TOKEN or not TECH_TELEGRAM_CHAT_ID:
        log.info("Technical-bot Telegram not configured (TECH_TELEGRAM_BOT_TOKEN/TECH_TELEGRAM_CHAT_ID) - skipped: %s", tagged)
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TECH_TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TECH_TELEGRAM_CHAT_ID, "text": tagged},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        log.exception("Failed to send technical-bot error-alert Telegram notification")
