import datetime as dt
import random

import pytest

from backtesting.event_loop import BacktestConfig, run_backtest
from backtesting.portfolio_equity_simulation import PortfolioEquitySimulationResult, PortfolioSimulatedTrade, simulate_portfolio_equity_curve
from backtesting.portfolio_sequence_risk import _riffle_interleave, monte_carlo_portfolio_sequence, replay_portfolio_in_logical_order
from backtesting.trade_record import Trade
from data.storage import load_ohlcv

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
BANKNIFTY_DAILY = load_ohlcv("BANKNIFTY", "ONE_DAY")
SENSEX_DAILY = load_ohlcv("SENSEX", "ONE_DAY")
requires_real_data = pytest.mark.skipif(
    len(NIFTY_DAILY) == 0 or len(BANKNIFTY_DAILY) == 0 or len(SENSEX_DAILY) == 0,
    reason="real NIFTY/BANKNIFTY/SENSEX daily data not pulled locally",
)


def _sim_trade(instrument: str, entry_index: int, exit_index: int, entry_premium: float, exit_premium: float) -> PortfolioSimulatedTrade:
    trade = Trade(
        strategy_name="t", direction="CE", entry_index=entry_index, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=entry_premium, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=95.0, target_price=110.0, exit_index=exit_index, exit_timestamp=dt.datetime(2026, 1, 2),
        exit_spot=100.0, exit_premium=exit_premium, exit_reason="target", pnl=exit_premium - entry_premium,
    )
    return PortfolioSimulatedTrade(
        instrument=instrument, trade=trade, lots=0, quantity=0, cash_before=0, capital_at_risk=0,
        equity_tier="NORMAL", gross_pnl=0, cost=0, net_pnl=0, cash_after=0,
        strike=100.0, entry_premium=entry_premium, exit_premium=exit_premium,
    )


def test_riffle_interleave_preserves_each_sequence_own_internal_order():
    a = ["a1", "a2", "a3"]
    b = ["b1", "b2"]
    rng = random.Random(0)
    result = _riffle_interleave([a, b], rng)
    assert [x for x in result if x.startswith("a")] == a
    assert [x for x in result if x.startswith("b")] == b
    assert len(result) == 5


def test_riffle_interleave_handles_empty_sequence():
    a = ["a1", "a2"]
    rng = random.Random(0)
    result = _riffle_interleave([a, []], rng)
    assert result == a


@requires_real_data
def test_monte_carlo_portfolio_sequence_runs_and_reports_sane_bounds():
    lot_sizes = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}
    strike_increments = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}
    dfs = {"NIFTY": NIFTY_DAILY, "BANKNIFTY": BANKNIFTY_DAILY, "SENSEX": SENSEX_DAILY}
    bts = {sym: run_backtest(dfs[sym], BacktestConfig(warmup_bars=30)) for sym in lot_sizes}

    base = simulate_portfolio_equity_curve(
        trades_by_instrument={sym: bt.trades for sym, bt in bts.items()},
        dfs_by_instrument=dfs, lot_sizes=lot_sizes, strike_increments=strike_increments,
        starting_capital=50_000, risk_free_rate=0.07, base_risk_pct=0.01,
    )
    result = monte_carlo_portfolio_sequence(base, lot_sizes, n_simulations=200, base_risk_pct=0.01, rng=random.Random(0))
    assert result.n_simulations == 200
    assert result.observed_ending_capital == pytest.approx(base.ending_capital)
    assert result.ci_low <= result.mean_simulated_ending_capital <= result.ci_high
    assert 0.0 <= result.fraction_worse_than_observed <= 1.0


def test_replay_portfolio_in_logical_order_is_deterministic_for_a_fixed_order():
    a = _sim_trade("NIFTY", 0, 2, 5.0, 8.0)
    b = _sim_trade("BANKNIFTY", 3, 5, 4.0, 2.0)
    from backtesting.portfolio_sequence_risk import _LogicalTradeEvent
    events = [
        _LogicalTradeEvent(instrument="NIFTY", trade=a.trade, entry_premium=5.0, exit_premium=8.0, strike=100.0, duration=2),
        _LogicalTradeEvent(instrument="BANKNIFTY", trade=b.trade, entry_premium=4.0, exit_premium=2.0, strike=100.0, duration=2),
    ]
    lot_sizes = {"NIFTY": 65, "BANKNIFTY": 30}
    r1 = replay_portfolio_in_logical_order(events, lot_sizes, starting_capital=50_000, base_risk_pct=0.05)
    r2 = replay_portfolio_in_logical_order(list(events), lot_sizes, starting_capital=50_000, base_risk_pct=0.05)
    assert r1.ending_capital == pytest.approx(r2.ending_capital)


def test_replay_portfolio_in_logical_order_allows_concurrent_different_instruments():
    from backtesting.portfolio_sequence_risk import _LogicalTradeEvent
    a_trade = _sim_trade("NIFTY", 0, 5, 5.0, 8.0).trade
    b_trade = _sim_trade("BANKNIFTY", 0, 5, 4.0, 6.0).trade
    events = [
        _LogicalTradeEvent(instrument="NIFTY", trade=a_trade, entry_premium=5.0, exit_premium=8.0, strike=100.0, duration=5),
        _LogicalTradeEvent(instrument="BANKNIFTY", trade=b_trade, entry_premium=4.0, exit_premium=6.0, strike=100.0, duration=5),
    ]
    lot_sizes = {"NIFTY": 65, "BANKNIFTY": 30}
    result = replay_portfolio_in_logical_order(events, lot_sizes, starting_capital=50_000, base_risk_pct=0.1)
    assert result.max_concurrent_positions == 2
    assert len(result.simulated_trades) == 2
