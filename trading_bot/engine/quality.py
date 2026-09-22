"""Data quality gate (spec §58): a single verdict per underlying per bar
that the regime engine turns into NO_TRADE when it is not OK.

    OK            fresh ticks, contiguous bars, sane prices, clock in sync
    STALE         no tick for > stale_tick_seconds while the session is open
    GAP           missing 1m bar(s) today that backfill hasn't covered
    INVALID       impossible bar (high < low, non-positive price, zero ticks)
    DISCONNECTED  feed reports no connection

Pure function over the store + feed health; no I/O.
"""
import datetime as dt
from dataclasses import dataclass, field

from trading_bot.engine.candles import Candle, CandleStore
from trading_bot.engine.clock import in_session
from trading_bot.timeutil import IST

STATUSES = ("OK", "STALE", "GAP", "INVALID", "DISCONNECTED")


@dataclass(frozen=True)
class FeedHealth:
    connected: bool
    last_tick_at: dt.datetime | None  # wall clock (IST) of the most recent tick, any token
    reconnects: int = 0
    last_error: str | None = None
    clock_drift_seconds: float | None = None  # |wall - exchange_ts| at last tick


@dataclass(frozen=True)
class DataQuality:
    status: str
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status == "OK"


def validate_candle(c: Candle) -> str | None:
    if c.high < c.low:
        return "high<low"
    if min(c.open, c.high, c.low, c.close) <= 0:
        return "non_positive_price"
    if not (c.low <= c.open <= c.high and c.low <= c.close <= c.high):
        return "open_close_outside_range"
    if c.complete and c.source == "ws" and c.tick_count == 0:
        return "zero_ticks"
    return None


def assess(store_1m: CandleStore, feed: FeedHealth, now: dt.datetime, *,
           stale_tick_seconds: float = 15.0, clock_drift_seconds: float = 5.0,
           max_gap_bars: int = 0, benign_gap_after: dt.time | None = None) -> DataQuality:
    """Priority: DISCONNECTED > INVALID > STALE > GAP > OK. Clock drift is
    reported as a reason but does not by itself downgrade (§86 flags it;
    the loop logs it).

    `benign_gap_after`: missing 1m bars that all start at or after this time
    (the entry cut-off) are reported but do not make the status GAP - no
    entry can happen after it, so tripping the global breaker for them only
    raises a CRITICAL alert and blocks the other underlyings' management.
    SENSEX (BSE) has dropped bars around 15:16 on both paper days and REST
    did not have them either."""
    reasons: list[str] = []
    now = now.astimezone(IST)
    open_now = in_session(now.time())
    if not feed.connected:
        return DataQuality("DISCONNECTED", ("feed_not_connected",) + ((feed.last_error,) if feed.last_error else ()))

    last = store_1m.last
    if last is not None:
        bad = validate_candle(last)
        if bad:
            return DataQuality("INVALID", (bad,))

    if open_now:
        if feed.last_tick_at is None:
            reasons.append("no_tick_yet")
            return DataQuality("STALE", tuple(reasons))
        age = (now - feed.last_tick_at).total_seconds()
        if age > stale_tick_seconds:
            return DataQuality("STALE", (f"last_tick_{age:.0f}s_ago",))

    gaps = store_1m.gaps(now.date())
    if len(gaps) > max_gap_bars:
        if benign_gap_after is not None and all(g.astimezone(IST).time() >= benign_gap_after for g in gaps):
            reasons.append(f"gap_after_cutoff_{len(gaps)}_bars_{gaps[0].strftime('%H:%M')}")
        else:
            return DataQuality("GAP", (f"missing_{len(gaps)}_1m_bars", gaps[0].strftime("%H:%M")))

    if feed.clock_drift_seconds is not None and abs(feed.clock_drift_seconds) > clock_drift_seconds:
        reasons.append(f"clock_drift_{feed.clock_drift_seconds:.1f}s")
    return DataQuality("OK", tuple(reasons))
