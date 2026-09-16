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
from models.feature_engineering import batch_regime_labels, build_features, build_options_features

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


def _synthetic_df(n: int = 60, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 1, n))
    ts = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"timestamp": ts, "open": closes, "high": closes + 1, "low": closes - 1,
                          "close": closes, "volume": rng.integers(1000, 2000, n)})


def test_build_options_features_returns_expected_columns_and_length():
    df = _synthetic_df(n=60)
    features = build_options_features(df, vol_window=20)
    assert list(features.columns) == ["realized_vol", "theoretical_gamma", "theoretical_vega"]
    assert len(features) == len(df)


def test_build_options_features_is_nan_during_vol_warmup_then_populated():
    df = _synthetic_df(n=60)
    features = build_options_features(df, vol_window=20)
    assert features["realized_vol"].iloc[:20].isna().all()
    assert not features["realized_vol"].iloc[21:].isna().any()
    assert (features["theoretical_gamma"].dropna() > 0).all()
    assert (features["theoretical_vega"].dropna() > 0).all()


def test_build_options_features_gamma_and_vega_are_identical_for_calls_and_puts():
    """The whole point of excluding delta/theta: gamma and vega are
    genuinely option_type-symmetric (put-call parity), so the module's
    fixed "CE" choice must be provably inert for these two outputs -
    not just asserted in the docstring, verified here directly."""
    from features.theoretical_options import theoretical_option_snapshot

    df = _synthetic_df(n=60)
    as_of_index = 40
    spot = float(df["close"].iloc[as_of_index])
    strike = round(spot / 50.0) * 50.0
    expiry = df["timestamp"].iloc[as_of_index].date() + pd.Timedelta(days=7)

    ce = theoretical_option_snapshot(df, as_of_index, strike, expiry, "CE", 0.07, 20)
    pe = theoretical_option_snapshot(df, as_of_index, strike, expiry, "PE", 0.07, 20)
    assert ce is not None and pe is not None
    assert ce.greeks.gamma == pytest.approx(pe.greeks.gamma)
    assert ce.greeks.vega_per_1pct_vol == pytest.approx(pe.greeks.vega_per_1pct_vol)


@requires_real_data
def test_build_options_features_is_causal_not_leaking_future_bars():
    features = build_options_features(NIFTY_DAILY)
    as_of = 500

    # 1.0 (used by the analogous build_features() test) rounds to a
    # strike of 0 at this module's default 50-point increment, which
    # blows up Black-Scholes' log(spot/strike) with a real
    # ZeroDivisionError - a test-fixture artifact, not a module bug.
    # 99999.0 is a large, obviously-fake value that stays clear of it.
    mutated = NIFTY_DAILY.copy()
    mutated.loc[mutated.index[as_of + 1]:, ["open", "high", "low", "close", "volume"]] = 99999.0
    mutated_features = build_options_features(mutated)

    pd.testing.assert_frame_equal(
        features.iloc[: as_of + 1].reset_index(drop=True),
        mutated_features.iloc[: as_of + 1].reset_index(drop=True),
    )
