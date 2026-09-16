"""Parameter sensitivity / overfitting check: perturbs ONE tunable
BacktestConfig parameter at a time by a small percentage around its
baseline value and re-runs the backtest on the SAME data. A strategy whose
result collapses (flips sign, or loses most of its P&L) under a tiny nudge
was tuned to a knife-edge in this specific historical data, not a real,
robust edge - a classic overfitting signal the master-prompt spec asks for
explicitly, distinct from walk-forward's out-of-sample check.
"""
from dataclasses import dataclass, replace

from research.framework.backtest_engine import BacktestConfig, simulate
from research.framework.metrics import compute_metrics


@dataclass
class SensitivityPoint:
    parameter: str
    perturbation_pct: float
    value: float
    total_net_pnl: float
    trade_count: int


@dataclass
class SensitivityReport:
    baseline_net_pnl: float
    points: list  # list[SensitivityPoint]
    fragile_parameters: list  # parameter names where a perturbation flipped sign or collapsed the result


def run_sensitivity(
    candles: list[dict],
    underlying: str,
    asset_scope: str,
    strategy: str,
    base_config: BacktestConfig,
    *,
    perturb_params: tuple = ("min_score", "stop_atr_mult", "reward_risk_ratio", "risk_pct_per_trade"),
    perturbation_pcts: tuple = (-10.0, -5.0, 5.0, 10.0),
    collapse_threshold_pct: float = 50.0,
) -> SensitivityReport:
    baseline_result = simulate(candles, underlying, asset_scope, strategy, base_config)
    baseline_net_pnl = compute_metrics(baseline_result.trades, base_config.starting_capital).total_net_pnl

    points: list[SensitivityPoint] = []
    fragile: set = set()
    for param in perturb_params:
        base_value = getattr(base_config, param)
        for pct in perturbation_pcts:
            new_value = base_value * (1 + pct / 100.0)
            new_config = replace(base_config, **{param: new_value})
            result = simulate(candles, underlying, asset_scope, strategy, new_config)
            m = compute_metrics(result.trades, base_config.starting_capital)
            points.append(SensitivityPoint(param, pct, new_value, m.total_net_pnl, m.trade_count))

            if baseline_net_pnl == 0:
                continue
            sign_flip = (baseline_net_pnl > 0) != (m.total_net_pnl > 0)
            retained_fraction = m.total_net_pnl / baseline_net_pnl if baseline_net_pnl > 0 else None
            magnitude_collapse = retained_fraction is not None and retained_fraction < (1 - collapse_threshold_pct / 100.0)
            if sign_flip or magnitude_collapse:
                fragile.add(param)

    return SensitivityReport(baseline_net_pnl=baseline_net_pnl, points=points, fragile_parameters=sorted(fragile))
