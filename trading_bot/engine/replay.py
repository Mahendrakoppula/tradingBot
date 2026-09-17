"""BACKTEST / restart-recovery tick source (spec §51): turns stored 1m
candles back into a deterministic tick stream and drives the SAME
ShadowLoop the live process runs. Two replays of the same candles must
produce byte-identical journals - tests/test_engine_replay.py asserts it.

Each 1m bar becomes four ticks - open, high, low, close - at fixed offsets
inside the minute (0s, 15s, 30s, 59s), so bars rebuilt by BarAggregator
equal the input bars exactly. Volume is replayed as a running cumulative
(the exchange's convention) so the aggregator's delta logic is exercised.
"""
import datetime as dt
from dataclasses import dataclass

from trading_bot.engine.candles import Candle
from trading_bot.engine.clock import session_close_at
from trading_bot.engine.quality import FeedHealth
from trading_bot.timeutil import IST

TICK_OFFSETS_S = (0, 15, 30, 59)


@dataclass(frozen=True)
class ReplayTick:
    token: str
    ltp: int  # paise
    exchange_timestamp: int  # epoch ms
    volume: int | None = None  # day-cumulative
    open_interest: int | None = None


class SimClock:
    """Wall clock for replay: sits at the latest tick's exchange time; once
    the source is exhausted it jumps past the session close so the loop's
    EOD path fires exactly as it would live."""

    def __init__(self, start: dt.datetime):
        self.now_value = start

    def now(self) -> dt.datetime:
        return self.now_value

    def advance_to(self, ts: dt.datetime) -> None:
        if ts > self.now_value:
            self.now_value = ts


def candles_to_ticks(token: str, candles: list[Candle | dict]) -> list[ReplayTick]:
    out: list[ReplayTick] = []
    cum = 0
    for c in candles:
        d = c.as_dict() if isinstance(c, Candle) else c
        ts: dt.datetime = d["ts"].astimezone(IST)
        base_ms = int(ts.timestamp() * 1000)
        vol = int(d.get("volume") or 0)
        prices = (d["open"], d["high"], d["low"], d["close"])
        n = len(prices)
        for k, (off, px) in enumerate(zip(TICK_OFFSETS_S, prices)):
            # spread the bar's volume across its ticks so the last tick carries the full cumulative
            cum_here = cum + (vol * (k + 1)) // n
            out.append(ReplayTick(token, int(round(px * 100)), base_ms + off * 1000, cum_here, d.get("oi")))
        cum += vol
    return out


class TickReplaySource:
    def __init__(self, candles_by_token: dict[str, list[Candle | dict]], clock: SimClock, day: dt.date):
        ticks: list[ReplayTick] = []
        for token, candles in candles_by_token.items():
            ticks.extend(candles_to_ticks(token, candles))
        # stable order: time, then token, so concurrent bars replay identically every time
        ticks.sort(key=lambda t: (t.exchange_timestamp, t.token))
        self._ticks = ticks
        self._i = 0
        self.clock = clock
        self.day = day
        self._started = False
        self.last_tick_at: dt.datetime | None = None

    def start(self) -> None:
        self._started = True

    def next(self, timeout: float = 0.0):
        if self._i >= len(self._ticks):
            # exhausted: jump the clock past the close so timers/EOD fire
            self.clock.advance_to(session_close_at(self.day) + dt.timedelta(seconds=10))
            return None
        t = self._ticks[self._i]
        self._i += 1
        ts = dt.datetime.fromtimestamp(t.exchange_timestamp / 1000, tz=IST)
        self.clock.advance_to(ts)
        self.last_tick_at = ts
        return t

    def done(self) -> bool:
        return self._i >= len(self._ticks)

    def health(self) -> FeedHealth:
        return FeedHealth(connected=True, last_tick_at=self.last_tick_at or self.clock.now(), reconnects=0,
                          last_error=None, clock_drift_seconds=0.0)

    def close(self) -> None:
        pass
