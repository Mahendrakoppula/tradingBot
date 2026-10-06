"""The holiday calendar, and the two failures it exists to prevent.

Failure 1 (cost Rs.3,958.50): an empty calendar let the daily bot enter on
2026-10-02 (Gandhi Jayanti) against the previous session's stale quote.
Failure 2 (would cost data): treating MCX holidays as full-day closures would
discard eleven real crude evening sessions.
"""
import datetime as dt

import pytest

from trading_bot import holidays
from trading_bot.engine.clock import MCX_SESSION, NSE_SESSION, configure_session
from trading_bot.holidays import (CLOSED, EVENING_ONLY, MORNING_ONLY, UnknownYear,
                                  closed_dates, is_trading_day, status)

GANDHI_JAYANTI = dt.date(2026, 10, 2)  # Friday - a weekday, which is the whole problem
DUSSEHRA = dt.date(2026, 10, 20)       # Tuesday - NSE shut, MCX evening open
NORMAL = dt.date(2026, 10, 6)          # Tuesday, ordinary session


def test_the_regression_gandhi_jayanti_is_not_a_trading_day():
    """The exact date and the exact question the daily bot got wrong."""
    assert GANDHI_JAYANTI.weekday() < 5  # a weekday, so the old guard said "trade"
    assert status(GANDHI_JAYANTI, "NSE") == CLOSED
    assert is_trading_day(GANDHI_JAYANTI, "NSE") is False
    assert is_trading_day(GANDHI_JAYANTI, "MCX") is False  # one of MCX's four full closures


def test_ordinary_weekday_still_trades():
    assert status(NORMAL, "NSE") is None
    assert is_trading_day(NORMAL, "NSE") is True
    assert is_trading_day(NORMAL, "MCX") is True


@pytest.mark.parametrize("day", [dt.date(2026, 10, 3), dt.date(2026, 10, 4)])
def test_weekends_are_closed(day):
    assert day.weekday() >= 5
    assert status(day, "NSE") == CLOSED
    assert is_trading_day(day, "NSE") is False


def test_mcx_evening_only_day_is_still_a_trading_day():
    """The failure in the other direction: MCX trades Dussehra evening. A flat
    'holiday' set would have thrown the whole session away."""
    assert status(DUSSEHRA, "NSE") == CLOSED       # index engine stays home
    assert status(DUSSEHRA, "MCX") == EVENING_ONLY  # crude engine does not
    assert is_trading_day(DUSSEHRA, "MCX") is True
    assert DUSSEHRA.isoformat() not in closed_dates("MCX")


def test_mcx_closes_the_full_day_only_four_times_in_2026():
    """If this number moves, someone edited the calendar - make them prove the
    new date against the exchange circular."""
    assert len(closed_dates("MCX")) == 4
    assert closed_dates("MCX") == ("2026-01-26", "2026-04-03", "2026-10-02", "2026-12-25")


def test_nse_calendar_is_all_full_day_closures():
    assert len(closed_dates("NSE")) == 16
    assert all(st == CLOSED for st in holidays.NSE_2026.values())


def test_bse_follows_nse():
    """SENSEX options trade in the same process as NIFTY's."""
    assert closed_dates("BSE") == closed_dates("NSE")


def test_new_years_day_is_morning_only_on_mcx():
    assert status(dt.date(2026, 1, 1), "MCX") == MORNING_ONLY
    assert is_trading_day(dt.date(2026, 1, 1), "MCX") is True
    assert is_trading_day(dt.date(2026, 1, 1), "NSE") is True  # not an NSE holiday at all


def test_extra_closed_is_additive_and_cannot_disarm_the_calendar():
    """An operator declaring an unscheduled closure must not lose the published
    dates, and an empty list must leave the calendar fully armed."""
    extra = closed_dates("NSE", ("2026-07-01",))
    assert "2026-07-01" in extra
    assert "2026-10-02" in extra and len(extra) == 17
    assert closed_dates("NSE", ()) == closed_dates("NSE")
    assert is_trading_day(dt.date(2026, 7, 1), "NSE", ("2026-07-01",)) is False


