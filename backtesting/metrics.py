"""Basic performance metrics - sample sizes always reported alongside
any rate/average, per the spec's own requirement. These are plain
descriptive summaries of ONE fixed sequence of trades - statistical
significance (is this distinguishable from noise?) is
backtesting/monte_carlo.py's job, not this module's.
"""
from dataclasses import dataclass

from backtesting.trade_record import Trade


@dataclass
class PerformanceSummary:
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float | None
    total_pnl: float
    avg_pnl_per_trade: float | None
    max_drawdown: float


def max_drawdown(pnls: list[float]) -> float:
    """Largest peak-to-trough decline in the CUMULATIVE sum of `pnls`,
    taken in the given order - reused by backtesting/monte_carlo.py to
    recompute this same statistic under many reshuffled orderings
    without duplicating the logic."""
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        worst = max(worst, peak - cumulative)
    return worst


def summarize(trades: list[Trade]) -> PerformanceSummary:
    closed = [t for t in trades if t.pnl is not None]
    n_trades = len(closed)
    if n_trades == 0:
        return PerformanceSummary(0, 0, 0, None, 0.0, None, 0.0)

    n_wins = sum(1 for t in closed if t.pnl > 0)
    n_losses = sum(1 for t in closed if t.pnl <= 0)
    total_pnl = sum(t.pnl for t in closed)

    return PerformanceSummary(
        n_trades=n_trades, n_wins=n_wins, n_losses=n_losses,
        win_rate=n_wins / n_trades, total_pnl=total_pnl,
        avg_pnl_per_trade=total_pnl / n_trades, max_drawdown=max_drawdown([t.pnl for t in closed]),
    )
