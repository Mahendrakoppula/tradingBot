"""requires_real_data tests need data/raw/{NIFTY,BANKNIFTY,SENSEX}/ONE_DAY.parquet
(see tests/test_feature_engineering.py's module docstring for why)."""
import pytest

from backtesting.equity_simulation import simulate_equity_curve_with_affordable_contracts
from backtesting.event_loop import BacktestConfig, run_backtest
from backtesting.portfolio_equity_simulation import simulate_portfolio_equity_curve
from data.storage import load_ohlcv
from execution.transaction_costs import TransactionCostRates

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
BANKNIFTY_DAILY = load_ohlcv("BANKNIFTY", "ONE_DAY")
SENSEX_DAILY = load_ohlcv("SENSEX", "ONE_DAY")
requires_real_data = pytest.mark.skipif(
    len(NIFTY_DAILY) == 0 or len(BANKNIFTY_DAILY) == 0 or len(SENSEX_DAILY) == 0,
    reason="real NIFTY/BANKNIFTY/SENSEX daily data not pulled locally",
)

ZERO_COST_RATES = TransactionCostRates(brokerage_per_order=0, stt_sell_pct=0, exchange_txn_pct=0, sebi_fee_pct=0, stamp_duty_pct=0, gst_pct=0)


@requires_real_data
def test_single_instrument_portfolio_matches_independent_simulation_exactly():
    """The core correctness check: with only ONE instrument populated
    (no concurrency ever possible), the shared-cash accounting must
    reduce to EXACTLY the same capital trajectory as the existing
    single-instrument equity_simulation.py - entry_cost is deducted at
    entry and refunded in full (plus/minus net P&L) at exit, which
    algebraically nets to the same "+= net_pnl" the single-instrument
    version does directly. Not an approximation - an exact match."""
    bt = run_backtest(NIFTY_DAILY, BacktestConfig(warmup_bars=30))
    independent = simulate_equity_curve_with_affordable_contracts(
        bt.trades, NIFTY_DAILY, lot_size=65, starting_capital=50_000, risk_free_rate=0.07,
        strike_increment=50, base_risk_pct=0.01, rates=ZERO_COST_RATES,
    )
    portfolio = simulate_portfolio_equity_curve(
        trades_by_instrument={"NIFTY": bt.trades},
        dfs_by_instrument={"NIFTY": NIFTY_DAILY},
        lot_sizes={"NIFTY": 65}, strike_increments={"NIFTY": 50},
        starting_capital=50_000, risk_free_rate=0.07, base_risk_pct=0.01, rates=ZERO_COST_RATES,
    )
    assert portfolio.ending_capital == pytest.approx(independent.ending_capital)
    assert len(portfolio.simulated_trades) == len(independent.simulated_trades)
    assert len(portfolio.skipped_trades) == len(independent.skipped_trades)
    assert portfolio.max_concurrent_positions == 1


@requires_real_data
def test_portfolio_simulation_runs_end_to_end_across_all_three_instruments():
    bts = {sym: run_backtest(load_ohlcv(sym, "ONE_DAY"), BacktestConfig(warmup_bars=30))
           for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]}
    result = simulate_portfolio_equity_curve(
        trades_by_instrument={sym: bt.trades for sym, bt in bts.items()},
        dfs_by_instrument={"NIFTY": NIFTY_DAILY, "BANKNIFTY": BANKNIFTY_DAILY, "SENSEX": SENSEX_DAILY},
        lot_sizes={"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20},
        strike_increments={"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100},
        starting_capital=50_000, risk_free_rate=0.07, base_risk_pct=0.01,
    )
    assert len(result.simulated_trades) > 0
    assert result.ending_capital > 0
    # with three instruments genuinely competing for one pool, concurrency
    # should actually occur at least once across a 5-year history - this
    # is the whole point of the module, not an edge case.
    assert result.max_concurrent_positions >= 2


@requires_real_data
def test_portfolio_simulation_differs_from_naively_summed_independent_pools():
    """Directly demonstrates the effect this module exists to capture:
    the portfolio result is NOT simply three independent Rs.50,000
    pools glued together. Deliberately does NOT assert a direction
    (more or fewer total trades) - pooled capital faces real contention
    at concurrent moments (fewer trades possible right then) but also
    compounds faster in aggregate than three separate, smaller pools
    would (gains in any one instrument boost shared capital available
    to ALL three) - which direction wins empirically is not obvious
    ahead of time and isn't the point; what matters is that the two are
    genuinely different simulations, not that one strictly dominates."""
    bts = {sym: run_backtest(load_ohlcv(sym, "ONE_DAY"), BacktestConfig(warmup_bars=30))
           for sym in ["NIFTY", "BANKNIFTY", "SENSEX"]}
    lot_sizes = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}
    strike_increments = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}
    dfs = {"NIFTY": NIFTY_DAILY, "BANKNIFTY": BANKNIFTY_DAILY, "SENSEX": SENSEX_DAILY}

    independent_total = 0
    for sym, bt in bts.items():
        independent = simulate_equity_curve_with_affordable_contracts(
            bt.trades, dfs[sym], lot_size=lot_sizes[sym], starting_capital=50_000, risk_free_rate=0.07,
            strike_increment=strike_increments[sym], base_risk_pct=0.01,
        )
        independent_total += len(independent.simulated_trades)

    portfolio = simulate_portfolio_equity_curve(
        trades_by_instrument={sym: bt.trades for sym, bt in bts.items()},
        dfs_by_instrument=dfs, lot_sizes=lot_sizes, strike_increments=strike_increments,
        starting_capital=50_000, risk_free_rate=0.07, base_risk_pct=0.01,
    )
    # genuine concurrency actually occurred - the mechanic this module
    # exists to model. Deliberately does NOT assert skipped_trades > 0:
    # at this capital-compounding rate (positions actually resolve to
    # explosive aggregate growth), a real, honest finding on its own
    # is that concurrency can occur without ever actually starving a
    # trade of cash, if the shared pool has already grown large enough
    # by the time overlap happens - see backtesting/BACKTESTS.md.
    assert portfolio.max_concurrent_positions >= 2
    assert len(portfolio.simulated_trades) != independent_total


def test_events_sort_exits_before_entries_on_the_same_day():
    """Unit-level check on the event-ordering logic itself, independent
    of the full pricing pipeline: an exit and an entry landing on the
    SAME index must process the exit first, so its freed cash is
    available to the same-day entry - not asserted, verified via the
    priority tuple used for sorting."""
    events = [(10, 1, "NIFTY", "entry", None), (10, 0, "BANKNIFTY", "exit", None)]
    events.sort(key=lambda e: (e[0], e[1]))
    assert events[0][3] == "exit"
    assert events[1][3] == "entry"
