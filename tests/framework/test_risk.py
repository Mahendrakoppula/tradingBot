import pytest

from research.framework.risk import (
    PortfolioRiskState,
    atr_stop_target,
    chandelier_stop,
    correlated_exposure_multiplier,
    evaluate_portfolio_risk,
    r_multiple_price,
    risk_based_quantity,
    should_activate_chandelier,
    structure_stop_target,
)
from trading_bot.support_resistance import SwingPoint


# --- stop/target ---

def test_atr_stop_target_long():
    st = atr_stop_target(100.0, "up", atr_value=2.0, stop_atr_mult=1.5, reward_risk_ratio=2.0)
    assert st.stop_price == pytest.approx(97.0)
    assert st.target_price == pytest.approx(106.0)


def test_atr_stop_target_short():
    st = atr_stop_target(100.0, "down", atr_value=2.0, stop_atr_mult=1.5, reward_risk_ratio=2.0)
    assert st.stop_price == pytest.approx(103.0)
    assert st.target_price == pytest.approx(94.0)


def test_atr_stop_target_invalid_direction():
    with pytest.raises(ValueError):
        atr_stop_target(100.0, "sideways", atr_value=2.0)


def test_structure_stop_target_uses_recent_swing_low_for_long():
    swings = [
        SwingPoint(index=1, price=110.0, kind="high"),
        SwingPoint(index=3, price=95.0, kind="low"),
    ]
    st = structure_stop_target(100.0, "up", swings, atr_value=2.0, reward_risk_ratio=2.0)
    assert st.stop_price == pytest.approx(95.0)
    assert st.target_price == pytest.approx(100.0 + 5.0 * 2.0)


def test_structure_stop_target_falls_back_when_no_swing():
    st = structure_stop_target(100.0, "up", [], atr_value=2.0)
    fallback = atr_stop_target(100.0, "up", atr_value=2.0)
    assert st.stop_price == pytest.approx(fallback.stop_price)


def test_structure_stop_target_falls_back_when_too_close():
    swings = [SwingPoint(index=1, price=99.8, kind="low")]  # 0.2 away, atr=2.0 -> below min_stop_atr_mult*atr
    st = structure_stop_target(100.0, "up", swings, atr_value=2.0, min_stop_atr_mult=0.5)
    fallback = atr_stop_target(100.0, "up", atr_value=2.0)
    assert st.stop_price == pytest.approx(fallback.stop_price)


def test_r_multiple_price_long_and_short():
    assert r_multiple_price(100.0, 95.0, "up", 2.0) == pytest.approx(110.0)
    assert r_multiple_price(100.0, 105.0, "down", 2.0) == pytest.approx(90.0)


def test_chandelier_stop_trails_from_extreme_not_current_price():
    assert chandelier_stop("up", highest_since_entry=120.0, lowest_since_entry=95.0, atr_value=2.0, atr_mult=3.0) == pytest.approx(114.0)
    assert chandelier_stop("down", highest_since_entry=105.0, lowest_since_entry=90.0, atr_value=2.0, atr_mult=3.0) == pytest.approx(96.0)


def test_should_activate_chandelier_requires_minimum_favorable_move():
    assert should_activate_chandelier("up", entry_price=100.0, current_price=100.5, stop_price=95.0) is False
    assert should_activate_chandelier("up", entry_price=100.0, current_price=106.0, stop_price=95.0) is True


# --- position sizing ---

def test_risk_based_quantity_basic():
    # Risk 1% of 100000 = 1000; stop distance 10 -> raw 100 units -> lot 25 -> 4 lots -> 100 units
    qty = risk_based_quantity(capital=100000.0, risk_pct_per_trade=1.0, entry_price=110.0, stop_price=100.0, lot_size=25)
    assert qty == 100


def test_risk_based_quantity_rounds_down_never_up():
    qty = risk_based_quantity(capital=1000.0, risk_pct_per_trade=1.0, entry_price=110.0, stop_price=100.0, lot_size=25)
    assert qty == 0  # budget of 10, needs 250 for one lot -> can't afford even 1 lot


def test_risk_based_quantity_zero_stop_distance_is_zero_qty():
    assert risk_based_quantity(100000.0, 1.0, 100.0, 100.0) == 0


# --- portfolio risk gate ---

def test_evaluate_portfolio_risk_normal_state_allows_full_size():
    state = PortfolioRiskState(starting_capital=100000.0, current_capital=100000.0, peak_capital=100000.0)
    decision = evaluate_portfolio_risk(state)
    assert decision.allowed is True
    assert decision.risk_multiplier == 1.0


def test_evaluate_portfolio_risk_daily_loss_limit_blocks_trading():
    state = PortfolioRiskState(starting_capital=100000.0, current_capital=97000.0, peak_capital=100000.0, daily_realized_pnl=-3000.0)
    decision = evaluate_portfolio_risk(state, daily_loss_limit_pct=3.0)
    assert decision.allowed is False
    assert decision.reason == "daily_loss_limit_breached"


def test_evaluate_portfolio_risk_drawdown_reduces_then_blocks():
    reduced = PortfolioRiskState(starting_capital=100000.0, current_capital=88000.0, peak_capital=100000.0)
    decision = evaluate_portfolio_risk(reduced)
    assert decision.allowed is True
    assert decision.risk_multiplier == 0.5

    blocked = PortfolioRiskState(starting_capital=100000.0, current_capital=79000.0, peak_capital=100000.0)
    decision2 = evaluate_portfolio_risk(blocked)
    assert decision2.allowed is False
    assert decision2.risk_multiplier == 0.0


def test_evaluate_portfolio_risk_consecutive_losses_reduce_size():
    state = PortfolioRiskState(starting_capital=100000.0, current_capital=100000.0, peak_capital=100000.0, consecutive_losses=3)
    decision = evaluate_portfolio_risk(state, max_consecutive_losses=3, consecutive_loss_risk_multiplier=0.5)
    assert decision.allowed is True
    assert decision.risk_multiplier == 0.5


# --- correlation cap ---

def test_correlated_exposure_multiplier_no_open_positions():
    assert correlated_exposure_multiplier("NIFTY", "up", []) == 1.0


def test_correlated_exposure_multiplier_one_same_direction_position_halves():
    assert correlated_exposure_multiplier("NIFTY", "up", [("BANKNIFTY", "up")]) == 0.5


def test_correlated_exposure_multiplier_two_same_direction_positions_blocks():
    assert correlated_exposure_multiplier("NIFTY", "up", [("BANKNIFTY", "up"), ("SENSEX", "up")]) == 0.0


def test_correlated_exposure_multiplier_ignores_opposite_direction():
    assert correlated_exposure_multiplier("NIFTY", "up", [("BANKNIFTY", "down")]) == 1.0


def test_correlated_exposure_multiplier_unknown_underlying_unaffected():
    assert correlated_exposure_multiplier("RELIANCE", "up", [("BANKNIFTY", "up"), ("SENSEX", "up")]) == 1.0
