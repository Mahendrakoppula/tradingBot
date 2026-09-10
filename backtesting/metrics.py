"""Basic performance metrics - sample sizes always reported alongside
any rate/average, per the spec's own requirement. Statistical
significance (confidence intervals, bootstrap, Monte Carlo) is Phase 13,
a separate later pass - these are plain descriptive summaries only, not
a claim of validated skill.
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


def summarize(trades: list[Trade]) -> PerformanceSummary:
    closed = [t for t in trades if t.pnl is not None]
    n_trades = len(closed)
    if n_trades == 0:
        return PerformanceSummary(0, 0, 0, None, 0.0, None, 0.0)

    n_wins = sum(1 for t in closed if t.pnl > 0)
    n_losses = sum(1 for t in closed if t.pnl <= 0)
    total_pnl = sum(t.pnl for t in closed)

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for t in closed:
        cumulative += t.pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    return PerformanceSummary(
        n_trades=n_trades, n_wins=n_wins, n_losses=n_losses,
        win_rate=n_wins / n_trades, total_pnl=total_pnl,
        avg_pnl_per_trade=total_pnl / n_trades, max_drawdown=max_drawdown,
    )
