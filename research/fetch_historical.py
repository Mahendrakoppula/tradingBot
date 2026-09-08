"""Pull historical spot candle data from SmartAPI and cache it locally as
CSV under research/data/. Research-only script - not part of the live
trading_bot package, not imported by it.

Usage: python research/fetch_historical.py

Three tiers of data, for the technical-indicator strategy's backtest
(see .claude/plans/goofy-plotting-sedgewick.md):
- MINUTE_STOCK_SYMBOLS + the two indices: both ONE_MINUTE (9mo) and ONE_DAY
  (5yr) - the scalp/intraday tiers need 1-min granularity, but pulling it
  for the full F&O universe is infeasible (confirmed: ~10 calls/stock x
  210 stocks =~42 min and not needed for daily-only swing signals), so
  this stays a small, hand-picked liquid list, same spirit as the live
  bot's own WATCHLIST=NIFTY,BANKNIFTY default.
- The full F&O-eligible stock universe (~210 names, resolved live from the
  scrip master, NOT hardcoded): ONE_DAY only (5yr) - cheap, ~1 call/stock,
  used for the swing tier's daily SMA/EMA/RSI/MACD signals.
"""
import csv
import datetime as dt
import logging
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_bot.auth import Session
from trading_bot.config import Config
from trading_bot.instruments import InstrumentLookup
from trading_bot.options import find_spot_instrument
from trading_bot.rest_client import ApiError, RestClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Index spot tokens, confirmed live against the scrip master (see project memory).
SYMBOLS = {
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009",
}
EXCHANGE = "NSE"

# Small, hand-picked liquid large-caps (all NIFTY50 constituents per
# breadth.py, all confirmed F&O-eligible) that also get 1-minute data, for
# the scalp/intraday tiers. Everything else in the F&O universe gets
# daily-only (see resolve_fo_stock_tokens below).
MINUTE_STOCK_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "ITC", "LT",
    "AXISBANK", "KOTAKBANK", "BHARTIARTL", "HINDUNILVR", "TATASTEEL", "MARUTI", "SUNPHARMA",
]


def resolve_fo_stock_tokens(instruments: list[dict]) -> dict[str, str]:
    """Every F&O-eligible stock underlying (has listed options -
    instrumenttype OPTSTK in the scrip master, live-verified count: 210),
    resolved to its own EQUITY spot token (not any option contract's token)
    via options.find_spot_instrument - i.e. the stock's own price history,
    which is what the technical strategy's signals need."""
    names = sorted({r["name"] for r in instruments if r.get("instrumenttype") == "OPTSTK" and r.get("exch_seg") == "NFO"})
    tokens: dict[str, str] = {}
    for name in names:
        try:
            tokens[name] = find_spot_instrument(instruments, name)["token"]
        except LookupError:
            log.warning("No equity spot instrument found for F&O stock %s - skipping", name)
    return tokens

# SmartAPI getCandleData rate limit: 3 req/sec, 150/min, 5000/day per docs,
# but empirically the very first call after login got a 403 "exceeding
# access rate" even from a cold start - the real limiter is stricter/flakier
# than documented, so pace well under the documented ceiling.
SLEEP_BETWEEN_CALLS = 1.2

ONE_MINUTE_MAX_DAYS = 30
ONE_DAY_MAX_DAYS = 2000


def _chunk_ranges(start: dt.date, end: dt.date, max_days: int):
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=max_days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + dt.timedelta(days=1)


