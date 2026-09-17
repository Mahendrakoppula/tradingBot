"""Candles from ticks (spec §2 / §58): the tick→bar aggregator, the
per-(token, tf) store, and the Candle record itself.

Design rules:
- Bucketing uses the tick's EXCHANGE timestamp (epoch ms), never wall
  clock, so replay and live produce identical bars (§51 parity). Wall clock
  only drives `flush_timers`, which closes a bar when the market goes
  quiet across a boundary (no tick arrives to push it over).
- The in-progress bar is exposed as `current` and is NEVER handed to
  indicators; only `complete=True` bars enter a CandleStore.
- Angel One prices arrive in paise (int) - converted to rupees here, once.
- Index tokens report volume=0; the caller aggregates a futures proxy
  token separately and the loop joins them by bar ts (§ volume proxy).
- Late ticks (older than the current bar) are counted, not applied - a bar
  that has closed is immutable.
"""
import datetime as dt
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable

from trading_bot.engine.clock import TF_MINUTES, bar_start, in_session, next_boundary, session_open_at
from trading_bot.timeutil import IST

PAISE = 100.0


@dataclass
class Candle:
    ts: dt.datetime  # bar START, aware IST
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    oi: int | None = None
    tick_count: int = 0
    max_gap_ms: int = 0  # longest silence between consecutive ticks inside the bar
    complete: bool = False
    source: str = "ws"  # "ws" | "rest" | "replay"
    last_tick_ms: int | None = None
    cum_volume: int | None = None  # exchange's day-cumulative volume at the last tick

    def as_dict(self) -> dict:
        """The repo's candle-dict shape (indicators/structure/trend read
        this); `ts` is the bar start."""
        return {"ts": self.ts, "open": self.open, "high": self.high, "low": self.low,
                "close": self.close, "volume": self.volume, "oi": self.oi}

    @classmethod
    def from_rest(cls, row: list, tf: str, source: str = "rest") -> "Candle":
        """getCandleData row: [iso8601+05:30, o, h, l, c, v] (rupees)."""
        ts = dt.datetime.fromisoformat(row[0]).astimezone(IST)
        # daily rows carry the session-open time already; intraday rows are
        # bar starts - normalise both through bar_start to be safe.
        ts = bar_start(ts, tf)
        return cls(ts=ts, open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]),
                   volume=int(row[5] or 0), complete=True, source=source)


class CandleStore:
    """Bounded, ts-ordered list of COMPLETE candles for one (token, tf).
    `upsert` keeps the newest version of a bar (REST backfill may replace a
    WS-built bar and vice-versa - the caller decides precedence)."""

    def __init__(self, tf: str, max_len: int = 2500):
        self.tf = tf
        self.max_len = max_len
        self._bars: deque[Candle] = deque()

    def __len__(self) -> int:
        return len(self._bars)

    def __iter__(self):
        return iter(self._bars)

    @property
    def last(self) -> Candle | None:
        return self._bars[-1] if self._bars else None

    def upsert(self, c: Candle) -> bool:
        """Insert or replace by ts. Returns True when a NEW bar was added
        (as opposed to replacing an existing one)."""
        if not c.complete:
            raise ValueError("CandleStore only accepts complete candles")
        if not self._bars or c.ts > self._bars[-1].ts:
            self._bars.append(c)
            while len(self._bars) > self.max_len:
                self._bars.popleft()
            return True
        # replace or insert in order (rare: backfill)
        for i in range(len(self._bars) - 1, -1, -1):
            if self._bars[i].ts == c.ts:
                self._bars[i] = c
                return False
            if self._bars[i].ts < c.ts:
                self._bars.insert(i + 1, c)
                return True
        self._bars.appendleft(c)
        return True

    def extend(self, candles: list[Candle]) -> int:
        return sum(1 for c in sorted(candles, key=lambda x: x.ts) if self.upsert(c))

    def dicts(self) -> list[dict]:
        return [c.as_dict() for c in self._bars]

    def since(self, ts: dt.datetime) -> list[Candle]:
        return [c for c in self._bars if c.ts >= ts]

    def today(self, day: dt.date) -> list[Candle]:
        return [c for c in self._bars if c.ts.date() == day]

    def gaps(self, day: dt.date) -> list[dt.datetime]:
        """Missing bar starts for `day` between the first and last bar we
        hold for that day (intraday tfs only)."""
        if self.tf not in TF_MINUTES:
            return []
        bars = self.today(day)
        if len(bars) < 2:
            return []
        have = {c.ts for c in bars}
        step = dt.timedelta(minutes=TF_MINUTES[self.tf])
        t, end = bars[0].ts, bars[-1].ts
        missing = []
        while t < end:
            if t not in have:
                missing.append(t)
            t += step
        return missing


@dataclass
class _Building:
    bar: Candle
    close_at: dt.datetime


