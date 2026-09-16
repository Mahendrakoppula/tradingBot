"""Walk-forward validation: the actual gate the master-prompt spec asks
for before treating any backtest.simulate() number as more than a
first-cut guess. Rolls chronologically ordered TRAIN -> VALIDATE -> TEST
folds forward through history; for each fold, tries every combination in a
small parameter grid on the TRAIN+VALIDATE window, picks whichever
combination did best on the VALIDATE portion, then reports how that PICKED
combination performs on the TEST portion - data it was never tuned
against. A strategy that only looks good on the exact window it was tuned
on is, by definition, tuned on that window; this is the test of whether it
generalizes.

Reuses research.framework.backtest_engine.simulate's own `min_history_bars`
as the train/validate/test cutoff mechanism rather than adding new engine
code: simulate() only ever makes DECISIONS from min_history_bars onward,
treating everything before that purely as indicator warmup - so slicing
candles as [train_start:validate_end] with min_history_bars=train_size
makes it evaluate ONLY the validate portion (using the preceding train
segment as warmup), and [train_start:test_end] with
min_history_bars=train_size+validate_size makes it evaluate ONLY the test
portion the same way.
"""
from dataclasses import dataclass, replace
from itertools import product

from research.framework.backtest_engine import BacktestConfig, simulate
from research.framework.metrics import Metrics, compute_metrics


@dataclass
class ParameterGrid:
    min_score: tuple[float, ...] = (55.0, 60.0, 65.0, 70.0)
    stop_atr_mult: tuple[float, ...] = (1.0, 1.5, 2.0)
    reward_risk_ratio: tuple[float, ...] = (1.5, 2.0, 3.0)

    def combinations(self) -> list[dict]:
        keys = ("min_score", "stop_atr_mult", "reward_risk_ratio")
        values = (self.min_score, self.stop_atr_mult, self.reward_risk_ratio)
        return [dict(zip(keys, combo)) for combo in product(*values)]


@dataclass
class Fold:
    train_start: int
    train_end: int  # exclusive; also the validate window's min_history_bars, relative to train_start
    validate_end: int  # exclusive
    test_end: int  # exclusive


def make_folds(n_candles: int, train_size: int, validate_size: int, test_size: int, step: int = None) -> list[Fold]:
    """Non-overlapping, chronologically ordered folds - each slides forward
    by `step` candles (defaults to test_size, so every test window is used
    exactly once, back to back)."""
    step = step or test_size
    if train_size <= 0 or validate_size <= 0 or test_size <= 0:
        raise ValueError("train_size, validate_size, and test_size must all be positive")
    folds = []
    start = 0
    while start + train_size + validate_size + test_size <= n_candles:
        folds.append(Fold(
            train_start=start,
            train_end=start + train_size,
            validate_end=start + train_size + validate_size,
            test_end=start + train_size + validate_size + test_size,
        ))
        start += step
    return folds


def _selection_score(m: Metrics) -> float:
    """total_net_pnl, not Sharpe/profit-factor - both can be undefined
    (None/inf) on the small trade counts a single validate window
    typically produces; total_net_pnl is always defined and is the
    quantity a real account ultimately cares about."""
    return m.total_net_pnl


@dataclass
class FoldResult:
    fold: Fold
    chosen_params: dict
    validate_metrics: Metrics
    test_metrics: Metrics
    test_trades: list  # list[ClosedTrade] - the genuinely out-of-sample trades


@dataclass
class WalkForwardResult:
    folds: list  # list[FoldResult]

    def aggregate_test_metrics(self, starting_capital: float) -> Metrics:
        """Metrics over ALL folds' out-of-sample trades pooled together -
        the single most trustworthy number this module produces, since
        none of these trades ever informed the parameter choice that
        produced them."""
        all_test_trades = [t for fr in self.folds for t in fr.test_trades]
        return compute_metrics(all_test_trades, starting_capital)


def run_walk_forward(
    candles: list[dict], underlying: str, asset_scope: str, strategy: str, base_config: BacktestConfig,
    grid: ParameterGrid, train_size: int, validate_size: int, test_size: int, step: int = None,
) -> WalkForwardResult:
    folds = make_folds(len(candles), train_size, validate_size, test_size, step)
    fold_results: list[FoldResult] = []

    for fold in folds:
        validate_candles = candles[fold.train_start : fold.validate_end]
        best_params, best_metrics, best_score = None, None, None
        for params in grid.combinations():
            validate_config = replace(base_config, min_history_bars=train_size, **params)
            result = simulate(validate_candles, underlying, asset_scope, strategy, validate_config)
            m = compute_metrics(result.trades, base_config.starting_capital)
            s = _selection_score(m)
            if best_score is None or s > best_score:
                best_params, best_metrics, best_score = params, m, s

        test_candles = candles[fold.train_start : fold.test_end]
        test_config = replace(base_config, min_history_bars=train_size + validate_size, **best_params)
        test_result = simulate(test_candles, underlying, asset_scope, strategy, test_config)
        test_metrics = compute_metrics(test_result.trades, base_config.starting_capital)

        fold_results.append(FoldResult(
            fold=fold, chosen_params=best_params, validate_metrics=best_metrics,
            test_metrics=test_metrics, test_trades=test_result.trades,
        ))

    return WalkForwardResult(folds=fold_results)
