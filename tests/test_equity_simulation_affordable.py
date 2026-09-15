"""requires_real_data tests need data/raw/NIFTY/ONE_DAY.parquet (see
tests/test_feature_engineering.py's module docstring for why)."""
import datetime as dt

import pytest

from backtesting.equity_simulation import simulate_equity_curve, simulate_equity_curve_with_affordable_contracts
from backtesting.event_loop import BacktestConfig, run_backtest
from data.storage import load_ohlcv
from execution.transaction_costs import TransactionCostRates

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")

ZERO_COST_RATES = TransactionCostRates(brokerage_per_order=0, stt_sell_pct=0, exchange_txn_pct=0, sebi_fee_pct=0, stamp_duty_pct=0, gst_pct=0)


@requires_real_data
def test_affordable_simulation_finds_trades_where_plain_simulation_finds_none():
    """The exact scenario Run 007 documented: at Rs.50,000 capital and a
    conservative 1% risk-per-trade, the plain simulation (fixed ATM
    strike) affords ZERO trades on NIFTY. The affordability-aware
    version, searching outward for a cheap enough strike, should afford
    at least some."""
    bt = run_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30))
    plain = simulate_equity_curve(bt.trades, lot_size=65, starting_capital=50_000, base_risk_pct=0.01, rates=ZERO_COST_RATES)
    affordable = simulate_equity_curve_with_affordable_contracts(
        bt.trades, NIFTY_DAILY, lot_size=65, starting_capital=50_000, risk_free_rate=0.07,
        strike_increment=50, base_risk_pct=0.01, rates=ZERO_COST_RATES,
    )
    assert len(plain.simulated_trades) == 0
    assert len(affordable.simulated_trades) > 0


@requires_real_data
def test_affordable_simulation_produces_sensible_sized_trades():
    bt = run_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30))
    result = simulate_equity_curve_with_affordable_contracts(
        bt.trades, NIFTY_DAILY, lot_size=65, starting_capital=50_000, risk_free_rate=0.07,
        strike_increment=50, base_risk_pct=0.02, rates=ZERO_COST_RATES,
    )
    assert len(result.simulated_trades) > 0
    for st in result.simulated_trades:
        assert st.quantity > 0
        assert st.lots > 0
        assert st.capital_at_risk > 0


@requires_real_data
def test_affordable_simulation_respects_max_lots():
    bt = run_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30))
    result = simulate_equity_curve_with_affordable_contracts(
        bt.trades, NIFTY_DAILY, lot_size=65, starting_capital=50_000, risk_free_rate=0.07,
        strike_increment=50, base_risk_pct=0.5, max_lots=2, rates=ZERO_COST_RATES,
    )
    for st in result.simulated_trades:
        assert st.lots <= 2


@requires_real_data
def test_affordable_simulation_skips_trades_during_stop_tier():
    bt = run_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30))
    # An extreme base_risk_pct should eventually blow through the STOP
    # threshold, after which everything remaining must be skipped.
    result = simulate_equity_curve_with_affordable_contracts(
        bt.trades, NIFTY_DAILY, lot_size=65, starting_capital=50_000, risk_free_rate=0.07,
        strike_increment=50, base_risk_pct=1.0, rates=ZERO_COST_RATES,
    )
    if result.ending_capital <= 50_000 * 0.85 and len(result.skipped_trades) > 0:
        # once STOP is reached, no simulated trade should appear after
        # the last skipped-for-STOP trade in entry order
        last_simulated_index = max((st.trade.entry_index for st in result.simulated_trades), default=-1)
        stop_tier_skips = [t for t in result.skipped_trades if t.entry_index > last_simulated_index]
        assert len(stop_tier_skips) > 0


@requires_real_data
def test_affordable_simulation_real_transaction_costs_reduce_net_pnl():
    bt = run_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30))
    result = simulate_equity_curve_with_affordable_contracts(
        bt.trades, NIFTY_DAILY, lot_size=65, starting_capital=50_000, risk_free_rate=0.07,
        strike_increment=50, base_risk_pct=0.02,  # default (real) rates
    )
    for st in result.simulated_trades:
        assert st.cost > 0
        assert st.net_pnl < st.gross_pnl


@requires_real_data
def test_affordable_simulation_returns_empty_result_for_no_trades():
    result = simulate_equity_curve_with_affordable_contracts(
        [], NIFTY_DAILY, lot_size=65, starting_capital=50_000, risk_free_rate=0.07, strike_increment=50,
    )
    assert result.simulated_trades == []
    assert result.ending_capital == result.starting_capital
