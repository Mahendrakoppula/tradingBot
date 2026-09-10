"""requires_real_data tests need data/raw/NIFTY/ONE_DAY.parquet (see
tests/test_feature_engineering.py's module docstring for why) - skipped,
not failed, in CI or anywhere that file hasn't been pulled."""
import numpy as np
import pandas as pd
import pytest

from data.storage import load_ohlcv
from models.regime_classifier import build_dataset, time_ordered_split, train_and_evaluate

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")


@requires_real_data
def test_build_dataset_has_no_unknown_targets_and_aligned_lengths():
    X, y = build_dataset(NIFTY_DAILY, horizon_bars=5)
    assert len(X) == len(y)
    assert len(X) > 0
    assert "UNKNOWN" not in set(y)
    assert not X.isna().any().any()


@requires_real_data
def test_time_ordered_split_is_chronological_with_a_purge_gap():
    X, y = build_dataset(NIFTY_DAILY, horizon_bars=5)
    split = time_ordered_split(X, y, test_fraction=0.2, purge_bars=5)
    assert split.X_train.index.max() < split.X_test.index.min()
    gap = split.X_test.index.min() - split.X_train.index.max()
    assert gap >= 5


@requires_real_data
def test_split_raises_when_too_few_rows_for_the_split():
    X, y = build_dataset(NIFTY_DAILY, horizon_bars=5)
    tiny_X, tiny_y = X.iloc[:5], y.iloc[:5]
    with pytest.raises(ValueError):
        time_ordered_split(tiny_X, tiny_y, test_fraction=0.2, purge_bars=5)


@requires_real_data
def test_train_and_evaluate_runs_end_to_end_on_real_data():
    result = train_and_evaluate(NIFTY_DAILY, horizon_bars=5, test_fraction=0.2)
    assert result.n_train > 0
    assert result.n_test > 0
    assert 0.0 <= result.model_accuracy <= 1.0
    assert 0.0 <= result.baseline_accuracy <= 1.0
    assert isinstance(result.report, str) and len(result.report) > 0


def test_build_dataset_on_synthetic_data_with_no_real_structure_still_runs():
    rng = np.random.default_rng(0)
    n = 400
    closes = 100 + np.cumsum(rng.normal(0, 1, n))
    ts = pd.bdate_range("2026-01-01", periods=n)
    df = pd.DataFrame({"timestamp": ts, "open": closes, "high": closes + 1, "low": closes - 1,
                        "close": closes, "volume": rng.integers(1000, 2000, n)})
    X, y = build_dataset(df, horizon_bars=5)
    assert len(X) == len(y)
