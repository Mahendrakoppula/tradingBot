"""Walk-forward / out-of-sample validation (spec Phase 13). The
strategies backtested here (strategies/portfolio.py) are rule-based,
not fit to data, so there's no "retrain per fold" step for them - what
this module validates instead is whether backtesting/event_loop.py's
Run 001 result (backtesting/BACKTESTS.md) holds up across several
independent stretches of history, or was a lucky feature of that one
full-history window. Each fold is treated as its OWN independent
series (own warmup, own daily-risk-engine state) - performance in one
fold never depends on what happened in another, by construction.

An embargo gap between consecutive folds reduces boundary leakage from
any rolling-window feature (ATR, realized vol, etc.) computed near a
fold's edges, per the spec's "purged CV, embargo periods" requirement.
"""
from dataclasses import dataclass

import pandas as pd

from backtesting.event_loop import BacktestConfig, run_backtest
from backtesting.metrics import PerformanceSummary, summarize


def generate_windows(n: int, n_windows: int, embargo_bars: int = 0) -> list[tuple[int, int]]:
    """Non-overlapping [start, end) index ranges spanning as much of
    [0, n) as divides evenly, each pair separated by `embargo_bars`."""
    if n_windows < 1:
        raise ValueError("n_windows must be >= 1")
    total_embargo = (n_windows - 1) * embargo_bars
    window_size = (n - total_embargo) // n_windows
    if window_size <= 0:
        raise ValueError(f"Not enough bars ({n}) for {n_windows} windows with embargo_bars={embargo_bars}")

    windows = []
    start = 0
    for _ in range(n_windows):
        end = start + window_size
        windows.append((start, end))
        start = end + embargo_bars
    return windows


@dataclass
class WalkForwardResult:
    windows: list[tuple[int, int]]
    window_summaries: list[PerformanceSummary]


def walk_forward_backtest(df: pd.DataFrame, config: BacktestConfig, n_windows: int, embargo_bars: int = 5) -> WalkForwardResult:
    windows = generate_windows(len(df), n_windows, embargo_bars)
    summaries = []
    for start, end in windows:
        window_df = df.iloc[start:end].reset_index(drop=True)
        result = run_backtest(window_df, config)
        summaries.append(summarize(result.trades))
    return WalkForwardResult(windows=windows, window_summaries=summaries)


@dataclass
class WalkForwardAggregate:
    n_folds: int
    n_folds_with_trades: int
    total_trades: int
    mean_win_rate: float | None
    mean_total_pnl: float
    std_total_pnl: float
    n_folds_profitable: int


def aggregate(walk_forward_result: WalkForwardResult) -> WalkForwardAggregate:
    summaries = walk_forward_result.window_summaries
    with_trades = [s for s in summaries if s.n_trades > 0]
    total_trades = sum(s.n_trades for s in summaries)

    win_rates = [s.win_rate for s in with_trades if s.win_rate is not None]
    mean_win_rate = sum(win_rates) / len(win_rates) if win_rates else None

    pnls = [s.total_pnl for s in summaries]
    mean_pnl = sum(pnls) / len(pnls) if pnls else 0.0
    variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls) if pnls else 0.0

    return WalkForwardAggregate(
        n_folds=len(summaries),
        n_folds_with_trades=len(with_trades),
        total_trades=total_trades,
        mean_win_rate=mean_win_rate,
        mean_total_pnl=mean_pnl,
        std_total_pnl=variance ** 0.5,
        n_folds_profitable=sum(1 for s in summaries if s.total_pnl > 0),
    )