def test_blank_entries_in_extra_closed_are_ignored():
    assert closed_dates("NSE", ("", "  ")) == closed_dates("NSE")


def test_unknown_year_raises_rather_than_reporting_a_normal_day():
    """A stale deployment must fail visibly, not quietly trade a 2027 holiday."""
    with pytest.raises(UnknownYear):
        status(dt.date(2027, 1, 4), "NSE")
    with pytest.raises(UnknownYear):
        is_trading_day(dt.date(2025, 1, 2), "NSE")


def test_unknown_exchange_raises():
    with pytest.raises(ValueError):
        status(NORMAL, "NYSE")


def test_exchange_name_is_case_insensitive():
    assert status(GANDHI_JAYANTI, "nse") == CLOSED


class TestSessionNarrowing:
    """configure_session(open_=) is what turns EVENING_ONLY into a real session
    window, so the engine does not treat a morning that never opened as a gap."""

    def teardown_method(self):
        configure_session("NSE")  # the profile is process-global

    def test_with_open_drops_and_clips_phases(self):
        prof = MCX_SESSION.with_open(holidays.MCX_EVENING_OPEN)
        assert prof.open == dt.time(17, 0)
        assert prof.close == MCX_SESSION.close
        assert all(end > dt.time(17, 0) for _, end, _ in prof.phases)
        assert prof.phases[0][0] == dt.time(17, 0)

    def test_with_open_rejects_an_open_at_or_after_close(self):
        with pytest.raises(ValueError):
            MCX_SESSION.with_open(dt.time(23, 30))

    def test_configure_session_applies_open_after_close(self):
        prof = configure_session("MCX", dt.time(23, 55), open_=holidays.MCX_EVENING_OPEN)
        assert prof.open == dt.time(17, 0) and prof.close == dt.time(23, 55)
        assert prof.phases[-1][1] == dt.time(23, 55)

    def test_morning_only_narrows_the_close(self):
        prof = MCX_SESSION.with_close(holidays.MCX_EVENING_OPEN)
        assert prof.open == MCX_SESSION.open and prof.close == dt.time(17, 0)

    def test_nse_profile_is_untouched_by_all_this(self):
        prof = configure_session("NSE")
        assert (prof.open, prof.close) == (dt.time(9, 15), dt.time(15, 30))
        assert prof.phases == NSE_SESSION.phases


class TestDailyBotGuard:
    """The daily bot's own wrapper. It must never raise - an un-updated
    calendar should block entries and shout, not crash-loop the runner."""

    def test_holiday_blocks_entry(self):
        from trading_bot.run_daily import _trading_today
        assert _trading_today(GANDHI_JAYANTI) == (False, None)

    def test_normal_day_allows_entry(self):
        from trading_bot.run_daily import _trading_today
        assert _trading_today(NORMAL) == (True, None)

    def test_unknown_year_blocks_entry_and_explains_why(self):
        from trading_bot.run_daily import _trading_today
        ok, warning = _trading_today(dt.date(2027, 1, 4))
        assert ok is False
        assert warning and "2027" in warning and "holidays.py" in warning

    def test_every_entry_path_is_gated_and_no_exit_path_is(self):
        """Pins the asymmetry deliberately. BOTH entry paths must be gated -
        the daily strategy and the scalp add-on, which has its own
        `cfg.enable_trading` branch and was missed on the first pass. No exit
        path may be gated: a position carried into a holiday must still be
        priced, stopped and closed at exit_time."""
        import inspect

        from trading_bot import run_daily
        src = inspect.getsource(run_daily.main)
        entries = [l for l in src.splitlines()
                   if "if cfg.enable_trading" in l and "exit_time" in l]
        assert len(entries) == 2, entries  # daily + scalp
        assert all("trading_today" in l for l in entries), entries
        exits = [l for l in src.splitlines() if "now_t >= exit_time" in l]
        assert len(exits) == 2, exits  # daily + scalp
        assert not any("trading_today" in l for l in exits), exits
