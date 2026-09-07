import logging
import time

from trading_bot.rest_client import RestClient

log = logging.getLogger(__name__)

# Verified live against the actual NSE scrip master (2026-09-07) - every name
# below resolved to a real NSE equity row. Indices rebalance semi-annually
# (NSE reviews in March/September), so re-verify this list periodically -
# a missing/renamed constituent just logs a warning and gets skipped, it
# won't crash, but breadth numbers will quietly undercount if it drifts.
NIFTY50_CONSTITUENTS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN",
    "SUNPHARMA", "TCS", "TATACONSUM", "TMPV", "TATASTEEL",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

BANKNIFTY_CONSTITUENTS = [
    "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK",
    "UNIONBANK", "BANKBARODA", "CANBK", "FEDERALBNK", "AUBANK",
    "INDUSINDBK", "IDFCFIRSTB", "YESBANK",
]

QUOTE_BATCH_SIZE = 50  # API limit per exchange per request


def resolve_constituents(instruments: list[dict], names: list[str]) -> list[dict]:
    by_name = {}
    for row in instruments:
        if row.get("exch_seg") == "NSE" and row.get("instrumenttype") == "" and str(row.get("symbol", "")).endswith("-EQ"):
            by_name[row["name"]] = row

    result = []
    for name in names:
        row = by_name.get(name)
        if row is None:
            log.warning("Constituent %s not found in scrip master - index may have rebalanced, update breadth.py", name)
            continue
        result.append(row)
    return result


def get_quotes(rest: RestClient, rows: list[dict]) -> list[dict]:
    """Fetches FULL-mode quotes (needed for tradeVolume - OHLC mode doesn't
    include it) for every row, batching at the API's 50-symbols-per-request
    limit."""
    tokens = [row["token"] for row in rows]
    all_fetched = []
    for i in range(0, len(tokens), QUOTE_BATCH_SIZE):
        batch = tokens[i : i + QUOTE_BATCH_SIZE]
        if i > 0:
            time.sleep(0.3)
        data = rest.get_quote("FULL", {"NSE": batch})
        all_fetched.extend(data.get("fetched", []))
    return all_fetched


def compute_breadth(quotes: list[dict]) -> dict:
    def pct(q):
        return float(q.get("percentChange", 0) or 0)

    def vol(q):
        return int(float(q.get("tradeVolume", 0) or 0))

    advancing = sum(1 for q in quotes if pct(q) > 0)
    declining = sum(1 for q in quotes if pct(q) < 0)
    unchanged = len(quotes) - advancing - declining
    total_volume = sum(vol(q) for q in quotes)

    by_pct_desc = sorted(quotes, key=pct, reverse=True)
    by_volume_desc = sorted(quotes, key=vol, reverse=True)

    return {
        "count": len(quotes),
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,
        "total_volume": total_volume,
        "top_gainers": [(q["tradingSymbol"], pct(q)) for q in by_pct_desc[:3]],
        "top_losers": [(q["tradingSymbol"], pct(q)) for q in by_pct_desc[-3:][::-1]],
        "top_volume": [(q["tradingSymbol"], vol(q)) for q in by_volume_desc[:3]],
    }


def get_breadth(rest: RestClient, instruments: list[dict], names: list[str]) -> dict | None:
    """One-call convenience: resolve names -> quote -> summarize. Returns
    None if nothing resolved (e.g. instruments not loaded yet)."""
    rows = resolve_constituents(instruments, names)
    if not rows:
        return None
    quotes = get_quotes(rest, rows)
    return compute_breadth(quotes)
