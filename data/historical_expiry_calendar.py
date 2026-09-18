"""Real, historically-verified expiry-day calendar for NIFTY/BANKNIFTY/
SENSEX index options across this project's 2021-2026 backtest window -
closes event_loop.py's own long-disclosed "fixed days_to_expiry, no
real weekly-expiry trading calendar" simplification (flagged since Run
001, deliberately deferred multiple times because getting a historical
regime-change date wrong would corrupt every trade's option pricing
with an unverified assumption - exactly the failure mode this
project's "verify, don't assume" discipline exists to prevent).

Every date below comes from real NSE/BSE regulatory action and
reputable financial reporting, cross-checked across multiple
independent sources (web research performed for this module, not
recalled from memory) - not assumed. Unlike data/expiry_calendar.py
(which resolves expiries for CURRENTLY LISTED contracts only, live
data with no historical archive), this module is built specifically to
cover PAST dates across the whole backtest window.

VERIFIED HISTORY:
  NIFTY weekly: THURSDAY from well before this project's backtest
    window until 2025-08-31; TUESDAY from 2025-09-01 (SEBI-mandated
    NSE/BSE expiry-day swap, ending a 25-year-old Thursday convention).
  BANKNIFTY weekly: WEDNESDAY, but DISCONTINUED entirely after its
    last weekly expiry 2024-11-13 (SEBI's one-weekly-index-per-exchange
    rule - NSE kept only NIFTY's weekly). From 2024-11-14 onward
    BANKNIFTY has NO weekly option at all, only monthly.
  BANKNIFTY monthly: LAST THURSDAY of the month until 2025-08-28 (the
    same 25-year NSE convention); LAST TUESDAY from 2025-09-01 (same
    SEBI-mandated swap, applied to monthly/quarterly contracts too).
  SENSEX weekly: THURSDAY until 2023-05-14; FRIDAY from BSE's
    2023-05-15 Sensex/Bankex relaunch (BSE deliberately chose Friday to
    differentiate from NSE's own Thursday at the time); TUESDAY from
    2025-01-01 (SEBI's one-weekly-index-per-exchange rule consolidated
    BSE onto Sensex only); THURSDAY from 2025-09-01 (the same SEBI
    swap as NIFTY, in the opposite direction).

HONEST RESIDUAL LIMITATION, not fixed here: a real listed expiry shifts
by a day when the calculated weekday lands on an exchange holiday
(confirmed elsewhere in this project - data/expiry_calendar.py's own
live-verified BANKNIFTY example, 2026-11-23 landing on a Monday, not
the "expected" Tuesday, due to a holiday). This module has no
historical NSE/BSE trading-holiday calendar to correct for that, unlike
data/expiry_calendar.py's live version (which gets holiday-awareness
for free from the real listed scrip master). Expect being off by up to
~1 trading day around holiday weeks, not a systematic multi-day error -
a materially smaller, disclosed approximation than the fixed-7-day
convention it replaces, not a claim of perfection.
"""
import datetime as dt

MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY = range(5)

NIFTY_TUESDAY_START = dt.date(2025, 9, 1)
SENSEX_FRIDAY_START = dt.date(2023, 5, 15)
SENSEX_TUESDAY_START = dt.date(2025, 1, 1)
SENSEX_THURSDAY_RESTART = dt.date(2025, 9, 1)
BANKNIFTY_WEEKLY_DISCONTINUED_DATE = dt.date(2024, 11, 14)  # first date with NO weekly option - last weekly expired 2024-11-13
BANKNIFTY_MONTHLY_TUESDAY_START = dt.date(2025, 9, 1)


def nifty_weekly_expiry_weekday(as_of: dt.date) -> int:
    return THURSDAY if as_of < NIFTY_TUESDAY_START else TUESDAY


def sensex_weekly_expiry_weekday(as_of: dt.date) -> int:
    if as_of < SENSEX_FRIDAY_START:
        return THURSDAY
    if as_of < SENSEX_TUESDAY_START:
        return FRIDAY
    if as_of < SENSEX_THURSDAY_RESTART:
        return TUESDAY
    return THURSDAY


