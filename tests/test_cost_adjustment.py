import datetime as dt

import pytest

from backtesting.cost_adjustment import cost_adjust_backtest, cost_adjust_trade
from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates


def _trade(entry_premium: float, exit_premium: float) -> Trade:
    return Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=entry_premium, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0, exit_index=2, exit_timestamp=dt.datetime(2026, 1, 3),
        exit_spot=100.0, exit_premium=exit_premium, exit_reason="target", pnl=exit_premium - entry_premium,
    )


def test_cost_adjust_trade_scales_gross_pnl_by_lot_size():
    trade = _trade(entry_premium=10.0, exit_premium=15.0)  # +5/unit
    result = cost_adjust_trade(trade, lot_size=65)
    assert result.gross_pnl == pytest.approx(5.0 * 65)


def test_cost_adjust_trade_net_pnl_is_gross_minus_cost():
    trade = _trade(entry_premium=10.0, exit_premium=15.0)
    result = cost_adjust_trade(trade, lot_size=65)
    assert result.net_pnl == pytest.approx(result.gross_pnl - result.cost)
    assert result.cost > 0


def test_cost_adjust_trade_raises_for_an_open_trade():
    open_trade = Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=10.0, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0,
    )
    with pytest.raises(ValueError):
        cost_adjust_trade(open_trade, lot_size=65)


def test_a_small_gross_win_can_flip_negative_after_realistic_costs():
    """The exact scenario `main`'s own costs.py was motivated by: a small
    gross win (e.g. Rs.0.50/unit on NIFTY) can be wiped out or reversed
    by real round-trip costs at a real lot size."""
    trade = _trade(entry_premium=10.0, exit_premium=10.5)  # +0.5/unit gross win
    result = cost_adjust_trade(trade, lot_size=65)
    assert result.gross_pnl > 0
    assert result.net_pnl < result.gross_pnl
    assert result.net_pnl < 0  # flips to a loss once real costs are applied


def test_cost_adjust_backtest_summarizes_gross_and_net_separately():
    trades = [_trade(10.0, 15.0), _trade(20.0, 15.0), _trade(30.0, 32.0)]
    result = cost_adjust_backtest(trades, lot_size=65)

    assert len(result.adjusted_trades) == 3
    assert result.gross_summary.n_trades == 3
    assert result.net_summary.n_trades == 3
    # net must be strictly worse than gross once costs are subtracted
    assert result.net_summary.total_pnl < result.gross_summary.total_pnl
    assert result.total_cost > 0
    assert result.total_cost == pytest.approx(result.gross_summary.total_pnl - result.net_summary.total_pnl)


def test_cost_adjust_backtest_ignores_open_trades():
    open_trade = Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=10.0, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0,
    )
    trades = [_trade(10.0, 15.0), open_trade]
    result = cost_adjust_backtest(trades, lot_size=65)
    assert len(result.adjusted_trades) == 1


def test_custom_rates_are_respected():
    trade = _trade(10.0, 15.0)
    cheap_rates = TransactionCostRates(brokerage_per_order=0.0, stt_sell_pct=0.0, exchange_txn_pct=0.0, sebi_fee_pct=0.0, stamp_duty_pct=0.0, gst_pct=0.0)
    result = cost_adjust_trade(trade, lot_size=65, rates=cheap_rates)
    assert result.cost == 0.0
    assert result.net_pnl == result.gross_pnl
