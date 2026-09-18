import datetime as dt

import pytest

from data.historical_expiry_calendar import (
    BANKNIFTY_WEEKLY_DISCONTINUED_DATE,
    resolve_historical_expiry,
)


# --- NIFTY: Thursday until 2025-08-31, Tuesday from 2025-09-01 ---

def test_nifty_thursday_regime_well_before_transition():
    # A Monday, safely within the old Thursday regime.
    assert resolve_historical_expiry("NIFTY", dt.date(2025, 8, 18)) == dt.date(2025, 8, 21)


def test_nifty_matches_the_verified_first_tuesday_expiry():
    """The single most load-bearing fact this module rests on, checked
    directly: real reporting confirms 2025-09-02 was the FIRST real
    Nifty Tuesday expiry. An entry the day before must resolve to it
    exactly."""
    assert resolve_historical_expiry("NIFTY", dt.date(2025, 9, 1)) == dt.date(2025, 9, 2)


def test_nifty_boundary_crossing_entry_before_transition_resolves_into_new_regime():
    """An entry on 2025-08-29 (Friday) is still technically "before" the
    2025-09-01 transition, but the naive OLD-regime answer (next
    Thursday = 2025-09-04) never actually existed - the real first
    expiry after that point was the new regime's Tuesday, 2025-09-02.
    The day-by-day resolver must self-correct across this boundary."""
    assert resolve_historical_expiry("NIFTY", dt.date(2025, 8, 29)) == dt.date(2025, 9, 2)


def test_nifty_tuesday_regime_well_after_transition():
    assert resolve_historical_expiry("NIFTY", dt.date(2025, 9, 3)) == dt.date(2025, 9, 9)


# --- SENSEX: Thursday -> Friday (2023-05-15) -> Tuesday (2025-01-01) -> Thursday (2025-09-01) ---

def test_sensex_thursday_regime_before_2023_relaunch():
    assert resolve_historical_expiry("SENSEX", dt.date(2022, 6, 1)) == dt.date(2022, 6, 2)  # next Thursday


def test_sensex_friday_regime_after_2023_relaunch():
    assert resolve_historical_expiry("SENSEX", dt.date(2023, 6, 1)) == dt.date(2023, 6, 2)  # next Friday


def test_sensex_tuesday_regime_in_2025_before_september():
    assert resolve_historical_expiry("SENSEX", dt.date(2025, 3, 3)) == dt.date(2025, 3, 4)  # next Tuesday


def test_sensex_thursday_regime_restart_after_september_2025():
    assert resolve_historical_expiry("SENSEX", dt.date(2025, 9, 8)) == dt.date(2025, 9, 11)  # next Thursday


def test_sensex_result_is_always_the_expected_weekday():
    """A broader sanity sweep across every SENSEX regime - every result
    must land on the regime's own configured weekday, not just at the
    hand-picked boundary dates above."""
    from data.historical_expiry_calendar import sensex_weekly_expiry_weekday
    for as_of in [dt.date(2021, 10, 1), dt.date(2023, 1, 1), dt.date(2024, 6, 1), dt.date(2025, 2, 1), dt.date(2026, 1, 1)]:
        result = resolve_historical_expiry("SENSEX", as_of)
        assert result.weekday() == sensex_weekly_expiry_weekday(result)
        assert result > as_of


# --- BANKNIFTY: weekly Wednesday until 2024-11-13, monthly only after ---

def test_banknifty_weekly_wednesday_well_before_discontinuation():
    assert resolve_historical_expiry("BANKNIFTY", dt.date(2024, 10, 1)) == dt.date(2024, 10, 2)  # next Wednesday


def test_banknifty_matches_verified_last_weekly_expiry():
    """Real reporting confirms 2024-11-13 was BANKNIFTY's actual LAST
    weekly expiry. An entry a week before must resolve exactly to it."""
    assert resolve_historical_expiry("BANKNIFTY", dt.date(2024, 11, 6)) == dt.date(2024, 11, 13)


def test_banknifty_entry_on_last_weekly_expiry_day_falls_through_to_monthly():
    """The edge case this module's own resolver had to be fixed for:
    an entry ON the real last weekly expiry day (2024-11-13) must NOT
    produce a fake, never-existed following Wednesday (2024-11-20) -
    weeklies were gone by then, so this must fall through to the next
    real MONTHLY expiry instead."""
    result = resolve_historical_expiry("BANKNIFTY", BANKNIFTY_WEEKLY_DISCONTINUED_DATE - dt.timedelta(days=1))
    assert result != dt.date(2024, 11, 20)
    assert result == dt.date(2024, 11, 28)  # last Thursday of November 2024


def test_banknifty_monthly_only_after_discontinuation():
    result = resolve_historical_expiry("BANKNIFTY", dt.date(2024, 12, 1))
    assert result == dt.date(2024, 12, 26)  # last Thursday of December 2024


def test_banknifty_monthly_thursday_to_tuesday_transition():
    # Entry in August 2025, before the 2025-09-01 monthly weekday swap -
    # the real last-Thursday-of-August expiry (2025-08-28) still applies.
    assert resolve_historical_expiry("BANKNIFTY", dt.date(2025, 8, 1)) == dt.date(2025, 8, 28)
    # Entry after the swap - now resolves against the new last-Tuesday convention.
    result = resolve_historical_expiry("BANKNIFTY", dt.date(2025, 9, 5))
    assert result.weekday() == 1  # Tuesday
    assert result == dt.date(2025, 9, 30)  # last Tuesday of September 2025


def test_banknifty_boundary_entry_just_before_monthly_weekday_swap():
    """An entry on 2025-08-29 (after the real last-Thursday-of-August
    expiry already happened) must correctly roll to the NEXT month's
    expiry under the NEW Tuesday convention, not a stale Thursday one."""
    result = resolve_historical_expiry("BANKNIFTY", dt.date(2025, 8, 29))
    assert result.weekday() == 1  # Tuesday
    assert result == dt.date(2025, 9, 30)


# --- General correctness ---

def test_unknown_underlying_raises():
    with pytest.raises(ValueError):
        resolve_historical_expiry("GOLD", dt.date(2024, 1, 1))


def test_result_always_strictly_after_entry_date_across_a_broad_sweep():
    import random
    rng = random.Random(0)
    start = dt.date(2021, 9, 20)
    for _ in range(200):
        entry_date = start + dt.timedelta(days=rng.randint(0, 1800))
        for underlying in ["NIFTY", "BANKNIFTY", "SENSEX"]:
            result = resolve_historical_expiry(underlying, entry_date)
            assert result > entry_date
