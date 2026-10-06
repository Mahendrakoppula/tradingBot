"""Exchange holiday calendars for NSE/BSE and MCX.

Why this file exists, in money: on Friday 2 October 2026 (Gandhi Jayanti) the
daily bot opened NIFTY06OCT2621950PE at 09:16 against the PREVIOUS session's
closing quote, held it through the holiday and the weekend, and closed it on
Monday for -Rs.3,958.50 - the single worst trade in that bot's 27-trade
history. Nothing was broken except the calendar: `TECH_HOLIDAYS` was empty
(`deploy/config.env` said "maintain by hand" and nobody ever did), so the only
guard was `day.weekday() < 5` and every weekday holiday looked tradeable.
The feed told the truth the whole time - zero candles, zero ticks, two
feed_stale alerts - but nothing turned that into "do not enter".

So the calendar lives in code, as data, with the dates checked against the
exchanges' published lists rather than left to an operator to remember.

MCX is NOT NSE with different dates. In 2026 MCX shuts for the WHOLE day on
only four of these sixteen dates; on eleven of them the morning session is
closed and the EVENING session (17:00-23:30 IST) trades normally, because
crude follows the US clock and MCX stays open for it. Treating MCX holidays
as full-day closures would have thrown away eleven real crude sessions - the
opposite failure to the one above, and just as wrong. Hence a status per
(exchange, date) instead of one flat set of "closed" days.

Maintenance: both exchanges publish the next year's list around December.
These are 2026 only; `status()` raises for a date outside a known year rather
than quietly reporting "normal trading day" for a year it knows nothing
about - an unknown year is exactly when a silent wrong answer costs money.
"""
import datetime as dt

# Day statuses. None (absent from a calendar) = ordinary full trading day.
CLOSED = "CLOSED"              # no trading in any session
MORNING_ONLY = "MORNING_ONLY"  # MCX: 09:00-17:00 trades, evening shut
EVENING_ONLY = "EVENING_ONLY"  # MCX: evening (17:00-) trades, morning shut

# MCX splits its day here: morning 09:00-17:00, evening 17:00-23:30 (23:55 in
# US winter time, see clock.MCX_SESSION). Used to narrow a partial day.
MCX_EVENING_OPEN = dt.time(17, 0)

# NSE/BSE trading holidays 2026 (equity, F&O, SLB) - all full-day closures.
# Weekend-falling holidays are deliberately omitted: the weekday check already
# covers them, and listing them would imply this set is the only guard.
# Muhurat trading on Sunday 8 Nov 2026 is a special session, not a holiday,
# and is NOT listed - the weekday check keeps the engine out of it, which is
# the behaviour we want for a one-hour ceremonial session.
NSE_2026: dict[str, str] = {
    "2026-01-15": CLOSED,  # Municipal Election, Maharashtra
    "2026-01-26": CLOSED,  # Republic Day
    "2026-03-03": CLOSED,  # Holi (2nd day)
    "2026-03-26": CLOSED,  # Shri Ram Navami
    "2026-03-31": CLOSED,  # Shri Mahavir Jayanti
    "2026-04-03": CLOSED,  # Good Friday
    "2026-04-14": CLOSED,  # Dr. Babasaheb Ambedkar Jayanti
    "2026-05-01": CLOSED,  # Maharashtra Day
    "2026-05-28": CLOSED,  # Bakri Id
    "2026-06-26": CLOSED,  # Moharram
    "2026-09-14": CLOSED,  # Ganesh Chaturthi
    "2026-10-02": CLOSED,  # Mahatma Gandhi Jayanti - the one that cost Rs.3,958
    "2026-10-20": CLOSED,  # Dussehra
    "2026-11-10": CLOSED,  # Diwali Balipratipada
    "2026-11-24": CLOSED,  # Guru Nanak Jayanti
    "2026-12-25": CLOSED,  # Christmas
}

