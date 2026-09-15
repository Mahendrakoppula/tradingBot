import pytest

from execution.transaction_costs import TransactionCostRates, option_round_trip_cost


def test_cost_components_match_hand_computed_values():
    """Verified by hand against the formula's own documented structure -
    entry_premium=100, exit_premium=120, quantity=65 (NIFTY's real lot
    size)."""
    rates = TransactionCostRates(
        brokerage_per_order=20.0, stt_sell_pct=0.1, exchange_txn_pct=0.035,
        sebi_fee_pct=0.0001, stamp_duty_pct=0.003, gst_pct=18.0,
    )
    entry_turnover = 100 * 65  # 6500
    exit_turnover = 120 * 65  # 7800

    brokerage = 20.0 * 2  # 40
    stt = exit_turnover * 0.1 / 100  # 7.8
    exchange_txn = (entry_turnover + exit_turnover) * 0.035 / 100  # 5.005
    sebi_fee = (entry_turnover + exit_turnover) * 0.0001 / 100  # 0.0143
    stamp_duty = entry_turnover * 0.003 / 100  # 0.195
    gst = (brokerage + exchange_txn + sebi_fee) * 18.0 / 100
    expected = brokerage + stt + exchange_txn + sebi_fee + stamp_duty + gst

    result = option_round_trip_cost(100, 120, 65, rates)
    assert result == pytest.approx(expected)


def test_cost_is_always_positive_for_typical_inputs():
    cost = option_round_trip_cost(entry_premium=50, exit_premium=45, quantity=30)
    assert cost > 0


def test_cost_scales_with_quantity():
    small = option_round_trip_cost(entry_premium=100, exit_premium=110, quantity=20)
    large = option_round_trip_cost(entry_premium=100, exit_premium=110, quantity=200)
    assert large > small


def test_default_rates_are_used_when_not_specified():
    default_rates = TransactionCostRates()
    explicit = option_round_trip_cost(100, 110, 65, default_rates)
    implicit = option_round_trip_cost(100, 110, 65)
    assert explicit == implicit


def test_zero_quantity_gives_only_flat_brokerage():
    cost = option_round_trip_cost(entry_premium=100, exit_premium=110, quantity=0)
    rates = TransactionCostRates()
    expected_brokerage_plus_gst = rates.brokerage_per_order * 2 * (1 + rates.gst_pct / 100)
    assert cost == pytest.approx(expected_brokerage_plus_gst)
