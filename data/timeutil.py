"""IST clock helper - deliberately duplicated from trading_bot/timeutil.py
rather than imported (codex is a standalone stack from `main`, see
README.md), same reasoning preserved: a fixed UTC+5:30 offset, not
zoneinfo, so this doesn't depend on the host having tzdata installed, and
this instance's system clock is UTC (confirmed on `main` - a naive
datetime.now() there silently misjudged "today" by hours).
"""
import datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def now_ist() -> dt.datetime:
    return dt.datetime.now(IST)


def today_ist() -> dt.date:
    return now_ist().date()