# MCX trading holidays 2026. Same dates as NSE plus New Year's Day, but mostly
# PARTIAL: morning shut, evening open. Only Republic Day, Good Friday, Gandhi
# Jayanti and Christmas close the full day.
MCX_2026: dict[str, str] = {
    "2026-01-01": MORNING_ONLY,  # New Year's Day - morning trades, evening shut
    "2026-01-26": CLOSED,        # Republic Day
    "2026-03-03": EVENING_ONLY,  # Holi (2nd day)
    "2026-03-26": EVENING_ONLY,  # Shri Ram Navami
    "2026-03-31": EVENING_ONLY,  # Shri Mahavir Jayanti
    "2026-04-03": CLOSED,        # Good Friday
    "2026-04-14": EVENING_ONLY,  # Dr. Babasaheb Ambedkar Jayanti
    "2026-05-01": EVENING_ONLY,  # Maharashtra Day
    "2026-05-28": EVENING_ONLY,  # Bakri Id
    "2026-06-26": EVENING_ONLY,  # Moharram
    "2026-09-14": EVENING_ONLY,  # Ganesh Chaturthi
    "2026-10-02": CLOSED,        # Mahatma Gandhi Jayanti
    "2026-10-20": EVENING_ONLY,  # Dussehra
    "2026-11-10": EVENING_ONLY,  # Diwali Balipratipada
    "2026-11-24": EVENING_ONLY,  # Guru Nanak Jayanti
    "2026-12-25": CLOSED,        # Christmas
}

CALENDARS: dict[str, dict[str, str]] = {"NSE": NSE_2026, "MCX": MCX_2026}
# BSE (SENSEX options) follows the NSE list; the engine trades both in one process.
CALENDARS["BSE"] = NSE_2026

KNOWN_YEARS: frozenset[int] = frozenset({2026})


class UnknownYear(LookupError):
    """Asked about a year with no published calendar. Deliberately loud: a
    missing calendar must not read as 'ordinary trading day'."""


def _calendar(exchange: str) -> dict[str, str]:
    try:
        return CALENDARS[exchange.upper()]
    except KeyError:
        raise ValueError(f"no holiday calendar for exchange {exchange!r}; "
                         f"known: {sorted(CALENDARS)}") from None


def status(day: dt.date, exchange: str = "NSE", extra_closed=()) -> str | None:
    """CLOSED / MORNING_ONLY / EVENING_ONLY, or None for a normal full day.

    Weekends return CLOSED. `extra_closed` is an operator escape hatch for an
    unscheduled closure (a state funeral, an exchange outage) declared without
    a release - see EngineConfig.holidays. Raises UnknownYear past the
    published calendars so a stale deployment fails visibly in a review
    rather than silently trading a holiday a year later.
    """
    if day.year not in KNOWN_YEARS:
        raise UnknownYear(f"no {exchange.upper()} holiday calendar for {day.year} "
                          f"(known: {sorted(KNOWN_YEARS)}) - update trading_bot/holidays.py")
    if day.isoformat() in set(extra_closed):
        return CLOSED
    if day.weekday() >= 5:
        return CLOSED
    return _calendar(exchange).get(day.isoformat())


def is_trading_day(day: dt.date, exchange: str = "NSE", extra_closed=()) -> bool:
    """True if ANY session trades that day. A partial day is a trading day -
    MCX's evening session on Dussehra is a real session with real crude flow."""
    return status(day, exchange, extra_closed) != CLOSED


def closed_dates(exchange: str = "NSE", extra_closed=()) -> tuple[str, ...]:
    """Full-day closures as sorted ISO strings, for the flat `holidays` tuple
    that EngineConfig and clock.is_trading_day already take. Partial days are
    excluded by design: they ARE trading days, just shorter ones."""
    dates = {d for d, st in _calendar(exchange).items() if st == CLOSED}
    dates |= {s.strip() for s in extra_closed if s and s.strip()}
    return tuple(sorted(dates))
