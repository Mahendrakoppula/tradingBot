"""Bootstrap and Monte Carlo validation (spec Phase 13, continuing
directly from Run 002's inconclusive walk-forward result - see
backtesting/BACKTESTS.md). Operates on already-CLOSED trades from a
completed backtest; never re-runs strategy logic or generates new
signals. Answers "how much of this observed result is plausibly noise",
not "does the strategy work" - that stays the backtester/walk-forward's
job. Must never be used to manufacture apparent profitability - every
resample/reshuffle counts, none are discarded for looking unfavorable.
"""
import random
from dataclasses import dataclass

from backtesting.metrics import max_drawdown
from backtesting.trade_record import Trade


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = (pct / 100.0) * (len(sorted_values) - 1)
    lower = int(idx)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = idx - lower
    return sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac


def _closed_pnls(trades: list[Trade]) -> list[float]:
    pnls = [t.pnl for t in trades if t.pnl is not None]
    if not pnls:
        raise ValueError("No closed trades to resample")
    return pnls


@dataclass
class BootstrapResult:
    n_resamples: int
    n_trades_per_resample: int
    observed_total_pnl: float
    mean_of_resampled_total_pnl: float
    ci_low: float
    ci_high: float
    fraction_of_resamples_profitable: float


def bootstrap_total_pnl(
    trades: list[Trade],
    n_resamples: int = 10_000,
    ci_low_pct: float = 5.0,
    ci_high_pct: float = 95.0,
    rng: random.Random | None = None,
) -> BootstrapResult:
    """Resamples the trade P&Ls WITH REPLACEMENT, same count as the
    original sample, n_resamples times - the standard bootstrap. If the
    resulting [ci_low, ci_high] interval straddles zero, the observed
    total is not distinguishable from noise at that confidence level
    given this sample."""
    pnls = _closed_pnls(trades)
    n = len(pnls)
    rng = rng or random.Random()

    observed_total = sum(pnls)
    resampled_totals = [sum(pnls[rng.randrange(n)] for _ in range(n)) for _ in range(n_resamples)]
    resampled_totals.sort()

    return BootstrapResult(
        n_resamples=n_resamples,
        n_trades_per_resample=n,
        observed_total_pnl=observed_total,
        mean_of_resampled_total_pnl=sum(resampled_totals) / n_resamples,
        ci_low=_percentile(resampled_totals, ci_low_pct),
        ci_high=_percentile(resampled_totals, ci_high_pct),
        fraction_of_resamples_profitable=sum(1 for t in resampled_totals if t > 0) / n_resamples,
    )


@dataclass
class MonteCarloDrawdownResult:
    n_simulations: int
    observed_max_drawdown: float
    mean_simulated_max_drawdown: float
    worst_case_max_drawdown: float  # 95th percentile across simulated orderings
    fraction_worse_than_observed: float


def monte_carlo_trade_sequence(
    trades: list[Trade],
    n_simulations: int = 10_000,
    rng: random.Random | None = None,
) -> MonteCarloDrawdownResult:
    """Reshuffles the ORDER of the same trade outcomes (never their
    values) many times and recomputes max drawdown each time - tests
    whether the observed equity path was a lucky/unlucky ordering of a
    fixed set of outcomes, holding what actually happened fixed."""
    pnls = _closed_pnls(trades)
    rng = rng or random.Random()

    observed_dd = max_drawdown(pnls)
    shuffled = list(pnls)
    simulated_dds = []
    for _ in range(n_simulations):
        rng.shuffle(shuffled)
        simulated_dds.append(max_drawdown(shuffled))
    simulated_dds.sort()

    return MonteCarloDrawdownResult(
        n_simulations=n_simulations,
        observed_max_drawdown=observed_dd,
        mean_simulated_max_drawdown=sum(simulated_dds) / n_simulations,
        worst_case_max_drawdown=_percentile(simulated_dds, 95.0),
        fraction_worse_than_observed=sum(1 for d in simulated_dds if d > observed_dd) / n_simulations,
    )