def _fetch_chunked(client: RestClient, symboltoken: str, interval: str, start: dt.date, end: dt.date, max_days: int) -> list:
    rows = []
    for chunk_start, chunk_end in _chunk_ranges(start, end, max_days):
        fromdate = f"{chunk_start.isoformat()} 09:00"
        todate = f"{chunk_end.isoformat()} 15:30"
        for attempt in range(6):
            try:
                data = client.get_candle_data(EXCHANGE, symboltoken, interval, fromdate, todate)
                break
            except (ApiError, ValueError, requests.exceptions.RequestException) as e:
                # ValueError covers requests' JSONDecodeError - the rate
                # limiter sometimes returns a plain-text 403 body ("Access
                # denied because of exceeding access rate") instead of JSON,
                # which resp.json() can't parse. RequestException covers
                # transient network failures (timeouts, connection resets) -
                # confirmed live: an unhandled ConnectTimeout killed a
                # 200+-stock unattended run outright before this was added.
                log.warning("getCandleData failed (attempt %d) for %s %s->%s: %s", attempt + 1, symboltoken, fromdate, todate, e)
                time.sleep(3.0 * (attempt + 1))
        else:
            log.error("giving up on chunk %s -> %s after 6 attempts", fromdate, todate)
            data = []
        log.info("  %s %s: %s -> %s -> %d candles", symboltoken, interval, chunk_start, chunk_end, len(data or []))
        rows.extend(data or [])
        time.sleep(SLEEP_BETWEEN_CALLS)
    return rows


def _write_csv(path: Path, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    log.info("wrote %d rows -> %s", len(rows), path)


def _pull_minute_and_daily(client: RestClient, name: str, token: str, today: dt.date) -> None:
    log.info("=== %s (token %s): 1-min + daily ===", name, token)
    minute_start = today - dt.timedelta(days=9 * 30)
    log.info("fetching ONE_MINUTE %s -> %s", minute_start, today)
    minute_rows = _fetch_chunked(client, token, "ONE_MINUTE", minute_start, today, ONE_MINUTE_MAX_DAYS)
    _write_csv(DATA_DIR / f"{name}_1min.csv", minute_rows)

    daily_start = today - dt.timedelta(days=5 * 365)
    log.info("fetching ONE_DAY %s -> %s", daily_start, today)
    daily_rows = _fetch_chunked(client, token, "ONE_DAY", daily_start, today, ONE_DAY_MAX_DAYS)
    _write_csv(DATA_DIR / f"{name}_1day.csv", daily_rows)


def _pull_daily_only(client: RestClient, name: str, token: str, today: dt.date) -> None:
    daily_start = today - dt.timedelta(days=5 * 365)
    daily_rows = _fetch_chunked(client, token, "ONE_DAY", daily_start, today, ONE_DAY_MAX_DAYS)
    _write_csv(DATA_DIR / f"{name}_1day.csv", daily_rows)


def main():
    cfg = Config.from_env()
    session = Session(cfg)
    session.login()
    client = RestClient(session)
    now = dt.datetime.now()
    # Requesting a chunk whose range includes TODAY before the market has
    # opened makes the API reject it outright ("From datetime can't be
    # greater than current datetime") - confirmed live, wasted ~70s/symbol
    # retrying 6 times for nothing. One day of "today so far" data doesn't
    # matter for a historical backtest, so just don't ask for it yet.
    today = now.date() if now.time() >= dt.time(9, 16) else now.date() - dt.timedelta(days=1)

    try:
        instruments = InstrumentLookup(cfg.scrip_master_url)
        instruments.load()

        # Tier 1: indices + curated liquid stocks - both 1-min and daily.
        minute_tier = dict(SYMBOLS)
        for name in MINUTE_STOCK_SYMBOLS:
            try:
                minute_tier[name] = find_spot_instrument(instruments.instruments, name)["token"]
            except LookupError:
                log.warning("No equity spot instrument found for %s - skipping from minute tier", name)
        for name, token in minute_tier.items():
            _pull_minute_and_daily(client, name, token, today)

        # Tier 2: the rest of the F&O-eligible stock universe - daily only.
        fo_tokens = resolve_fo_stock_tokens(instruments.instruments)
        daily_only = {name: token for name, token in fo_tokens.items() if name not in minute_tier}
        log.info("=== daily-only tier: %d F&O stocks (already have %d in the minute tier) ===",
                  len(daily_only), len(minute_tier) - len(SYMBOLS))
        for name, token in daily_only.items():
            log.info("--- %s (token %s): daily only ---", name, token)
            _pull_daily_only(client, name, token, today)
    finally:
        session.logout()


if __name__ == "__main__":
    main()
