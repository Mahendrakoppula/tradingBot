import datetime as dt

import pytest

from backtesting.slippage_sensitivity import (
    DEFAULT_SLIPPAGE_LEVELS_PCT,
    combined_adjusted_pnl,
    slippage_sensitivity_sweep,
)
from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates


def _trade(entry_premium: float, exit_premium: float) -> Trade:
    return Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=entry_premium, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0, exit_index=2, exit_timestamp=dt.datetime(2026, 1, 3),
        exit_spot=100.0, exit_premium=exit_premium, exit_reason="target", pnl=exit_premium - entry_premium,
    )


def test_zero_slippage_matches_pure_cost_adjustment():
    from backtesting.cost_adjustment import cost_adjust_trade
    trade = _trade(10.0, 15.0)
    combined = combined_adjusted_pnl(trade, lot_size=65, slippage_pct=0.0)
    cost_only = cost_adjust_trade(trade, lot_size=65).net_pnl
    assert combined == pytest.approx(cost_only)


def test_higher_slippage_reduces_pnl_further():
    trade = _trade(10.0, 15.0)
    low = combined_adjusted_pnl(trade, lot_size=65, slippage_pct=0.5)
    high = combined_adjusted_pnl(trade, lot_size=65, slippage_pct=5.0)
    assert high < low


def test_slippage_cost_scales_with_premium_level():
    """A higher-premium trade should absorb more slippage rupees at the
    same percentage, since slippage is applied to the premium level, not
    a flat rupee amount."""
    cheap = combined_adjusted_pnl(_trade(10.0, 12.0), lot_size=65, slippage_pct=2.0)
    cheap_gross = (12.0 - 10.0) * 65
    expensive = combined_adjusted_pnl(_trade(500.0, 502.0), lot_size=65, slippage_pct=2.0)
    expensive_gross = (502.0 - 500.0) * 65
    # both have the same GROSS pnl (2/unit * 65), but the expensive one
    # must lose much more to slippage since slippage scales with premium
    assert cheap_gross == pytest.approx(expensive_gross)
    assert (cheap_gross - cheap) < (expensive_gross - expensive)


def test_combined_adjusted_pnl_raises_for_open_trade():
    open_trade = Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=10.0, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0,
    )
    with pytest.raises(ValueError):
        combined_adjusted_pnl(open_trade, lot_size=65, slippage_pct=1.0)


def test_sweep_produces_one_point_per_level_in_order():
    trades = [_trade(10.0, 15.0), _trade(20.0, 18.0)]
    result = slippage_sensitivity_sweep(trades, lot_size=65)
    assert [p.slippage_pct for p in result] == list(DEFAULT_SLIPPAGE_LEVELS_PCT)


def test_sweep_total_pnl_is_monotonically_non_increasing_with_slippage():
    trades = [_trade(10.0, 15.0), _trade(20.0, 18.0), _trade(30.0, 40.0)]
    result = slippage_sensitivity_sweep(trades, lot_size=65)
    pnls = [p.summary.total_pnl for p in result]
    assert pnls == sorted(pnls, reverse=True)  # non-increasing as slippage rises


def test_sweep_ignores_open_trades():
    open_trade = Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=10.0, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0,
    )
    result = slippage_sensitivity_sweep([_trade(10.0, 15.0), open_trade], lot_size=65)
    assert all(p.summary.n_trades == 1 for p in result)


def test_custom_slippage_levels_and_rates_are_respected():
    trades = [_trade(10.0, 15.0)]
    zero_rates = TransactionCostRates(brokerage_per_order=0, stt_sell_pct=0, exchange_txn_pct=0, sebi_fee_pct=0, stamp_duty_pct=0, gst_pct=0)
    result = slippage_sensitivity_sweep(trades, lot_size=65, slippage_levels_pct=(0.0, 10.0), rates=zero_rates)
    assert [p.slippage_pct for p in result] == [0.0, 10.0]
    assert result[0].summary.total_pnl == pytest.approx((15.0 - 10.0) * 65)  # zero cost, zero slippage = pure gross
