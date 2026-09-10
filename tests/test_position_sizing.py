import pytest

from risk.position_sizing import capital_at_risk_for_trade, position_size_lots


def test_basic_sizing_rounds_down_to_whole_lots():
    # capital_at_risk=1000, risk per unit=10 points, lot_size=50 -> risk
    # per lot = 500 -> 2 whole lots (1000/500=2.0 exactly)
    lots = position_size_lots(capital_at_risk=1000, stop_loss_points=10, lot_size=50)
    assert lots == 2


def test_rounds_down_not_up_when_not_an_exact_multiple():
    # risk per lot = 500, budget = 1400 -> 2.8 lots -> rounds DOWN to 2,
    # never up past the risk budget
    lots = position_size_lots(capital_at_risk=1400, stop_loss_points=10, lot_size=50)
    assert lots == 2


def test_slippage_buffer_reduces_size():
    without_slippage = position_size_lots(capital_at_risk=1000, stop_loss_points=10, lot_size=50)
    with_slippage = position_size_lots(capital_at_risk=1000, stop_loss_points=10, lot_size=50, estimated_slippage_points=5)
    assert with_slippage <= without_slippage


def test_max_lots_caps_the_result():
    lots = position_size_lots(capital_at_risk=100_000, stop_loss_points=1, lot_size=1, max_lots=5)
    assert lots == 5


def test_zero_stop_distance_returns_zero_not_a_division_error():
    lots = position_size_lots(capital_at_risk=1000, stop_loss_points=0, lot_size=50)
    assert lots == 0


def test_zero_capital_at_risk_returns_zero():
    lots = position_size_lots(capital_at_risk=0, stop_loss_points=10, lot_size=50)
    assert lots == 0


def test_insufficient_capital_for_even_one_lot_returns_zero():
    lots = position_size_lots(capital_at_risk=100, stop_loss_points=10, lot_size=50)
    assert lots == 0


def test_capital_at_risk_scales_with_size_multiplier():
    full = capital_at_risk_for_trade(current_capital=50_000, base_risk_pct=0.01, size_multiplier=1.0)
    reduced = capital_at_risk_for_trade(current_capital=50_000, base_risk_pct=0.01, size_multiplier=0.5)
    assert full == 500.0
    assert reduced == 250.0


def test_capital_at_risk_rejects_out_of_range_multiplier():
    with pytest.raises(ValueError):
        capital_at_risk_for_trade(50_000, 0.01, size_multiplier=1.5)
