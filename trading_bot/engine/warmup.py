"""Startup history warmup over REST getCandleData (spec §2), then the
one-off intraday backfill after the WebSocket is up.

The plan: ONE_DAY 400d / THIRTY_MINUTE 90d / FIVE_MINUTE 21d / ONE_MINUTE 7d
per token, chunked to the API's max-days-per-request table, paced through
`RateLimiter` - ~18 calls at startup and then near-zero REST for the day.

`rest` is anything with `get_candle_data(exchange, token, interval,
fromdate, todate) -> list[row]`; tests pass a fake.
"""
import datetime as dt
import logging
import time
from dataclasses import dataclass

from trading_bot.engine.candles import Candle, CandleStore
from trading_bot.engine.clock import next_boundary, session_close_at, session_open_at
from trading_bot.engine.ratelimit import RateLimiter, paced_call
from trading_bot.timeutil import IST

log = logging.getLogger(__name__)

INTERVAL = {"1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "30m": "THIRTY_MINUTE", "1d": "ONE_DAY"}
# docs/smartapi-reference.md "Max days per single request"; kept a little
# under the limit so a boundary-day rounding never trips a rejection.
MAX_DAYS = {"1m": 28, "5m": 95, "30m": 190, "1d": 1900}
FMT = "%Y-%m-%d %H:%M"


@dataclass(frozen=True)
class WarmupPlan:
    days: dict[str, int]  # tf -> calendar days of history

    @classmethod
    def from_config(cls, cfg) -> "WarmupPlan":
        return cls({"1m": cfg.warmup_days_1m, "5m": cfg.warmup_days_5m,
                    "30m": cfg.warmup_days_30m, "1d": cfg.warmup_days_1d})


@dataclass(frozen=True)
class Instrument:
    underlying: str
    exchange: str  # "NSE" | "BSE" | "NFO" | "BFO"
    token: str
    role: str = "spot"  # "spot" | "volume_proxy"


@dataclass
class WarmupReport:
    calls: int = 0
    bars: dict = None  # (token, tf) -> count
    errors: list = None

    def __post_init__(self):
        self.bars = self.bars or {}
        self.errors = self.errors or []


def _chunks(start: dt.datetime, end: dt.datetime, max_days: int):
    cur = start
    step = dt.timedelta(days=max_days)
    while cur < end:
        nxt = min(cur + step, end)
        yield cur, nxt
        cur = nxt


def fetch_history(rest, inst: Instrument, tf: str, start: dt.datetime, end: dt.datetime,
                  limiter: RateLimiter | None, report: WarmupReport | None = None,
                  sleep=time.sleep) -> list[Candle]:
    out: list[Candle] = []
    for a, b in _chunks(start, end, MAX_DAYS[tf]):
        rows = paced_call(rest.get_candle_data, inst.exchange, inst.token, INTERVAL[tf],
                          a.strftime(FMT), b.strftime(FMT), limiter=limiter, sleep=sleep) or []
        if report is not None:
            report.calls += 1
        out.extend(Candle.from_rest(r, tf) for r in rows)
    # de-duplicate on ts (chunk boundaries can overlap by one bar)
    seen: dict[dt.datetime, Candle] = {}
    for c in out:
        seen[c.ts] = c
    return [seen[k] for k in sorted(seen)]


def warm_up(rest, instruments: list[Instrument], plan: WarmupPlan, stores: dict[tuple[str, str], CandleStore],
            now: dt.datetime, limiter: RateLimiter | None = None, sleep=time.sleep) -> WarmupReport:
    """Fill `stores[(token, tf)]` with history up to `now` (exclusive of any
    bar still forming). Creates stores that are missing."""
    report = WarmupReport()
    end = now.astimezone(IST)
    for inst in instruments:
        for tf, days in plan.days.items():
            start = (end - dt.timedelta(days=days)).replace(hour=9, minute=15, second=0, microsecond=0)
            key = (inst.token, tf)
            store = stores.setdefault(key, CandleStore(tf))
            try:
                candles = fetch_history(rest, inst, tf, start, end, limiter, report, sleep=sleep)
            except Exception as exc:  # noqa: BLE001 - one bad tf must not abort the rest
                log.error("warmup %s %s failed: %s", inst.underlying, tf, exc)
                report.errors.append((inst.underlying, tf, str(exc)))
                continue
            # a bar whose close is still in the future is the forming bar - drop it
            candles = [c for c in candles if _bar_closed(c, tf, end)]
            store.extend(candles)
            report.bars[key] = len(store)
            log.info("warmup %s %s: %d bars (%d total in store)", inst.underlying, tf, len(candles), len(store))
    return report


def backfill_today_1m(rest, inst: Instrument, store: CandleStore, now: dt.datetime,
                      limiter: RateLimiter | None = None) -> int:
    """After the WS is connected: re-pull today's 1m bars once and merge, so
    a restart mid-session (or a WS gap before subscribe) leaves no hole.
    REST bars win over WS-built bars for the same ts. Returns bars merged."""
    end = now.astimezone(IST)
    start = session_open_at(end.date())
    if end <= start:
        return 0
    candles = fetch_history(rest, inst, "1m", start, min(end, session_close_at(end.date())), limiter)
    candles = [c for c in candles if _bar_closed(c, "1m", end)]
    for c in candles:
        store.upsert(c)
    return len(candles)


def _bar_closed(c: Candle, tf: str, now: dt.datetime) -> bool:
    return next_boundary(c.ts, tf) <= now
