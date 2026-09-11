"""Model 1 of 8 (spec: "regime classifier"). Predicts the market-state
regime `horizon_bars` bars into the FUTURE from features available up to
and including the current bar - a genuine forecasting task, not a
redundant real-time duplicate of market_state.classifier's own same-bar
output.

Scope of this first pass, deliberately limited: proves the training
pipeline works honestly end-to-end (causal feature engineering ->
time-ordered split with a purge gap -> a tabular model, per the spec's
own "tabular first" preference -> evaluation against a naive baseline,
with sample sizes always reported). This is NOT validated against
walk-forward/out-of-sample robustness yet (spec Phases 12-13, a separate
later pass) and must never be read as a claim of a production-ready
trading signal - see the spec's own "robustness over complexity, no
fabricated profitability" closing philosophy.
"""
from dataclasses import dataclass

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report

from models.feature_engineering import batch_regime_labels, build_features

FEATURE_COLUMNS = ["atr", "atr_percentile", "roc", "roc_magnitude_percentile", "is_strong_momentum"]


def build_dataset(df: pd.DataFrame, horizon_bars: int, **feature_kwargs) -> tuple[pd.DataFrame, pd.Series]:
    """Returns (X, y) where y.loc[t] is the regime at t+horizon_bars and
    X.loc[t] is built only from data up to and including t. Rows without
    a valid label (near the tail, past the end of the series) or without
    enough warmup history for the features (near the head) are dropped,
    as are rows whose future regime is UNKNOWN (insufficient data at
    that future point) - there's nothing learnable about predicting
    "we don't know"."""
    features = build_features(df, **feature_kwargs)
    labels = batch_regime_labels(df)
    target = labels.shift(-horizon_bars)

    combined = features.copy()
    combined["target"] = target
    combined = combined.dropna()
    combined = combined[combined["target"] != "UNKNOWN"]

    return combined[FEATURE_COLUMNS], combined["target"]


@dataclass
class TrainTestSplit:
    X_train: pd.DataFrame
    y_train: pd.Series
    X_test: pd.DataFrame
    y_test: pd.Series


def time_ordered_split(X: pd.DataFrame, y: pd.Series, test_fraction: float = 0.2, purge_bars: int = 0) -> TrainTestSplit:
    """Chronological split - never random shuffling for time series data,
    per the spec's purged-CV requirement. `purge_bars` drops a gap
    between the end of train and the start of test, reducing leakage
    from any rolling-window overlap straddling the boundary."""
    n = len(X)
    test_size = int(n * test_fraction)
    train_end = n - test_size - purge_bars
    if train_end <= 0 or test_size <= 0:
        raise ValueError(f"Not enough rows ({n}) for test_fraction={test_fraction} and purge_bars={purge_bars}")
    return TrainTestSplit(
        X_train=X.iloc[:train_end], y_train=y.iloc[:train_end],
        X_test=X.iloc[n - test_size:], y_test=y.iloc[n - test_size:],
    )


@dataclass
class EvaluationResult:
    model_accuracy: float
    baseline_accuracy: float
    n_train: int
    n_test: int
    report: str


def _make_model() -> RandomForestClassifier:
    return RandomForestClassifier(n_estimators=200, max_depth=6, min_samples_leaf=20, random_state=42, class_weight="balanced")


def _fit_and_evaluate(df: pd.DataFrame, X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame, y_test: pd.Series) -> EvaluationResult:
    model = _make_model()
    model.fit(X_train, y_train)
    predictions = model.predict(X_test)
    model_accuracy = accuracy_score(y_test, predictions)

    # Persistence baseline: "the regime won't change" - the naive
    # forecast every real model here must beat to be worth anything.
    current_regime = batch_regime_labels(df).reindex(X_test.index)
    baseline_accuracy = accuracy_score(y_test, current_regime)

    report = classification_report(y_test, predictions, zero_division=0)
    return EvaluationResult(
        model_accuracy=model_accuracy, baseline_accuracy=baseline_accuracy,
        n_train=len(X_train), n_test=len(X_test), report=report,
    )


def train_and_evaluate(
    df: pd.DataFrame,
    horizon_bars: int = 5,
    test_fraction: float = 0.2,
    purge_bars: int | None = None,
) -> EvaluationResult:
    purge_bars = horizon_bars if purge_bars is None else purge_bars
    X, y = build_dataset(df, horizon_bars)
    split = time_ordered_split(X, y, test_fraction, purge_bars)
    return _fit_and_evaluate(df, split.X_train, split.y_train, split.X_test, split.y_test)


MIN_FOLD_SIZE = 10  # below this, a fold's "accuracy" is a handful of coin flips, not a meaningful measurement


def walk_forward_evaluate(
    df: pd.DataFrame,
    horizon_bars: int = 5,
    n_folds: int = 5,
    purge_bars: int | None = None,
    min_fold_size: int = MIN_FOLD_SIZE,
) -> list[EvaluationResult]:
    """Expanding-window walk-forward: fold i trains on ALL data up to
    (minus a purge gap) the start of fold i's own held-out test window,
    then tests on that window - unlike backtesting/walk_forward.py's
    independent, non-overlapping folds (which suit strategies that
    aren't fit to data at all), a model genuinely needs to be retrained
    per fold, and an expanding window matches how a real periodic-
    retrain system would behave (train on everything available so far).

    Returns one EvaluationResult per fold with at least `min_fold_size`
    rows in both train and test - smaller folds are silently skipped
    rather than reported, since e.g. "100% accuracy" from one lucky
    guess on a 1-row fold is worse than no result at all."""
    purge_bars = horizon_bars if purge_bars is None else purge_bars
    X, y = build_dataset(df, horizon_bars)
    n = len(X)
    fold_size = n // (n_folds + 1)  # first slice is reserved purely for initial training
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
        results.append(_fit_and_evaluate(df, X_train, y_train, X_test, y_test))

    if not results:
        raise ValueError(
            f"No usable folds: {n} rows is not enough for {n_folds} folds "
            f"with purge_bars={purge_bars} and min_fold_size={min_fold_size}"
        )
    return results