class BarAggregator:
    """Ticks in, closed candles out, for one token across several tfs.

    on_close(tf, candle) fires exactly once per (tf, bar) with
    complete=True. Higher tfs are built from ticks directly (not from 1m
    bars) so a missing 1m bar never poisons the 5m bar.
    """

    def __init__(self, token: str, tfs: tuple[str, ...], on_close: Callable[[str, Candle], None],
                 grace_seconds: float = 2.0, source: str = "ws"):
        self.token = token
        self.tfs = tuple(tfs)
        self.on_close = on_close
        self.grace = dt.timedelta(seconds=grace_seconds)
        self.source = source
        self._building: dict[str, _Building] = {}
        self._prev_cum: dict[str, int] = {}  # last bar's day-cumulative volume per tf
        self._last_closed: dict[str, dt.datetime] = {}  # a closed bar is immutable: later ticks for it are late
        self.late_ticks = 0
        self.out_of_session_ticks = 0  # pre-open / post-close prints, never bucketed
        self.last_tick_ms: int | None = None
        self.last_tick_at: dt.datetime | None = None  # wall clock of the last tick seen
        self.ticks = 0

    # --- inputs ------------------------------------------------------------------

    def on_tick(self, ltp_paise: int, exchange_ts_ms: int, cum_volume: int | None = None,
                oi: int | None = None, now: dt.datetime | None = None) -> list[tuple[str, Candle]]:
        """Apply one tick. Returns the bars this tick closed (also delivered
        via on_close) so callers can drive the loop synchronously."""
        price = ltp_paise / PAISE
        ts = dt.datetime.fromtimestamp(exchange_ts_ms / 1000.0, tz=IST)
        self.ticks += 1
        self.last_tick_at = now
        closed: list[tuple[str, Candle]] = []
        if not in_session(ts.time()):
            # pre-open (09:00-09:08 auction) and post-close prints would
            # otherwise be clamped into the first/last bar by bar_start
            self.out_of_session_ticks += 1
            return closed
        for tf in self.tfs:
            start = bar_start(ts, tf)
            b = self._building.get(tf)
            closed_ts = self._last_closed.get(tf)
            if (b is not None and start < b.bar.ts) or (closed_ts is not None and start <= closed_ts):
                self.late_ticks += 1
                continue
            if b is not None and start > b.bar.ts:
                closed.append((tf, self._close(tf)))
                b = None
            if b is None:
                b = _Building(
                    bar=Candle(ts=start, open=price, high=price, low=price, close=price, source=self.source),
                    close_at=next_boundary(ts, tf),
                )
                self._building[tf] = b
            bar = b.bar
            bar.high = max(bar.high, price)
            bar.low = min(bar.low, price)
            bar.close = price
            bar.tick_count += 1
            if bar.last_tick_ms is not None:
                bar.max_gap_ms = max(bar.max_gap_ms, exchange_ts_ms - bar.last_tick_ms)
            bar.last_tick_ms = exchange_ts_ms
            if oi is not None:
                bar.oi = oi
            if cum_volume is not None:
                if bar.cum_volume is None:
                    # first tick in this bar: volume since the previous bar's last
                    # cumulative. With no previous bar, the whole day-cumulative
                    # belongs to this bar ONLY if it is the session's first bar;
                    # after a mid-session restart it would be the day's volume so far
                    prev_cum = self._prev_cum.get(tf)
                    if prev_cum is not None:
                        bar.volume = max(0, cum_volume - prev_cum)
                    elif start == session_open_at(start.date()):
                        bar.volume = max(0, cum_volume)
                    else:
                        bar.volume = 0
                else:
                    bar.volume += max(0, cum_volume - bar.cum_volume)
                bar.cum_volume = cum_volume
        self.last_tick_ms = exchange_ts_ms
        return closed

    def flush_timers(self, now: dt.datetime) -> list[tuple[str, Candle]]:
        """Close any bar whose boundary + grace has passed on the wall clock
        without a tick pushing it over (quiet market, or feed silence)."""
        closed = []
        for tf in self.tfs:
            b = self._building.get(tf)
            if b is not None and now >= b.close_at + self.grace:
                closed.append((tf, self._close(tf)))
        return closed

    def flush_all(self) -> list[tuple[str, Candle]]:
        """EOD: emit every partial bar as INCOMPLETE (complete=False) so the
        loop can persist it flagged, without feeding it to indicators."""
        out = []
        for tf in list(self._building):
            b = self._building.pop(tf)
            out.append((tf, replace(b.bar, complete=False)))
        return out

    # --- state ---------------------------------------------------------------------

    def current(self, tf: str) -> Candle | None:
        b = self._building.get(tf)
        return b.bar if b else None

    def _close(self, tf: str) -> Candle:
        b = self._building.pop(tf)
        bar = b.bar
        bar.complete = True
        self._last_closed[tf] = bar.ts
        if bar.cum_volume is not None:
            self._prev_cum[tf] = bar.cum_volume
        self.on_close(tf, bar)
        return bar
