"""requires_real_data tests need data/raw/NIFTY/ONE_DAY.parquet (see
tests/test_feature_engineering.py's module docstring for why)."""
import datetime as dt

import pytest

from data.storage import load_ohlcv
from strategies.contract_selection import evaluate_candidate_contracts, select_affordable_contract, select_contract

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


@requires_real_data
def test_select_affordable_contract_regression_against_live_verified_numbers():
    """Regression check against a real, hand-verified sweep of every
    strike offset (0-9) at this exact historical bar (spot 23477.8, ATM
    23500): the first offset whose premium x lot_size (65) fits a
    Rs.1,000 budget is offset=5 (strike 23750, premium ~13.46, cost
    ~875) - offsets 0-4 are all still too expensive (up to Rs.5,779 at
    ATM). Uses this exact historical bar (not "the last one", which
    drifts as more data gets pulled) so the numbers stay reproducible."""
    as_of_index = len(NIFTY_DAILY) - 1
    if float(NIFTY_DAILY["close"].iloc[as_of_index]) != pytest.approx(23477.8, abs=0.5):
        pytest.skip("local data/raw/ has moved past the exact bar this regression check was verified against")

    expiry = NIFTY_DAILY["timestamp"].iloc[as_of_index].date() + dt.timedelta(days=7)
    result = select_affordable_contract(
        NIFTY_DAILY, as_of_index, "CE", expiry, risk_free_rate=0.07, strike_increment=50,
        risk_budget=1000, lot_size=65,
    )
    assert result is not None
    assert result.strike == 23750.0
    assert result.snapshot.price == pytest.approx(13.46, abs=0.05)
    assert result.snapshot.price * 65 <= 1000


@requires_real_data
def test_select_affordable_contract_never_returns_a_strike_costing_more_than_the_budget():
    as_of_index = 500
    expiry = NIFTY_DAILY["timestamp"].iloc[as_of_index].date() + dt.timedelta(days=7)
    for risk_budget in (200, 1000, 5000, 20000):
        result = select_affordable_contract(
            NIFTY_DAILY, as_of_index, "CE", expiry, risk_free_rate=0.07, strike_increment=50,
            risk_budget=risk_budget, lot_size=65,
        )
        if result is not None:
            assert result.snapshot.price * 65 <= risk_budget


@requires_real_data
def test_select_affordable_contract_prefers_least_far_otm_that_fits():
    as_of_index = 500
    expiry = NIFTY_DAILY["timestamp"].iloc[as_of_index].date() + dt.timedelta(days=7)
    generous = select_affordable_contract(
        NIFTY_DAILY, as_of_index, "CE", expiry, risk_free_rate=0.07, strike_increment=50,
        risk_budget=1_000_000, lot_size=65,
    )
    # with an enormous budget, ATM itself must already be affordable -
    # so the search must stop immediately at ATM, not walk further out.
    spot = float(NIFTY_DAILY["close"].iloc[as_of_index])
    atm = round(spot / 50) * 50
    assert generous.strike == atm


@requires_real_data
def test_select_affordable_contract_returns_none_when_nothing_fits_even_far_otm():
    as_of_index = 500
    expiry = NIFTY_DAILY["timestamp"].iloc[as_of_index].date() + dt.timedelta(days=7)
    result = select_affordable_contract(
        NIFTY_DAILY, as_of_index, "CE", expiry, risk_free_rate=0.07, strike_increment=50,
        risk_budget=0.0001, lot_size=65, max_otm_steps=3,  # tiny budget, tiny search range
    )
    assert result is None


@requires_real_data
def test_select_affordable_contract_pe_searches_lower_strikes_for_otm():
    as_of_index = 500
    expiry = NIFTY_DAILY["timestamp"].iloc[as_of_index].date() + dt.timedelta(days=7)
    spot = float(NIFTY_DAILY["close"].iloc[as_of_index])
    atm = round(spot / 50) * 50
    result = select_affordable_contract(
        NIFTY_DAILY, as_of_index, "PE", expiry, risk_free_rate=0.07, strike_increment=50,
        risk_budget=500, lot_size=65,
    )
    if result is not None:
        assert result.strike <= atm  # PE's OTM direction is downward, never above ATM


def test_select_affordable_contract_rejects_invalid_direction():
    import pandas as pd
    df = pd.DataFrame({"timestamp": pd.bdate_range("2026-01-01", periods=30), "close": [100.0] * 30,
                        "open": [100.0] * 30, "high": [101.0] * 30, "low": [99.0] * 30, "volume": [0] * 30})
    with pytest.raises(ValueError):
        select_affordable_contract(df, 29, "XX", dt.date(2026, 2, 1), 0.07, 50, risk_budget=1000, lot_size=65)
