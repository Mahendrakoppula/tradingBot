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


def train_and_evaluate(
    df: pd.DataFrame,
    horizon_bars: int = 5,
    test_fraction: float = 0.2,
    purge_bars: int | None = None,
) -> EvaluationResult:
    purge_bars = horizon_bars if purge_bars is None else purge_bars
    X, y = build_dataset(df, horizon_bars)
    split = time_ordered_split(X, y, test_fraction, purge_bars)

    model = RandomForestClassifier(
        n_estimators=200, max_depth=6, min_samples_leaf=20, random_state=42, class_weight="balanced",
    )
    model.fit(split.X_train, split.y_train)
    predictions = model.predict(split.X_test)
    model_accuracy = accuracy_score(split.y_test, predictions)

    # Persistence baseline: "the regime won't change" - the naive
    # forecast every real model here must beat to be worth anything.
    current_regime = batch_regime_labels(df).reindex(split.X_test.index)
    baseline_accuracy = accuracy_score(split.y_test, current_regime)

    report = classification_report(split.y_test, predictions, zero_division=0)
    return EvaluationResult(
        model_accuracy=model_accuracy, baseline_accuracy=baseline_accuracy,
        n_train=len(split.X_train), n_test=len(split.X_test), report=report,
    )
