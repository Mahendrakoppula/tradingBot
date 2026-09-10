"""Monte Carlo simulation over an ALREADY-REALIZED trade sequence (from
research.framework.backtest_engine or research.framework.walk_forward,
ideally the latter's genuinely out-of-sample trades) - never a fresh price
simulation. Resamples the sequence of per-trade P&Ls many times (with
replacement, by default) to build a distribution of possible equity
curves/drawdowns/final outcomes.

This answers "how much did the specific ORDER these trades happened to
occur in matter" - a strategy whose backtest looks fine only because its
worst losses happened to land late (when there was a large cushion) rather
than early (when there wasn't) is fragile in a way the single realized
equity curve alone won't show.
"""
import random
from dataclasses import dataclass


@dataclass
class MonteCarloResult:
    n_simulations: int
    final_pnl_distribution: list[float]
    max_drawdown_pct_distribution: list[float]
    probability_of_ruin: float  # fraction of paths whose drawdown reached ruin_threshold_pct
    median_final_pnl: float
    worst_case_drawdown_pct: float  # 95th percentile of the drawdown distribution


def _path_stats(pnls: list[float], starting_capital: float) -> tuple[float, float]:
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    max_dd_pct = (max_dd / starting_capital * 100.0) if starting_capital else 0.0
    return cum, max_dd_pct


def run_monte_carlo(
    trades: list,
    starting_capital: float,
    *,
    n_simulations: int = 2000,
    ruin_threshold_pct: float = 50.0,
    resample_with_replacement: bool = True,
    seed: int | None = None,
) -> MonteCarloResult:
    if not trades:
        return MonteCarloResult(0, [], [], 0.0, 0.0, 0.0)

    rng = random.Random(seed)
    pnls = [t.net_pnl for t in trades]
    n = len(pnls)

    final_pnls: list[float] = []
    max_dds: list[float] = []
    ruin_count = 0
    for _ in range(n_simulations):
        if resample_with_replacement:
            path = [rng.choice(pnls) for _ in range(n)]
        else:
            path = pnls[:]
            rng.shuffle(path)
        final_pnl, max_dd_pct = _path_stats(path, starting_capital)
        final_pnls.append(final_pnl)
        max_dds.append(max_dd_pct)
        if max_dd_pct >= ruin_threshold_pct:
            ruin_count += 1

    sorted_final = sorted(final_pnls)
    sorted_dd = sorted(max_dds)
    median_final_pnl = sorted_final[n_simulations // 2]
    worst_case_drawdown_pct = sorted_dd[int(0.95 * (n_simulations - 1))]

    return MonteCarloResult(
        n_simulations=n_simulations,
        final_pnl_distribution=final_pnls,
        max_drawdown_pct_distribution=max_dds,
        probability_of_ruin=ruin_count / n_simulations,
        median_final_pnl=median_final_pnl,
        worst_case_drawdown_pct=worst_case_drawdown_pct,
    )
