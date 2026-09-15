import datetime as dt

import pytest

from data.expiry_calendar import list_option_expiries, resolve_nearest_expiry

# Real rows' shape, extracted from a live scrip-master query
# (2026-09-15) - see data/expiry_calendar.py's module docstring for the
# verified findings this fixture reflects.
SAMPLE_INSTRUMENTS = [
    {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "15SEP2026", "token": "1"},
    {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "22SEP2026", "token": "2"},
    {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "06OCT2026", "token": "3"},
    {"name": "BANKNIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "29SEP2026", "token": "4"},
    {"name": "BANKNIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "27OCT2026", "token": "5"},
    {"name": "BANKNIFTY", "exch_seg": "NFO", "instrumenttype": "OPTIDX", "expiry": "23NOV2026", "token": "6"},
    {"name": "SENSEX", "exch_seg": "BFO", "instrumenttype": "OPTIDX", "expiry": "17SEP2026", "token": "7"},
    {"name": "SENSEX", "exch_seg": "BFO", "instrumenttype": "OPTIDX", "expiry": "24SEP2026", "token": "8"},
    # Noise that must never leak into the results:
    {"name": "NIFTY", "exch_seg": "NFO", "instrumenttype": "FUTIDX", "expiry": "24SEP2026", "token": "9"},  # future, not option
    {"name": "RELIANCE", "exch_seg": "NFO", "instrumenttype": "OPTSTK", "expiry": "24SEP2026", "token": "10"},  # stock option
    {"name": "SENSEX", "exch_seg": "BFO", "instrumenttype": "OPTIDX", "expiry": "not-a-date", "token": "11"},  # malformed
]


def test_list_option_expiries_returns_sorted_distinct_dates():
    result = list_option_expiries(SAMPLE_INSTRUMENTS, "NIFTY")
    assert result == [dt.date(2026, 9, 15), dt.date(2026, 9, 22), dt.date(2026, 10, 6)]


def test_list_option_expiries_is_case_insensitive():
    assert list_option_expiries(SAMPLE_INSTRUMENTS, "nifty") == list_option_expiries(SAMPLE_INSTRUMENTS, "NIFTY")


def test_list_option_expiries_excludes_futures_and_stock_options():
    result = list_option_expiries(SAMPLE_INSTRUMENTS, "NIFTY")
    assert dt.date(2026, 9, 24) not in result  # that date only appears on the FUTIDX row


def test_list_option_expiries_skips_malformed_expiry_strings():
    result = list_option_expiries(SAMPLE_INSTRUMENTS, "SENSEX")
    assert result == [dt.date(2026, 9, 17), dt.date(2026, 9, 24)]


def test_list_option_expiries_raises_for_unknown_underlying():
    with pytest.raises(ValueError):
        list_option_expiries(SAMPLE_INSTRUMENTS, "NOTANINDEX")


def test_resolve_nearest_expiry_returns_the_soonest_qualifying_date():
    result = resolve_nearest_expiry(SAMPLE_INSTRUMENTS, "NIFTY", as_of=dt.date(2026, 9, 16))
    assert result == dt.date(2026, 9, 22)  # 15th has already passed relative to as_of


def test_resolve_nearest_expiry_includes_as_of_date_itself():
    result = resolve_nearest_expiry(SAMPLE_INSTRUMENTS, "NIFTY", as_of=dt.date(2026, 9, 15))
    assert result == dt.date(2026, 9, 15)


def test_resolve_nearest_expiry_respects_min_days_to_expiry():
    result = resolve_nearest_expiry(SAMPLE_INSTRUMENTS, "NIFTY", as_of=dt.date(2026, 9, 15), min_days_to_expiry=3)
    assert result == dt.date(2026, 9, 22)  # 15th and even 22nd's 7-day gap matters - 15+3=18, first expiry >= 18th is 22nd


def test_resolve_nearest_expiry_raises_when_nothing_qualifies():
    with pytest.raises(LookupError):
        resolve_nearest_expiry(SAMPLE_INSTRUMENTS, "NIFTY", as_of=dt.date(2030, 1, 1))


def test_banknifty_monthly_expiries_land_on_monday_or_tuesday():
    """Regression check for the real, live-verified finding this module's
    docstring documents: BANKNIFTY is monthly-only, and at least one real
    expiry lands on a Monday (23NOV2026) rather than the more common
    Tuesday - confirmed live, not assumed."""
    result = list_option_expiries(SAMPLE_INSTRUMENTS, "BANKNIFTY")
    weekdays = {d.strftime("%A") for d in result}
    assert weekdays <= {"Monday", "Tuesday"}
    assert dt.date(2026, 11, 23) in result
    assert dt.date(2026, 11, 23).strftime("%A") == "Monday"
