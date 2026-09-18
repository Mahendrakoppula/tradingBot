"""requires_real_data tests need data/raw/NIFTY/ONE_DAY.parquet (see
tests/test_feature_engineering.py's module docstring for why) - skipped,
not failed, in CI or anywhere that file hasn't been pulled."""
import numpy as np
import pandas as pd
import pytest

from data.storage import load_ohlcv
from models.direction_classifier import (
    EXTENDED_FEATURE_COLUMNS,
    MTF_FEATURE_COLUMNS,
    build_dataset,
    build_dataset_extended,
    build_dataset_with_mtf,
    train_and_evaluate,
    walk_forward_evaluate,
    walk_forward_evaluate_extended,
    walk_forward_evaluate_with_mtf,
)

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
NIFTY_HOURLY = load_ohlcv("NIFTY", "ONE_HOUR")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")
requires_real_hourly_data = pytest.mark.skipif(len(NIFTY_HOURLY) == 0, reason="real NIFTY hourly data not pulled locally")


def _trending_df(n: int = 60) -> pd.DataFrame:
    closes = [100.0 + i for i in range(n)]  # strictly rising - every horizon's target is unambiguously 1
    ts = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"timestamp": ts, "open": closes, "high": [c + 1 for c in closes],
                          "low": [c - 1 for c in closes], "close": closes, "volume": [0] * n})


def test_build_dataset_labels_rising_close_as_one():
    df = _trending_df()
    X, y = build_dataset(df, horizon_bars=5)
    assert len(X) == len(y)
    assert len(X) > 0
    assert set(y.unique()) == {1}  # strictly rising series - every row's future close is higher


def test_build_dataset_labels_falling_close_as_zero():
    df = _trending_df()
    df["close"] = df["close"].iloc[::-1].reset_index(drop=True)  # strictly falling instead
    df["high"] = df["close"] + 1
    df["low"] = df["close"] - 1
    X, y = build_dataset(df, horizon_bars=5)
    assert len(X) > 0
    assert set(y.unique()) == {0}


def test_build_dataset_has_no_nan_targets_or_features():
    X, y = build_dataset(_trending_df(), horizon_bars=5)
    assert not X.isna().any().any()
    assert not y.isna().any()
    assert y.isin([0, 1]).all()


@requires_real_data
def test_train_and_evaluate_runs_end_to_end_on_real_data():
    result = train_and_evaluate(NIFTY_DAILY, horizon_bars=5, test_fraction=0.2)
    assert result.n_train > 0
    assert result.n_test > 0
    assert 0.0 <= result.model_accuracy <= 1.0
    assert 0.0 <= result.baseline_accuracy <= 1.0
    assert result.baseline_class in (0, 1)
    if result.model_auc is not None:
        assert 0.0 <= result.model_auc <= 1.0


@requires_real_data
def test_walk_forward_evaluate_produces_multiple_folds_with_expanding_training_sets():
    results = walk_forward_evaluate(NIFTY_DAILY, horizon_bars=5, n_folds=5)
    assert len(results) >= 2
    for a, b in zip(results, results[1:]):
        assert b.n_train > a.n_train
    for r in results:
        assert 0.0 <= r.model_accuracy <= 1.0
        assert 0.0 <= r.baseline_accuracy <= 1.0
        assert r.n_test > 0


@requires_real_data
def test_walk_forward_evaluate_raises_when_not_enough_rows_for_the_fold_count():
    tiny_df = NIFTY_DAILY.iloc[: min(50, len(NIFTY_DAILY))]
    with pytest.raises(ValueError):
        walk_forward_evaluate(tiny_df, horizon_bars=5, n_folds=100)


@requires_real_data
def test_build_dataset_extended_has_no_nan_and_expected_columns():
    X, y = build_dataset_extended(NIFTY_DAILY, horizon_bars=5)
    assert list(X.columns) == EXTENDED_FEATURE_COLUMNS
    assert len(X) == len(y)
    assert len(X) > 0
    assert not X.isna().any().any()
    assert y.isin([0, 1]).all()


@requires_real_data
def test_walk_forward_evaluate_extended_runs_end_to_end():
    results = walk_forward_evaluate_extended(NIFTY_DAILY, horizon_bars=5, n_folds=5)
    assert len(results) >= 2
    for r in results:
        assert 0.0 <= r.model_accuracy <= 1.0
        assert 0.0 <= r.baseline_accuracy <= 1.0
        assert r.n_test > 0


@requires_real_data
@requires_real_hourly_data
def test_build_dataset_with_mtf_has_no_nan_and_expected_columns():
    X, y = build_dataset_with_mtf(NIFTY_DAILY, NIFTY_HOURLY, horizon_bars=5)
    assert list(X.columns) == MTF_FEATURE_COLUMNS
    assert len(X) == len(y)
    assert len(X) > 0
    assert not X.isna().any().any()
    assert y.isin([0, 1]).all()
    assert set(X["mtf_agree"].unique()) <= {0.0, 0.5, 1.0}


@requires_real_data
@requires_real_hourly_data
def test_walk_forward_evaluate_with_mtf_runs_end_to_end():
    # fewer folds than Experiments 004-006's 8 - real hourly data covers
    # a much shorter window, so fold sizes need to stay meaningful.
    results = walk_forward_evaluate_with_mtf(NIFTY_DAILY, NIFTY_HOURLY, horizon_bars=5, n_folds=4)
    assert len(results) >= 2
    for r in results:
        assert 0.0 <= r.model_accuracy <= 1.0
        assert 0.0 <= r.baseline_accuracy <= 1.0
        assert r.n_test > 0


def test_build_dataset_on_synthetic_random_walk_still_runs():
    rng = np.random.default_rng(0)
    n = 400
    closes = 100 + np.cumsum(rng.normal(0, 1, n))
    ts = pd.bdate_range("2026-01-01", periods=n)
    df = pd.DataFrame({"timestamp": ts, "open": closes, "high": closes + 1, "low": closes - 1,
                        "close": closes, "volume": rng.integers(1000, 2000, n)})
    X, y = build_dataset(df, horizon_bars=5)
    assert len(X) == len(y)
    assert set(y.unique()) <= {0, 1}
