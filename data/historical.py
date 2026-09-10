"""Chunked historical OHLCV fetch for the three in-scope indices, across
every interval the multi-timeframe engine (Phase 3) will need. Adapted
from research/fetch_historical.py's proven chunking/retry/rate-limit
logic on `main` (live-tuned: a 403 rate-limit response is sometimes plain
text, not JSON, and an unhandled ConnectTimeout has killed an unattended
run before) rather than re-deriving it from the docs alone.
"""
import datetime as dt
import logging
import time

import requests

from data.broker_client import ApiError, HistoricalDataClient
from data.timeutil import now_ist

log = logging.getLogger(__name__)

# SmartAPI getCandleData: documented 3 req/sec, 150/min, 5000/day, but the
# real limiter is empirically stricter/flakier than documented (see
# research/fetch_historical.py) - pace well under the ceiling.
SLEEP_BETWEEN_CALLS_SECONDS = 1.2
MAX_RETRIES_PER_CHUNK = 6

# docs/smartapi-reference.md's max-days-per-request table.
MAX_DAYS_BY_INTERVAL = {
    "ONE_MINUTE": 30,
    "THREE_MINUTE": 60,
    "FIVE_MINUTE": 100,
    "TEN_MINUTE": 100,
    "FIFTEEN_MINUTE": 200,
    "THIRTY_MINUTE": 200,
    "ONE_HOUR": 400,
    "ONE_DAY": 2000,
}


def _chunk_ranges(start: dt.date, end: dt.date, max_days: int):
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=max_days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + dt.timedelta(days=1)


def last_completed_trading_date(now: dt.datetime | None = None) -> dt.date:
    """A chunk whose range includes TODAY before market open is rejected
    outright by the API ("From datetime can't be greater than current
    datetime") - confirmed live, wastes a full retry budget for nothing.
    One day of "today so far" doesn't matter for historical backfill, so
    just don't ask for it until the market's been open a few minutes."""
    now = now or now_ist()
    return now.date() if now.time() >= dt.time(9, 16) else now.date() - dt.timedelta(days=1)


def fetch_history(
    client: HistoricalDataClient,
    exchange: str,
    symboltoken: str,
    interval: str,
    start: dt.date,
    end: dt.date,
) -> list[dict]:
    """Returns a flat list of {timestamp, open, high, low, close, volume}
    dicts spanning [start, end], fetched in interval-appropriate chunks."""
    if interval not in MAX_DAYS_BY_INTERVAL:
        raise ValueError(f"Unknown interval {interval!r} - must be one of {sorted(MAX_DAYS_BY_INTERVAL)}")
    max_days = MAX_DAYS_BY_INTERVAL[interval]

    rows: list[dict] = []
    for chunk_start, chunk_end in _chunk_ranges(start, end, max_days):
        fromdate = f"{chunk_start.isoformat()} 09:00"
        todate = f"{chunk_end.isoformat()} 15:30"
        data = None
        for attempt in range(MAX_RETRIES_PER_CHUNK):
            try:
                data = client.get_candle_data(exchange, symboltoken, interval, fromdate, todate)
                break
            except (ApiError, ValueError, requests.exceptions.RequestException) as e:
                log.warning("getCandleData failed (attempt %d/%d) for %s %s %s->%s: %s",
                            attempt + 1, MAX_RETRIES_PER_CHUNK, symboltoken, interval, fromdate, todate, e)
                time.sleep(3.0 * (attempt + 1))
        if data is None:
            log.error("giving up on chunk %s -> %s after %d attempts", fromdate, todate, MAX_RETRIES_PER_CHUNK)
            data = []
        for candle in data:
            # SmartAPI candle shape: [timestamp_iso, open, high, low, close, volume]
            ts, o, h, l, c, v = candle
            rows.append({"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v})
        log.info("%s %s: %s -> %s -> %d candles", symboltoken, interval, chunk_start, chunk_end, len(data))
        time.sleep(SLEEP_BETWEEN_CALLS_SECONDS)
    return rows
