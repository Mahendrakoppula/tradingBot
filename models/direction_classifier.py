"""Model 2 of 8 (spec: "direction-probability"). Predicts whether the
close price will be HIGHER (1) or LOWER (0) `horizon_bars` bars into the
FUTURE from features available up to and including the current bar - a
genuine forecasting task, exactly the "fundamentally different target"
Model 1's own experiment log (models/EXPERIMENTS.md's Experiment 001/003)
already flagged as the natural next step once the regime-classification
target itself was thoroughly explored and found to have no signal at
any horizon tried.

Reuses models/regime_classifier.py's proven scaffolding directly
(models/feature_engineering.py's causal features, time_ordered_split's
purged chronological split, the expanding-window walk-forward shape) -
only the TARGET and the BASELINE differ, so none of that plumbing is
re-derived or duplicated here.

BASELINE CHOICE, different from Model 1's: regime has real persistence
(today's regime is a genuinely informative guess at tomorrow's), so
Model 1 used a persistence baseline. Price DIRECTION over a short
horizon is much closer to a random walk - assuming "yesterday's
direction continues" is not obviously a strong prior, and majority-class
is the standard, textbook-honest naive baseline for a binary target: it
already gives credit for any real class imbalance in the sample (e.g. a
bull-trending window has genuinely more UP days than DOWN days) without
assuming anything about actual predictability. A real model must beat
this, not a coin flip and not an unexamined assumption about trend
continuation.

Also reports ROC-AUC alongside accuracy (Model 1 didn't need this,
since it optimizes/reports a single hard class): Model 2 is explicitly
a PROBABILITY model, and accuracy alone can hide genuine
ranking/calibration skill in the predicted probabilities that a
threshold-0.5 hard-class comparison would miss. Promotion decisions
still key off accuracy vs. baseline (research/promotion_gate.py, kept
consistent with Model 1), with AUC reported as supplementary honesty,
not a second promotion criterion.
"""
from dataclasses import dataclass

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score

from models.feature_engineering import build_features, build_mtf_features, build_options_features
from models.regime_classifier import TrainTestSplit, time_ordered_split

FEATURE_COLUMNS = ["atr", "atr_percentile", "roc", "roc_magnitude_percentile", "is_strong_momentum"]
EXTENDED_FEATURE_COLUMNS = FEATURE_COLUMNS + ["realized_vol", "theoretical_gamma", "theoretical_vega"]
MTF_FEATURE_COLUMNS = FEATURE_COLUMNS + ["mtf_both_directional", "mtf_agree"]


def build_dataset(df: pd.DataFrame, horizon_bars: int, **feature_kwargs) -> tuple[pd.DataFrame, pd.Series]:
    """Returns (X, y) where y.loc[t] is 1 if close rises from t to
    t+horizon_bars, else 0, and X.loc[t] is built only from data up to
    and including t (same causal features as Model 1). Rows without a
    valid label (past the end of the series) or without enough warmup
    history for the features (near the head) are dropped."""
    features = build_features(df, **feature_kwargs)
    future_close = df["close"].shift(-horizon_bars)
    target = (future_close > df["close"]).astype(float)
    target[future_close.isna()] = float("nan")  # no peeking past the end of the series - an unknown future is not a 0

    combined = features.copy()
    combined["target"] = target.reindex(features.index)
    combined = combined.dropna()

    return combined[FEATURE_COLUMNS], combined["target"].astype(int)


def build_dataset_extended(
    df: pd.DataFrame,
    horizon_bars: int,
    strike_increment: float = 50.0,
    days_to_expiry: int = 7,
    risk_free_rate: float = 0.07,
    vol_window: int = 20,
    **feature_kwargs,
) -> tuple[pd.DataFrame, pd.Series]:
    """Same target/label logic as build_dataset(), with
    models/feature_engineering.py's options-derived features
    (realized_vol, theoretical_gamma, theoretical_vega) added on -
    built to test whether Experiments 004/005's "no signal" conclusion
    (models/EXPERIMENTS.md) reflects an absence of any learnable
    structure, or just a limitation of the ATR/ROC-only feature set, per
    that experiment's own stated next step. See
    build_options_features()'s docstring for why gamma/vega specifically
    (not delta/theta) were chosen as option_type-symmetric, leak-safe
    additions."""
    base_features = build_features(df, **feature_kwargs)
    options_features = build_options_features(df, strike_increment, days_to_expiry, risk_free_rate, vol_window)
    features = pd.concat([base_features, options_features], axis=1)

    future_close = df["close"].shift(-horizon_bars)
    target = (future_close > df["close"]).astype(float)
    target[future_close.isna()] = float("nan")

    combined = features.copy()
    combined["target"] = target.reindex(features.index)
    combined = combined.dropna()

    return combined[EXTENDED_FEATURE_COLUMNS], combined["target"].astype(int)


