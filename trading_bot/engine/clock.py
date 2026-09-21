"""Session clock: bar boundaries, trading-day and session-phase helpers.

Bars are anchored at the session open, matching SmartAPI's own candle
alignment (docs/smartapi-reference.md getCandleData) - so on NSE a 30m bar
runs 09:15-09:45, 09:45-10:15, ... and the final 30m bar 15:15-15:30 is a
15-minute stub, exactly as the broker reports it. The aggregator must
produce bars that match REST candles bar-for-bar, otherwise warmup history
and live bars would be misaligned and every indicator would be wrong at the
seam. tests/test_engine_clock.py pins these boundaries.

Session PROFILES: one engine process trades one exchange session. The
default is NSE/BSE cash-market hours (09:15-15:30). The MCX commodity
session (09:00-23:30 IST, 23:55 during US winter time) is selected with
`configure_session("MCX")` once at startup (run_technical.main from
TECH_SESSION) - process-global, like the exchange it describes. A process
never mixes sessions: the crude-oil spike runs as its own systemd unit
(deploy/trading-bot-mcx.service) with its own database.

All times are IST. Use trading_bot.timeutil.now_ist() for "now" - the EC2
host clock is UTC (see that module's docstring for the bug that taught us).
"""
import datetime as dt
from dataclasses import dataclass, replace

from trading_bot.timeutil import IST

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

# MCX crude oil: the day is driven by which global market is open. Asia
# (thin), Europe (15:00 IST on), the US pre-open/open (17:00-20:00 IST, the
# EIA inventory print is Wednesday 20:00 IST) and the US session proper.
# Labels for segmentation, exactly like the NSE ones.
MCX_SESSION_PHASES: tuple[tuple[dt.time, dt.time, str], ...] = (
    (dt.time(9, 0), dt.time(12, 0), "09:00-12:00"),
    (dt.time(12, 0), dt.time(15, 0), "12:00-15:00"),
    (dt.time(15, 0), dt.time(17, 0), "15:00-17:00"),
    (dt.time(17, 0), dt.time(20, 0), "17:00-20:00"),
    (dt.time(20, 0), dt.time(23, 30), "20:00-23:30"),
)


@dataclass(frozen=True)
class SessionProfile:
    name: str
    open: dt.time
    close: dt.time
    phases: tuple[tuple[dt.time, dt.time, str], ...]

    def with_close(self, close: dt.time) -> "SessionProfile":
        """The same session ending at `close` (MCX 23:55 in US winter time):
        the last phase stretches or shrinks to the new close."""
        if close <= self.open:
            raise ValueError(f"session close {close} must be after open {self.open}")
        phases = tuple(p for p in self.phases if p[0] < close)
        if phases:
            s, _, label = phases[-1]
            phases = phases[:-1] + ((s, close, f"{s:%H:%M}-{close:%H:%M}" if phases[-1][1] != close else label),)
        return replace(self, close=close, phases=phases)


NSE_SESSION = SessionProfile("NSE", dt.time(9, 15), dt.time(15, 30), SESSION_PHASES)
MCX_SESSION = SessionProfile("MCX", dt.time(9, 0), dt.time(23, 30), MCX_SESSION_PHASES)
SESSION_PROFILES: dict[str, SessionProfile] = {"NSE": NSE_SESSION, "MCX": MCX_SESSION}

# NSE constants kept by name: the default session, and what tests pin.
SESSION_OPEN = NSE_SESSION.open
SESSION_CLOSE = NSE_SESSION.close

_active: SessionProfile = NSE_SESSION


def configure_session(name: str, close: dt.time | None = None) -> SessionProfile:
    """Select the process-wide session profile (once, at startup)."""
    global _active
    try:
        prof = SESSION_PROFILES[name.upper()]
    except KeyError:
        raise ValueError(f"unknown session profile {name!r}; known: {sorted(SESSION_PROFILES)}") from None
    if close is not None and close != prof.close:
        prof = prof.with_close(close)
    _active = prof
    return prof


def active_session() -> SessionProfile:
    return _active


def session_open() -> dt.time:
    return _active.open


def session_close() -> dt.time:
    return _active.close


def session_open_at(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, _active.open, tzinfo=IST)


def session_close_at(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, _active.close, tzinfo=IST)


def bar_start(ts: dt.datetime, tf: str) -> dt.datetime:
    """Start of the `tf` bar containing `ts`. Pre-open timestamps map to
    the opening bar; post-close timestamps map to the last bar of the day
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
    return _active.open <= t < _active.close


def session_phase(t: dt.time) -> str:
    if t < _active.open:
        return "PRE_MARKET"
    for start, end, label in _active.phases:
        if start <= t < end:
            return label
    return "POST_MARKET"
