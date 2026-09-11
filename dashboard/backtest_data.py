"""Backtest data-preparation for the dashboard - always informational,
never a claim of validated performance. See backtesting/BACKTESTS.md
for the full, honest validation history (walk-forward, bootstrap/Monte
Carlo, attribution) - this module just runs the same engine on demand
for interactive viewing.
"""
from dataclasses import dataclass

from backtesting.event_loop import BacktestConfig, run_backtest
from backtesting.metrics import PerformanceSummary, summarize
from backtesting.trade_record import Trade
from data.storage import load_ohlcv


@dataclass
class DashboardBacktestResult:
    trades: list[Trade]
    summary: PerformanceSummary


def run_backtest_for_dashboard(
    instrument: str,
    interval: str = "ONE_DAY",
    use_contract_selector: bool = False,
) -> DashboardBacktestResult | None:
    df = load_ohlcv(instrument, interval)
    if len(df) == 0:
        return None
    config = BacktestConfig(use_contract_selector=use_contract_selector)
    result = run_backtest(df, config)
    return DashboardBacktestResult(trades=result.trades, summary=summarize(result.trades))