def build_dataset_with_mtf(
    df: pd.DataFrame,
    hourly_df: pd.DataFrame,
    horizon_bars: int,
    **feature_kwargs,
) -> tuple[pd.DataFrame, pd.Series]:
    """Same target/label logic as build_dataset(), with
    models/feature_engineering.py's multi-timeframe alignment features
    (mtf_both_directional, mtf_agree) added on - the OTHER "materially
    different feature set" Experiment 006 identified but deferred as
    harder/more leakage-risk-prone than theoretical Greeks (which was
    tried first). Real hourly data only covers a recent window (see
    backtesting/BACKTESTS.md's Run 011/012) - rows before it exists are
    dropped by the same dropna() every other feature's warmup already
    goes through, so this dataset is meaningfully SHORTER than
    build_dataset()/build_dataset_extended()'s full-history ones, a
    real, disclosed limitation stated once here rather than at every
    call site."""
    base_features = build_features(df, **feature_kwargs)
    mtf_features = build_mtf_features(df, hourly_df)
    features = pd.concat([base_features, mtf_features], axis=1)

    future_close = df["close"].shift(-horizon_bars)
    target = (future_close > df["close"]).astype(float)
    target[future_close.isna()] = float("nan")

    combined = features.copy()
    combined["target"] = target.reindex(features.index)
    combined = combined.dropna()

    return combined[MTF_FEATURE_COLUMNS], combined["target"].astype(int)


@dataclass
class EvaluationResult:
    model_accuracy: float
    baseline_accuracy: float
    model_auc: float | None  # None if train or test has only one class present - AUC is undefined then
    n_train: int
    n_test: int
    baseline_class: int  # the majority class in TRAIN, used as every test-row prediction


def _make_model() -> RandomForestClassifier:
    return RandomForestClassifier(n_estimators=200, max_depth=6, min_samples_leaf=20, random_state=42, class_weight="balanced")


def _fit_and_evaluate(X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame, y_test: pd.Series) -> EvaluationResult:
    model = _make_model()
    model.fit(X_train, y_train)
    predictions = model.predict(X_test)
    probabilities = model.predict_proba(X_test)[:, list(model.classes_).index(1)] if 1 in model.classes_ else None
    model_accuracy = accuracy_score(y_test, predictions)

    baseline_class = int(y_train.mode().iloc[0])  # majority class in TRAIN only - never peek at test
    baseline_predictions = pd.Series(baseline_class, index=y_test.index)
    baseline_accuracy = accuracy_score(y_test, baseline_predictions)

    model_auc = None
    if probabilities is not None and y_test.nunique() > 1:
        model_auc = roc_auc_score(y_test, probabilities)

    return EvaluationResult(
        model_accuracy=model_accuracy, baseline_accuracy=baseline_accuracy, model_auc=model_auc,
        n_train=len(X_train), n_test=len(X_test), baseline_class=baseline_class,
    )


def train_and_evaluate(
    df: pd.DataFrame,
    horizon_bars: int = 5,
    test_fraction: float = 0.2,
    purge_bars: int | None = None,
) -> EvaluationResult:
    purge_bars = horizon_bars if purge_bars is None else purge_bars
    X, y = build_dataset(df, horizon_bars)
    split: TrainTestSplit = time_ordered_split(X, y, test_fraction, purge_bars)
    return _fit_and_evaluate(split.X_train, split.y_train, split.X_test, split.y_test)


MIN_FOLD_SIZE = 10  # below this, "accuracy" is a handful of coin flips, not a meaningful measurement


