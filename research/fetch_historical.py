"""Pull historical spot candle data for NIFTY/BANKNIFTY from SmartAPI and
cache it locally as CSV under research/data/. Research-only script - not
part of the live trading_bot package, not imported by it.

Usage: python research/fetch_historical.py
"""
import csv
import datetime as dt
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_bot.auth import Session
from trading_bot.config import Config
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
            except (ApiError, ValueError) as e:
                # ValueError covers requests' JSONDecodeError - the rate
                # limiter sometimes returns a plain-text 403 body ("Access
                # denied because of exceeding access rate") instead of JSON,
                # which resp.json() can't parse.
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


def main():
    cfg = Config.from_env()
    session = Session(cfg)
    session.login()
    client = RestClient(session)

    today = dt.date.today()

    try:
        for name, token in SYMBOLS.items():
            log.info("=== %s (token %s) ===", name, token)

            # 1-minute candles, last 9 months - the actual granularity the
            # ORB backtest needs (opening-range window is minutes, not days).
            minute_start = today - dt.timedelta(days=9 * 30)
            log.info("fetching ONE_MINUTE %s -> %s", minute_start, today)
            minute_rows = _fetch_chunked(client, token, "ONE_MINUTE", minute_start, today, ONE_MINUTE_MAX_DAYS)
            _write_csv(DATA_DIR / f"{name}_1min.csv", minute_rows)

            # Daily candles, last 5 years - longer-horizon context, cheap to
            # pull (well within the 2000-day-per-request cap, one request).
            daily_start = today - dt.timedelta(days=5 * 365)
            log.info("fetching ONE_DAY %s -> %s", daily_start, today)
            daily_rows = _fetch_chunked(client, token, "ONE_DAY", daily_start, today, ONE_DAY_MAX_DAYS)
            _write_csv(DATA_DIR / f"{name}_1day.csv", daily_rows)
    finally:
        session.logout()


if __name__ == "__main__":
    main()