def banknifty_monthly_expiry_weekday(as_of: dt.date) -> int:
    return THURSDAY if as_of < BANKNIFTY_MONTHLY_TUESDAY_START else TUESDAY


def _last_weekday_of_month(year: int, month: int, weekday: int) -> dt.date:
    next_month_first = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
    last_day = next_month_first - dt.timedelta(days=1)
    days_back = (last_day.weekday() - weekday) % 7
    return last_day - dt.timedelta(days=days_back)


def _next_weekly_expiry_after(entry_date: dt.date, weekday_fn) -> dt.date:
    """Iterates day by day rather than jumping straight to "the next
    Nth weekday" - deliberately, so a regime change landing WITHIN the
    search window (an entry in the last few days before a transition
    date) is handled correctly by checking each candidate day against
    the regime THAT DAY is actually under, not the regime on
    entry_date. A weekly cycle is at most 7 days out; 14 is a safe,
    generous upper bound that still terminates quickly even across a
    boundary."""
    candidate = entry_date + dt.timedelta(days=1)
    for _ in range(14):
        if candidate.weekday() == weekday_fn(candidate):
            return candidate
        candidate += dt.timedelta(days=1)
    raise RuntimeError(f"no weekly expiry found within 14 days of {entry_date} - should never happen for a 7-day cycle")


def _next_banknifty_monthly_expiry_after(entry_date: dt.date) -> dt.date:
    """Same regime-boundary-safety principle as the weekly resolver,
    adapted for a monthly cycle: checks each candidate month's OWN
    last-day regime (not entry_date's), so the single known monthly
    weekday transition (2025-09-01) is handled correctly for entries in
    the last days of August 2025."""
    year, month = entry_date.year, entry_date.month
    for _ in range(3):  # a month or two of lookahead is always enough
        next_month_first = dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)
        last_day_of_month = next_month_first - dt.timedelta(days=1)
        weekday = banknifty_monthly_expiry_weekday(last_day_of_month)
        candidate = _last_weekday_of_month(year, month, weekday)
        if candidate > entry_date:
            return candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1
    raise RuntimeError(f"no BANKNIFTY monthly expiry found within 3 months of {entry_date} - should never happen")


def resolve_historical_expiry(underlying: str, entry_date: dt.date) -> dt.date:
    """Returns the real, historically-correct expiry date for a trade
    ENTERED on `entry_date` - the nearest real listed expiry strictly
    AFTER entry_date (an option entered on its own expiry day is not a
    meaningful new trade), matching this project's existing
    contract-selection conventions elsewhere. Replaces
    event_loop.py's fixed `entry_date + timedelta(days=7)` with the
    real weekly/monthly cadence and regime history documented in this
    module's own docstring."""
    underlying = underlying.upper()
    if underlying == "NIFTY":
        return _next_weekly_expiry_after(entry_date, nifty_weekly_expiry_weekday)
    if underlying == "SENSEX":
        return _next_weekly_expiry_after(entry_date, sensex_weekly_expiry_weekday)
    if underlying == "BANKNIFTY":
        # Checking the RESULTING candidate against the cutoff too, not
        # just entry_date, matters for the same reason the weekly/
        # monthly weekday resolvers check each candidate's own regime:
        # an entry on (or just before) the real last weekly expiry
        # (2024-11-13) would otherwise compute a fake, never-existed
        # weekly expiry (e.g. 2024-11-20) that spills past the real
        # discontinuation date.
        if entry_date < BANKNIFTY_WEEKLY_DISCONTINUED_DATE:
            weekly_candidate = _next_weekly_expiry_after(entry_date, lambda _d: WEDNESDAY)
            if weekly_candidate < BANKNIFTY_WEEKLY_DISCONTINUED_DATE:
                return weekly_candidate
        return _next_banknifty_monthly_expiry_after(entry_date)
    raise ValueError(f"No known historical expiry convention for {underlying!r} - expected NIFTY, BANKNIFTY, or SENSEX")
