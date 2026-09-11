"""requires_real_data tests need data/raw/NIFTY/ONE_DAY.parquet (see
tests/test_feature_engineering.py's module docstring for why)."""
import datetime as dt

import pytest

from data.storage import load_ohlcv
from strategies.contract_selection import evaluate_candidate_contracts, select_contract

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")


@requires_real_data
def test_evaluate_candidates_returns_one_per_offset_with_correct_strikes():
    as_of_index = 500
    spot = float(NIFTY_DAILY["close"].iloc[as_of_index])
    atm = round(spot / 50) * 50
    expiry = NIFTY_DAILY["timestamp"].iloc[as_of_index].date() + dt.timedelta(days=7)

    candidates = evaluate_candidate_contracts(NIFTY_DAILY, as_of_index, "CE", expiry, 0.07, strike_increment=50)
    assert len(candidates) == 5
    expected_strikes = sorted(atm + offset * 50 for offset in (-2, -1, 0, 1, 2))
    assert sorted(c.strike for c in candidates) == expected_strikes


@requires_real_data
def test_evaluate_candidates_empty_when_not_enough_history():
    candidates = evaluate_candidate_contracts(
        NIFTY_DAILY, as_of_index=5, direction="CE", expiry=dt.date(2026, 2, 1),
        risk_free_rate=0.07, strike_increment=50,
    )
    assert candidates == []


@requires_real_data
def test_select_contract_picks_the_max_capital_efficiency_candidate():
    as_of_index = 500
    expiry = NIFTY_DAILY["timestamp"].iloc[as_of_index].date() + dt.timedelta(days=7)
    candidates = evaluate_candidate_contracts(NIFTY_DAILY, as_of_index, "CE", expiry, 0.07, strike_increment=50)
    best = select_contract(NIFTY_DAILY, as_of_index, "CE", expiry, 0.07, strike_increment=50)
    assert best is not None
    assert best.capital_efficiency == max(c.capital_efficiency for c in candidates)


@requires_real_data
def test_select_contract_returns_none_when_no_candidates():
    best = select_contract(
        NIFTY_DAILY, as_of_index=5, direction="CE", expiry=dt.date(2026, 2, 1),
        risk_free_rate=0.07, strike_increment=50,
    )
    assert best is None


@requires_real_data
def test_every_candidate_has_a_priced_snapshot_and_nonnegative_efficiency():
    as_of_index = 700
    expiry = NIFTY_DAILY["timestamp"].iloc[as_of_index].date() + dt.timedelta(days=14)
    candidates = evaluate_candidate_contracts(NIFTY_DAILY, as_of_index, "PE", expiry, 0.07, strike_increment=50)
    assert len(candidates) == 5
    for c in candidates:
        assert c.snapshot.price >= 0
        assert c.capital_efficiency >= 0


@requires_real_data
def test_at_expiry_zero_premium_candidate_has_zero_efficiency_not_a_crash():
    as_of_index = 500
    as_of_date = NIFTY_DAILY["timestamp"].iloc[as_of_index].date()
    # expiry on the as-of date itself -> pure intrinsic value, some
    # OTM candidates will have exactly zero premium
    candidates = evaluate_candidate_contracts(NIFTY_DAILY, as_of_index, "CE", as_of_date, 0.07, strike_increment=50)
    assert len(candidates) == 5
    for c in candidates:
        assert c.capital_efficiency >= 0
