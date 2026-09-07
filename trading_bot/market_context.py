import logging
import os
import time
from dataclasses import dataclass, field

import requests

from trading_bot.rest_client import RestClient

log = logging.getLogger(__name__)

# Verified against a live account (2026-09-07): OIBuildup/gainersLosers are
# NOT covered by the documented rate-limit table - hitting them back-to-back
# gets HTTP 403 "Access denied because of exceeding access rate" on most
# calls (confirmed: looping 4 calls with no delay succeeded on ~2/4; with a
# 1.1s gap, still one 403 if the gap since the PREVIOUS loop's last call was
# too short). Pacing calls at this interval avoids it.
MARKET_DATA_CALL_INTERVAL_SECONDS = 1.2

# --- SmartAPI-native market-wide breadth/sentiment (no extra keys needed) ---

INDIA_VIX_TOKEN = "99926017"  # verified against a live scrip master dump, exch NSE


def get_india_vix(rest: RestClient) -> float | None:
    try:
        data = rest.get_ltp("NSE", "India VIX", INDIA_VIX_TOKEN)
        return float(data["ltp"])
    except Exception:
        log.exception("Could not fetch India VIX")
        return None


def get_pcr(rest: RestClient) -> list | None:
    try:
        return rest.get_pcr()
    except Exception:
        log.exception("Could not fetch PCR")
        return None


def get_oi_buildup(rest: RestClient) -> dict[str, list]:
    """Covers ALL underlyings in each of the 4 calls - fetch this ONCE per
    cycle and reuse the result for every underlying you need it for, rather
    than calling it per-underlying."""
    result = {}
    for datatype in ("Long Built Up", "Short Built Up", "Short Covering", "Long Unwinding"):
        time.sleep(MARKET_DATA_CALL_INTERVAL_SECONDS)  # always pace - see rate-limit note above
        try:
            result[datatype] = rest.get_oi_buildup(expirytype="NEAR", datatype=datatype)
        except Exception:
            log.exception("Could not fetch OI buildup for %s", datatype)
    return result


def get_gainers_losers(rest: RestClient) -> dict[str, list]:
    result = {}
    for datatype in ("PercOIGainers", "PercOILosers"):
        time.sleep(MARKET_DATA_CALL_INTERVAL_SECONDS)
        try:
            result[datatype] = rest.get_gainers_losers(datatype=datatype, expirytype="NEAR")
        except Exception:
            log.exception("Could not fetch gainers/losers for %s", datatype)
    return result


# --- global cues: unofficial, keyless Yahoo Finance quote endpoint ---
# No auth, no key, no rate-limit contract - it's not an official/documented
# API, just what a lot of retail tooling relies on. Can break or get
# rate-limited without notice; treat as best-effort, not load-bearing.

YAHOO_SYMBOLS = {
    "sp500": "^GSPC",
    "dow": "^DJI",
    "nasdaq": "^IXIC",
    "crude_wti": "CL=F",
    "usd_inr": "INR=X",
}


def get_global_quote(yahoo_symbol: str) -> float | None:
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}",
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception:
        log.exception("Could not fetch Yahoo quote for %s", yahoo_symbol)
        return None


def get_global_cues() -> dict[str, float]:
    result = {}
    for name, symbol in YAHOO_SYMBOLS.items():
        value = get_global_quote(symbol)
        if value is not None:
            result[name] = value
    return result


# --- macro: World Bank API (free, keyless) ---
# Annual data with a real lag (often a year or more behind) - background
# context only, never a same-day trading signal.

WORLD_BANK_INDICATORS = {
    "cpi_inflation_pct": "FP.CPI.TOTL.ZG",
    "gdp_growth_pct": "NY.GDP.MKTP.KD.ZG",
}


def get_macro_indicator(indicator_code: str, country: str = "IN") -> float | None:
    try:
        resp = requests.get(
            f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator_code}",
            params={"format": "json", "per_page": 1, "mrnev": 1},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data[1][0]["value"])
    except Exception:
        log.exception("Could not fetch World Bank indicator %s", indicator_code)
        return None


def get_macro_snapshot() -> dict[str, float]:
    result = {}
    for name, code in WORLD_BANK_INDICATORS.items():
        value = get_macro_indicator(code)
        if value is not None:
            result[name] = value
    return result


# --- news & economic calendar: need a free API key you provide ---
# Both are gracefully skipped (logged, not raised) if the key env var isn't
# set, so the rest of the snapshot still works without signing up for these.

NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")


def get_news_headlines(query: str = "RBI OR Nifty OR Sensex OR NSE India", page_size: int = 10) -> list | None:
    """newsapi.org - free tier requires a key (sign up at https://newsapi.org)."""
    if not NEWS_API_KEY:
        log.info("NEWS_API_KEY not set - skipping news headlines (free key at https://newsapi.org)")
        return None
    try:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": query, "sortBy": "publishedAt", "pageSize": page_size, "language": "en"},
            headers={"X-Api-Key": NEWS_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("articles", [])
    except Exception:
        log.exception("Could not fetch news headlines")
        return None


def get_economic_calendar() -> list | None:
    """finnhub.io economic calendar - free tier requires a key (sign up at
    https://finnhub.io). Useful as a filter to skip trading on days with a
    scheduled RBI policy decision, budget, or major global data release.
    """
    if not FINNHUB_API_KEY:
        log.info("FINNHUB_API_KEY not set - skipping economic calendar (free key at https://finnhub.io)")
        return None
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"token": FINNHUB_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("economicCalendar", [])
    except Exception:
        log.exception("Could not fetch economic calendar")
        return None


@dataclass
class MarketContext:
    india_vix: float | None = None
    pcr: list | None = None
    oi_buildup: dict[str, list] = field(default_factory=dict)
    gainers_losers: dict[str, list] = field(default_factory=dict)
    global_quotes: dict[str, float] = field(default_factory=dict)
    macro: dict[str, float] = field(default_factory=dict)
    news: list | None = None
    economic_calendar: list | None = None


def snapshot(rest: RestClient) -> MarketContext:
    """One-shot pull of everything above. Nothing here is wired into the
    condor strategy's entry/exit logic yet - it's informational, meant to be
    reviewed (via show_context.py) or used to build an actual filter once
    you decide what signal from this should gate a trade.
    """
    return MarketContext(
        india_vix=get_india_vix(rest),
        pcr=get_pcr(rest),
        oi_buildup=get_oi_buildup(rest),
        gainers_losers=get_gainers_losers(rest),
        global_quotes=get_global_cues(),
        macro=get_macro_snapshot(),
        news=get_news_headlines(),
        economic_calendar=get_economic_calendar(),
    )
