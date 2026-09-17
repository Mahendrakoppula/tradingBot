"""Session clock: bar boundaries, trading-day and session-phase helpers.

Bars are anchored at the 09:15 IST open, matching SmartAPI's own candle
alignment (docs/smartapi-reference.md getCandleData) - so a 30m bar runs
09:15-09:45, 09:45-10:15, ... and the final 30m bar 15:15-15:30 is a
15-minute stub, exactly as the broker reports it. The aggregator must
produce bars that match REST candles bar-for-bar, otherwise warmup history
and live bars would be misaligned and every indicator would be wrong at the
seam. tests/test_engine_clock.py pins these boundaries.

All times are IST. Use trading_bot.timeutil.now_ist() for "now" - the EC2
host clock is UTC (see that module's docstring for the bug that taught us).
"""
import datetime as dt

from trading_bot.timeutil import IST

SESSION_OPEN = dt.time(9, 15)
SESSION_CLOSE = dt.time(15, 30)

TF_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "30m": 30}
INTRADAY_TFS: tuple[str, ...] = ("1m", "5m", "30m")

# Spec §37 time-of-day buckets. "Do not assume any period is profitable" -
# these are labels for later statistical segmentation, not trading rules.
SESSION_PHASES: tuple[tuple[dt.time, dt.time, str], ...] = (
    (dt.time(9, 15), dt.time(10, 0), "09:15-10:00"),
    (dt.time(10, 0), dt.time(11, 30), "10:00-11:30"),
    (dt.time(11, 30), dt.time(13, 0), "11:30-13:00"),
    (dt.time(13, 0), dt.time(14, 0), "13:00-14:00"),
    (dt.time(14, 0), dt.time(15, 0), "14:00-15:00"),
    (dt.time(15, 0), dt.time(15, 30), "15:00-15:30"),
)


def session_open_at(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, SESSION_OPEN, tzinfo=IST)


def session_close_at(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, SESSION_CLOSE, tzinfo=IST)


def bar_start(ts: dt.datetime, tf: str) -> dt.datetime:
    """Start of the `tf` bar containing `ts`. Pre-open timestamps map to
    the 09:15 bar; post-close timestamps map to the last bar of the day
    (callers decide whether such ticks should count at all)."""
    if ts.tzinfo is None:
        raise ValueError("bar_start needs an aware datetime (IST)")
    ts = ts.astimezone(IST)
    if tf == "1d":
        return session_open_at(ts.date())
    minutes = TF_MINUTES[tf]
    open_ = session_open_at(ts.date())
    elapsed = int((ts - open_).total_seconds() // 60)
    if elapsed < 0:
        return open_
    last_start = int((session_close_at(ts.date()) - open_).total_seconds() // 60) - 1
    elapsed = min(elapsed, last_start)
    return open_ + dt.timedelta(minutes=(elapsed // minutes) * minutes)


def next_boundary(ts: dt.datetime, tf: str) -> dt.datetime:
    """The instant the bar containing `ts` closes. For intraday bars this is
    capped at the session close (the 15:15 30m bar closes at 15:30, not
    15:45); for "1d" it is the session close of that day."""
    start = bar_start(ts, tf)
    close = session_close_at(start.date())
    if tf == "1d":
        return close
    return min(start + dt.timedelta(minutes=TF_MINUTES[tf]), close)


def is_trading_day(day: dt.date, holidays: tuple[str, ...] | frozenset[str] = ()) -> bool:
    return day.weekday() < 5 and day.isoformat() not in set(holidays)


def in_session(t: dt.time) -> bool:
    return SESSION_OPEN <= t < SESSION_CLOSE


def session_phase(t: dt.time) -> str:
    if t < SESSION_OPEN:
        return "PRE_MARKET"
    for start, end, label in SESSION_PHASES:
        if start <= t < end:
            return label
    return "POST_MARKET"
