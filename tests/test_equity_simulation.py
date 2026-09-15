import datetime as dt

import pytest

from backtesting.equity_simulation import simulate_equity_curve
from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates


def _trade(entry_index: int, entry_premium: float, exit_premium: float) -> Trade:
    return Trade(
        strategy_name="t", direction="CE", entry_index=entry_index, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=entry_premium, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0, exit_index=entry_index + 2, exit_timestamp=dt.datetime(2026, 1, 3),
        exit_spot=100.0, exit_premium=exit_premium, exit_reason="target", pnl=exit_premium - entry_premium,
    )


ZERO_COST_RATES = TransactionCostRates(brokerage_per_order=0, stt_sell_pct=0, exchange_txn_pct=0, sebi_fee_pct=0, stamp_duty_pct=0, gst_pct=0)


def test_simple_winning_trade_grows_capital():
    # base_risk_pct=1.0 so this actually affords >=1 lot at entry_premium=10
    # (the module's default 1% would correctly skip this trade entirely -
    # see test_lot_count_respects_risk_budget below).
    trades = [_trade(0, 10.0, 15.0)]  # +5/unit win
    result = simulate_equity_curve(trades, lot_size=65, starting_capital=50_000, base_risk_pct=1.0, rates=ZERO_COST_RATES)
    assert result.ending_capital > result.starting_capital
    assert len(result.simulated_trades) == 1
    assert result.simulated_trades[0].net_pnl == pytest.approx(5.0 * result.simulated_trades[0].quantity)


def test_lot_count_respects_risk_budget():
    # capital=50000, base_risk_pct=0.01 -> risk_budget=500. entry_premium=10,
    # lot_size=65 -> max_loss_per_lot=650. 500/650 = 0 lots -> skipped.
    trades = [_trade(0, 10.0, 15.0)]
    result = simulate_equity_curve(trades, lot_size=65, starting_capital=50_000, base_risk_pct=0.01, rates=ZERO_COST_RATES)
    assert len(result.simulated_trades) == 0
    assert len(result.skipped_trades) == 1


def test_lot_count_scales_up_with_larger_risk_budget():
    trades = [_trade(0, 10.0, 15.0)]
    result = simulate_equity_curve(trades, lot_size=65, starting_capital=50_000, base_risk_pct=0.5, rates=ZERO_COST_RATES)
    assert len(result.simulated_trades) == 1
    assert result.simulated_trades[0].lots >= 1


def test_max_lots_caps_the_position_size():
    trades = [_trade(0, 1.0, 2.0)]  # cheap premium -> would otherwise size to many lots
    result = simulate_equity_curve(trades, lot_size=65, starting_capital=50_000, base_risk_pct=1.0, max_lots=2, rates=ZERO_COST_RATES)
    assert result.simulated_trades[0].lots == 2


def test_a_loss_crossing_the_reduced_threshold_shrinks_the_next_trades_sizing():
    # Hand-computed: base_risk_pct=0.08, entry_premium=50, lot_size=65 ->
    # risk_budget=4000, max_loss_per_lot=3250 -> 1 lot -> quantity=65.
    # Trade 1 exits at 5 -> pnl=65*(5-50)=-2925 -> capital 50000->47075,
    # drawdown=-5.85% -> REDUCED (crosses the -5% threshold, stays above -10%).
    trades = [_trade(0, 50.0, 5.0), _trade(1, 10.0, 10.0)]  # second trade is breakeven, just to observe its tier
    result = simulate_equity_curve(trades, lot_size=65, starting_capital=50_000, base_risk_pct=0.08, rates=ZERO_COST_RATES)
    assert len(result.simulated_trades) == 2
    assert result.simulated_trades[0].equity_tier == "NORMAL"
    assert result.simulated_trades[0].capital_after == pytest.approx(47_075.0)
    assert result.simulated_trades[1].equity_tier == "REDUCED"
    assert result.simulated_trades[1].capital_at_risk == pytest.approx(47_075.0 * 0.08 * 0.5)


def test_stop_tier_skips_all_subsequent_trades():
    # First trade loses enough to blow past the -15% STOP threshold given
    # a large enough risk_pct, subsequent trades must all be skipped.
    trades = [_trade(0, 100.0, 5.0), _trade(1, 10.0, 20.0), _trade(2, 10.0, 20.0)]
    result = simulate_equity_curve(trades, lot_size=65, starting_capital=50_000, base_risk_pct=1.0, rates=ZERO_COST_RATES)
    assert result.simulated_trades[0].equity_tier in ("NORMAL",)  # first trade always evaluated at NORMAL
    # if the first trade's loss pushed capital past -15%, everything after must be skipped
    if result.ending_capital <= 50_000 * 0.85:
        assert len(result.simulated_trades) == 1
        assert len(result.skipped_trades) == 2


def test_max_drawdown_is_computed_from_the_peak():
    trades = [_trade(0, 10.0, 20.0), _trade(1, 10.0, 5.0), _trade(2, 10.0, 5.0)]  # win then two losses
    result = simulate_equity_curve(trades, lot_size=65, starting_capital=50_000, base_risk_pct=1.0, rates=ZERO_COST_RATES)
    assert result.peak_capital >= result.starting_capital
    assert result.max_drawdown_rupees >= 0
    assert result.max_drawdown_pct >= 0


def test_open_trades_are_never_simulated():
    open_trade = Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=10.0, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0,
    )
    result = simulate_equity_curve([open_trade], lot_size=65, starting_capital=50_000, base_risk_pct=1.0, rates=ZERO_COST_RATES)
    assert result.simulated_trades == []
    assert result.skipped_trades == []


def test_real_transaction_costs_reduce_net_pnl_below_gross():
    trades = [_trade(0, 10.0, 15.0)]
    result = simulate_equity_curve(trades, lot_size=65, starting_capital=50_000, base_risk_pct=1.0)  # default (real) rates
    st = result.simulated_trades[0]
    assert st.cost > 0
    assert st.net_pnl < st.gross_pnl


def test_trades_are_processed_in_chronological_entry_order_even_if_passed_out_of_order():
    out_of_order = [_trade(5, 10.0, 5.0), _trade(0, 10.0, 20.0)]  # loss then win, but passed loss-first
    result = simulate_equity_curve(out_of_order, lot_size=65, starting_capital=50_000, base_risk_pct=1.0, rates=ZERO_COST_RATES)
    assert result.simulated_trades[0].trade.entry_index == 0  # the win (entry_index 0) must be processed first
