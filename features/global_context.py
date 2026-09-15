"""Global market cues + macro background - both keyless, no signup
needed. Adapted from `main`'s trading_bot/market_context.py.

Yahoo Finance's chart endpoint is UNOFFICIAL - no auth, no key, no
documented rate-limit contract, just what a lot of retail tooling relies
on. Can break or get rate-limited without notice; treat as best-effort,
never load-bearing (same caveat `main` already carries this data with).

World Bank indicators are official and free but annual with a real lag
(often a year or more behind) - background context only, never a
same-day trading signal.

LIVE-ONLY, informational only - see features/market_context.py's
docstring for why (no historical series pulled, not backtestable, not
wired into any decision yet).
"""
import logging

import requests

log = logging.getLogger(__name__)

YAHOO_SYMBOLS = {
    "sp500": "^GSPC",
    "dow": "^DJI",
    "nasdaq": "^IXIC",
    "crude_wti": "CL=F",
    "usd_inr": "INR=X",
}

WORLD_BANK_INDICATORS = {
    "cpi_inflation_pct": "FP.CPI.TOTL.ZG",
    "gdp_growth_pct": "NY.GDP.MKTP.KD.ZG",
}


def _fetch_yahoo_meta(yahoo_symbol: str) -> dict | None:
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}",
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["chart"]["result"][0]["meta"]
    except Exception:
        log.exception("Could not fetch Yahoo quote for %s", yahoo_symbol)
        return None


def get_global_quote(yahoo_symbol: str) -> float | None:
    meta = _fetch_yahoo_meta(yahoo_symbol)
    return float(meta["regularMarketPrice"]) if meta else None


def get_global_change_pct(yahoo_symbol: str) -> float | None:
    """The session's own % change - used as an overnight leading cue for
    how Indian markets tend to open, since a US session already closed
    fully before India's opens."""
    meta = _fetch_yahoo_meta(yahoo_symbol)
    if meta is None or "regularMarketChangePercent" not in meta:
        return None
    return float(meta["regularMarketChangePercent"])


def get_global_cues() -> dict[str, float]:
    result = {}
    for name, symbol in YAHOO_SYMBOLS.items():
        value = get_global_quote(symbol)
        if value is not None:
            result[name] = value
    return result


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
