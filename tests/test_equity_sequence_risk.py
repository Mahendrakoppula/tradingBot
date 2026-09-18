import datetime as dt
import random

import pandas as pd
import pytest

from backtesting.equity_sequence_risk import monte_carlo_equity_sequence, replay_equity_curve_in_order
from backtesting.equity_simulation import EquitySimulationResult, SimulatedTrade
from backtesting.event_loop import BacktestConfig, run_backtest
from backtesting.equity_simulation import simulate_equity_curve_with_affordable_contracts
from backtesting.trade_record import Trade
from data.storage import load_ohlcv

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")


def _sim_trade(entry_premium: float, exit_premium: float, pnl_hint: float) -> SimulatedTrade:
    trade = Trade(
        strategy_name="t", direction="CE", entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=entry_premium, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0, exit_index=1, exit_timestamp=dt.datetime(2026, 1, 2),
        exit_spot=100.0, exit_premium=exit_premium, exit_reason="target", pnl=pnl_hint,
    )
    return SimulatedTrade(
        trade=trade, lots=0, quantity=0, capital_before=0, capital_at_risk=0, equity_tier="NORMAL",
        gross_pnl=0, cost=0, net_pnl=0, capital_after=0, strike=100.0,
        entry_premium=entry_premium, exit_premium=exit_premium,
    )


def test_replay_equity_curve_in_order_is_deterministic_for_a_fixed_order():
    trades = [_sim_trade(5.0, 8.0, 3.0), _sim_trade(4.0, 2.0, -2.0), _sim_trade(6.0, 12.0, 6.0)]
    result_a = replay_equity_curve_in_order(trades, lot_size=65, starting_capital=50_000, base_risk_pct=0.01)
    result_b = replay_equity_curve_in_order(list(trades), lot_size=65, starting_capital=50_000, base_risk_pct=0.01)
    assert result_a.ending_capital == pytest.approx(result_b.ending_capital)


def test_replay_equity_curve_in_order_changes_with_order():
    """The whole point of this module: reordering the SAME trades can
    change the ending capital, because lot sizing and tier gating
    depend on capital available AT THAT POINT in the sequence."""
    winner = _sim_trade(5.0, 50.0, 45.0)   # a huge winner
    loser = _sim_trade(5.0, 0.5, -4.5)     # a loser

    winner_first = replay_equity_curve_in_order([winner, loser], lot_size=65, starting_capital=50_000, base_risk_pct=0.5)
    loser_first = replay_equity_curve_in_order([loser, winner], lot_size=65, starting_capital=50_000, base_risk_pct=0.5)
    # With a large risk_pct, the winner-first path compounds a bigger capital base into the second trade's sizing -
    # not asserting a specific direction, just that order actually matters (they must differ).
    assert winner_first.ending_capital != pytest.approx(loser_first.ending_capital)


def test_replay_equity_curve_in_order_skips_trade_when_unaffordable():
    tiny_capital_trade = _sim_trade(1000.0, 1000.0, 0.0)  # premium way beyond what a small risk budget affords
    result = replay_equity_curve_in_order([tiny_capital_trade], lot_size=65, starting_capital=1000, base_risk_pct=0.01)
    assert len(result.simulated_trades) == 0
    assert len(result.skipped_trades) == 1
    assert result.ending_capital == pytest.approx(1000)


def test_monte_carlo_equity_sequence_runs_and_reports_sane_bounds():
    trades = [_sim_trade(5.0, 8.0, 3.0), _sim_trade(4.0, 2.0, -2.0), _sim_trade(6.0, 12.0, 6.0), _sim_trade(3.0, 1.0, -2.0)]
    base = replay_equity_curve_in_order(trades, lot_size=65, starting_capital=50_000, base_risk_pct=0.05)
    base_result = EquitySimulationResult(
        starting_capital=50_000, ending_capital=base.ending_capital, simulated_trades=base.simulated_trades,
        skipped_trades=base.skipped_trades, peak_capital=base.peak_capital,
        max_drawdown_rupees=base.max_drawdown_rupees, max_drawdown_pct=base.max_drawdown_pct,
    )
    result = monte_carlo_equity_sequence(base_result, lot_size=65, n_simulations=200, base_risk_pct=0.05, rng=random.Random(0))
    assert result.n_simulations == 200
    assert result.ci_low <= result.mean_simulated_ending_capital <= result.ci_high
    assert 0.0 <= result.fraction_worse_than_observed <= 1.0


@requires_real_data
def test_monte_carlo_equity_sequence_on_real_data_runs_end_to_end():
    bt = run_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30))
    base_result = simulate_equity_curve_with_affordable_contracts(
        bt.trades, NIFTY_DAILY, lot_size=65, starting_capital=50_000, risk_free_rate=0.07,
        strike_increment=50, base_risk_pct=0.01,
    )
    assert len(base_result.simulated_trades) > 0
    result = monte_carlo_equity_sequence(base_result, lot_size=65, n_simulations=200, base_risk_pct=0.01, rng=random.Random(0))
    assert result.observed_ending_capital == pytest.approx(base_result.ending_capital)
    assert result.n_simulations == 200