def walk_forward_evaluate(
    df: pd.DataFrame,
    horizon_bars: int = 5,
    n_folds: int = 5,
    purge_bars: int | None = None,
    min_fold_size: int = MIN_FOLD_SIZE,
) -> list[EvaluationResult]:
    """Expanding-window walk-forward, identical shape to
    models/regime_classifier.py's own (see that module's docstring for
    why expanding-window suits a model that's genuinely retrained per
    fold, unlike backtesting/walk_forward.py's independent folds for a
    rule-based strategy). Applied from Model 2's very first experiment,
    not added as a follow-up the way Model 1 needed - a lesson already
    learned from Model 1's own experiment history (Experiment 001's
    single split was immediately followed by Experiment 002's
    walk-forward once a single split was shown to not be enough
    rigor)."""
    purge_bars = horizon_bars if purge_bars is None else purge_bars
    X, y = build_dataset(df, horizon_bars)
    n = len(X)
    fold_size = n // (n_folds + 1)
    if fold_size <= 0:
        raise ValueError(f"Not enough rows ({n}) for {n_folds} folds")

    results = []
    for fold in range(1, n_folds + 1):
        train_end = fold * fold_size - purge_bars
        test_start = fold * fold_size
        test_end = min((fold + 1) * fold_size, n)
        if train_end < min_fold_size or test_end - test_start < min_fold_size:
            continue
        X_train, y_train = X.iloc[:train_end], y.iloc[:train_end]
        X_test, y_test = X.iloc[test_start:test_end], y.iloc[test_start:test_end]
        results.append(_fit_and_evaluate(X_train, y_train, X_test, y_test))

    if not results:
        raise ValueError(
            f"No usable folds: {n} rows is not enough for {n_folds} folds "
            f"with purge_bars={purge_bars} and min_fold_size={min_fold_size}"
        )
    return results


def walk_forward_evaluate_extended(
    df: pd.DataFrame,
    horizon_bars: int = 5,
    n_folds: int = 5,
    purge_bars: int | None = None,
    min_fold_size: int = MIN_FOLD_SIZE,
    strike_increment: float = 50.0,
    days_to_expiry: int = 7,
    risk_free_rate: float = 0.07,
    vol_window: int = 20,
) -> list[EvaluationResult]:
    """Identical fold-splitting logic to walk_forward_evaluate(), built
    on build_dataset_extended()'s options-augmented feature set instead
    of build_dataset()'s ATR/ROC-only one - see that function's
    docstring for why."""
    purge_bars = horizon_bars if purge_bars is None else purge_bars
    X, y = build_dataset_extended(df, horizon_bars, strike_increment, days_to_expiry, risk_free_rate, vol_window)
    n = len(X)
    fold_size = n // (n_folds + 1)
    if fold_size <= 0:
        raise ValueError(f"Not enough rows ({n}) for {n_folds} folds")

    results = []
    for fold in range(1, n_folds + 1):
        train_end = fold * fold_size - purge_bars
        test_start = fold * fold_size
        test_end = min((fold + 1) * fold_size, n)
        if train_end < min_fold_size or test_end - test_start < min_fold_size:
            continue
        X_train, y_train = X.iloc[:train_end], y.iloc[:train_end]
        X_test, y_test = X.iloc[test_start:test_end], y.iloc[test_start:test_end]
        results.append(_fit_and_evaluate(X_train, y_train, X_test, y_test))

    if not results:
        raise ValueError(
            f"No usable folds: {n} rows is not enough for {n_folds} folds "
            f"with purge_bars={purge_bars} and min_fold_size={min_fold_size}"
        )
    return results


def walk_forward_evaluate_with_mtf(
    df: pd.DataFrame,
    hourly_df: pd.DataFrame,
    horizon_bars: int = 5,
    n_folds: int = 5,
    purge_bars: int | None = None,
    min_fold_size: int = MIN_FOLD_SIZE,
) -> list[EvaluationResult]:
    """Identical fold-splitting logic to walk_forward_evaluate(), built
    on build_dataset_with_mtf()'s MTF-augmented feature set instead of
    build_dataset()'s ATR/ROC-only one. `min_fold_size` matters more
    here than for the other variants - the dataset is already much
    shorter (real hourly data covers a recent window only), so `n_folds`
    may need to be smaller than the 8 used for Experiments 004-006."""
    purge_bars = horizon_bars if purge_bars is None else purge_bars
    X, y = build_dataset_with_mtf(df, hourly_df, horizon_bars)
    n = len(X)
    fold_size = n // (n_folds + 1)
    if fold_size <= 0:
        raise ValueError(f"Not enough rows ({n}) for {n_folds} folds")

    results = []
    for fold in range(1, n_folds + 1):
        train_end = fold * fold_size - purge_bars
        test_start = fold * fold_size
        test_end = min((fold + 1) * fold_size, n)
        if train_end < min_fold_size or test_end - test_start < min_fold_size:
            continue
        X_train, y_train = X.iloc[:train_end], y.iloc[:train_end]
        X_test, y_test = X.iloc[test_start:test_end], y.iloc[test_start:test_end]
        results.append(_fit_and_evaluate(X_train, y_train, X_test, y_test))

    if not results:
        raise ValueError(
            f"No usable folds: {n} rows is not enough for {n_folds} folds "
            f"with purge_bars={purge_bars} and min_fold_size={min_fold_size}"
        )
    return results
