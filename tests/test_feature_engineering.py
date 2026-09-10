"""The two tests marked requires_real_data need data/raw/NIFTY/ONE_DAY.parquet,
which is gitignored and only exists on a machine that has actually run
data/pull_history.py against live credentials - CI has no such data and
these two are skipped there, not failed. Every other test here uses
synthetic fixtures and always runs."""
import numpy as np
import pandas as pd
import pytest

from data.storage import load_ohlcv
from market_state.classifier import classify_market_state
from models.feature_engineering import batch_regime_labels, build_features

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")


@requires_real_data
def test_batch_regime_labels_matches_live_classifier_at_sampled_rows():
    """The whole-series batch computation must be EXACTLY equivalent to
    calling classify_market_state on the truncated history at each row -
    not an approximation. Sampled rather than exhaustive (exhaustive
    would itself be the quadratic thing this module exists to avoid)."""
    batch = batch_regime_labels(NIFTY_DAILY)
    n = len(NIFTY_DAILY)
    sample_rows = sorted(set([5, 20, 50, 100, 150, 200, 300, 500, 800, n - 1]))

    for t in sample_rows:
        expected = classify_market_state(NIFTY_DAILY.iloc[: t + 1]).regime
        assert batch.iloc[t] == expected, f"mismatch at row {t}: batch={batch.iloc[t]!r} live={expected!r}"


@requires_real_data
def test_build_features_is_causal_not_leaking_future_bars():
    features = build_features(NIFTY_DAILY)
    as_of = 500

    mutated = NIFTY_DAILY.copy()
    mutated.loc[mutated.index[as_of + 1]:, ["open", "high", "low", "close", "volume"]] = 1.0
    mutated_features = build_features(mutated)

    pd.testing.assert_frame_equal(
        features.iloc[: as_of + 1].reset_index(drop=True),
        mutated_features.iloc[: as_of + 1].reset_index(drop=True),
    )


def test_batch_regime_labels_on_synthetic_short_series_is_all_unknown():
    ts = pd.bdate_range("2026-01-01", periods=5)
    df = pd.DataFrame({"timestamp": ts, "open": [100] * 5, "high": [101] * 5, "low": [99] * 5,
                        "close": [100] * 5, "volume": [1000] * 5})
    labels = batch_regime_labels(df)
    assert (labels == "UNKNOWN").all()


def test_build_features_returns_expected_columns():
    rng = np.random.default_rng(0)
    n = 150
    closes = 100 + np.cumsum(rng.normal(0, 1, n))
    ts = pd.bdate_range("2026-01-01", periods=n)
    df = pd.DataFrame({"timestamp": ts, "open": closes, "high": closes + 1, "low": closes - 1,
                        "close": closes, "volume": rng.integers(1000, 2000, n)})
    features = build_features(df)
    assert list(features.columns) == ["atr", "atr_percentile", "roc", "roc_magnitude_percentile", "is_strong_momentum"]
    assert len(features) == n
