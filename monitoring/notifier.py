"""Telegram push for codex - deliberately modeled 1:1 on the `main`
branch's trading_bot/notifier.py (same notify(message, html=False) shape,
same best-effort/never-raises behavior, confirmed proven in production on
that project) rather than inventing a new notification pattern.

One real difference from that module, learned the hard way on `main`
tonight: that module reads os.environ at IMPORT TIME as module-level
constants, which silently breaks if the token isn't in the process's
environment yet when the module loads (e.g. a systemd service missing the
right EnvironmentFile= - exactly what happened there). This version reads
from config.settings.Settings() INSIDE notify() instead, at call time, so
it can never go stale relative to whatever the environment actually is by
the time a notification is sent.
"""
import logging

import requests

from config.settings import get_settings

log = logging.getLogger(__name__)


def notify(message: str, *, html: bool = False) -> None:
    """Best-effort Telegram push - logs and returns instead of raising if
    unconfigured or if the request fails, so a notification problem can
    never take down the trading loop.

    `html=True` sends with Telegram's HTML parse_mode - callers passing it
    are responsible for html.escape()-ing any dynamic text they
    interpolate, so a stray "<" can't break the tag structure.
    """
    settings = get_settings()
    token, chat_id = settings.codex_telegram_bot_token, settings.codex_telegram_chat_id
    if not token or not chat_id:
        log.info("Telegram not configured (CODEX_TELEGRAM_BOT_TOKEN/CODEX_TELEGRAM_CHAT_ID) - notification skipped: %s", message)
        return
    payload = {"chat_id": chat_id, "text": message}
    if html:
        payload["parse_mode"] = "HTML"
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
        resp.raise_for_status()
    except Exception:
        log.exception("Failed to send Telegram notification")
