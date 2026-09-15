"""Economic-events calendar - finnhub.io free tier. Gracefully skipped
(logged, not raised) if no key is configured, same as `main`'s own
trading_bot/market_context.py - the rest of the context engine works
fine without signing up for this. User is obtaining a free key
(config/settings.py's finnhub_api_key); this module is ready to receive
it with zero rework once set.

LIVE-ONLY, informational only - see features/market_context.py's
docstring for why (no historical calendar archive pulled, not
backtestable, not wired into any decision yet).
"""
import logging

import requests

log = logging.getLogger(__name__)


def get_economic_calendar(api_key: str) -> list | None:
    """Useful as a future filter to skip trading on days with a scheduled
    RBI policy decision, budget, or major global data release - not used
    for that yet, collected only."""
    if not api_key:
        log.info("FINNHUB_API_KEY not set - skipping economic calendar (free key at https://finnhub.io)")
        return None
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"token": api_key},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("economicCalendar", [])
    except Exception:
        log.exception("Could not fetch economic calendar")
        return None
