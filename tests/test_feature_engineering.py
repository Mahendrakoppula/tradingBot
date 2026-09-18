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
from models.feature_engineering import batch_regime_labels, build_features, build_mtf_features, build_options_features

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
NIFTY_HOURLY = load_ohlcv("NIFTY", "ONE_HOUR")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")
requires_real_hourly_data = pytest.mark.skipif(len(NIFTY_HOURLY) == 0, reason="real NIFTY hourly data not pulled locally")


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


def _mtf_synthetic_dfs():
    """Two daily bars (2026-01-01, 2026-01-02) and hourly bars spanning
    both days, used with a monkeypatched batch_regime_labels() so the
    alignment MECHANICS (not real regime classification, which needs
    much more history to confirm a swing-based trend) can be verified
    precisely and deterministically."""
    daily_df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-01", "2026-01-02"]).tz_localize("Asia/Kolkata"),
        "open": [100, 101], "high": [101, 102], "low": [99, 100], "close": [100, 101], "volume": [0, 0],
    })
    hourly_times = pd.to_datetime([
        "2026-01-01 09:15", "2026-01-01 14:15", "2026-01-01 15:15",  # day 1's own session, including its LAST bar at 15:15
        "2026-01-02 09:15", "2026-01-02 15:15",  # day 2's own session
    ]).tz_localize("Asia/Kolkata")
    hourly_df = pd.DataFrame({
        "timestamp": hourly_times, "open": [100] * 5, "high": [101] * 5, "low": [99] * 5, "close": [100] * 5, "volume": [0] * 5,
    })
    return daily_df, hourly_df


def test_build_mtf_features_includes_same_day_hourly_bars_not_just_prior_days(monkeypatch):
    """The core leakage-safety fix this function exists for: day 1's
    OWN 15:15 hourly bar (which closes before day 1's own 15:30 daily
    close) must be included when computing day 1's row - a naive
    merge_asof on the raw midnight daily timestamp would wrongly exclude
    it, understating what's genuinely already known by day 1's close."""
    daily_df, hourly_df = _mtf_synthetic_dfs()

    # Distinct labels per row so the alignment result unambiguously reveals which hourly row got picked.
    daily_labels = pd.Series(["TRENDING_UP", "TRENDING_DOWN"])
    hourly_labels = pd.Series(["H0_0915", "H0_1415", "H0_1515", "H1_0915", "H1_1515"])

    def fake_batch_regime_labels(df, *args, **kwargs):
        return daily_labels if len(df) == 2 else hourly_labels

    monkeypatch.setattr("models.feature_engineering.batch_regime_labels", fake_batch_regime_labels)
    monkeypatch.setattr("models.feature_engineering._direction_of", lambda r: {
        "TRENDING_UP": "UP", "TRENDING_DOWN": "DOWN",
        "H0_1515": "UP", "H1_1515": "DOWN",  # only the LAST bar of each day is directional, to prove which one wins
    }.get(r))

    result = build_mtf_features(daily_df, hourly_df)
    # Day 1: daily=UP, hourly as-of day 1's 15:30 close should resolve to H0_1515 (=UP) - the SAME day's own last bar.
    assert result["mtf_both_directional"].iloc[0] == 1.0
    assert result["mtf_agree"].iloc[0] == 1.0
    # Day 2: daily=DOWN, hourly as-of day 2's 15:30 close should resolve to H1_1515 (=DOWN).
    assert result["mtf_both_directional"].iloc[1] == 1.0
    assert result["mtf_agree"].iloc[1] == 1.0


def test_build_mtf_features_marks_not_both_directional_as_nan_agreement(monkeypatch):
    daily_df, hourly_df = _mtf_synthetic_dfs()
    daily_labels = pd.Series(["TRENDING_UP", "RANGING"])
    hourly_labels = pd.Series(["RANGING", "RANGING", "RANGING", "RANGING", "TRENDING_DOWN"])

    def fake_batch_regime_labels(df, *args, **kwargs):
        return daily_labels if len(df) == 2 else hourly_labels

    monkeypatch.setattr("models.feature_engineering.batch_regime_labels", fake_batch_regime_labels)

    result = build_mtf_features(daily_df, hourly_df)
    # Day 1: daily=UP, hourly=RANGING (not directional, but KNOWN - not
    # missing data) - not both directional, so agreement is the neutral
    # 0.5 sentinel, not NaN (RANGING is real information, never dropped
    # just for not trending).
    assert result["mtf_both_directional"].iloc[0] == 0.0
    assert result["mtf_agree"].iloc[0] == 0.5
    # Day 2: daily=RANGING, hourly's last bar=DOWN - still not BOTH directional, same neutral treatment.
    assert result["mtf_both_directional"].iloc[1] == 0.0
    assert result["mtf_agree"].iloc[1] == 0.5


def test_build_mtf_features_unknown_warmup_is_genuine_nan_not_neutral(monkeypatch):
    """The real distinction this design draws: "UNKNOWN" (genuine
    warmup, no information yet) must still produce NaN (dropped
    downstream) - only a KNOWN non-directional state (RANGING/VOLATILE)
    gets the neutral 0.5 sentinel."""
    daily_df, hourly_df = _mtf_synthetic_dfs()
    daily_labels = pd.Series(["UNKNOWN", "TRENDING_UP"])
    hourly_labels = pd.Series(["UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "RANGING"])

    def fake_batch_regime_labels(df, *args, **kwargs):
        return daily_labels if len(df) == 2 else hourly_labels

    monkeypatch.setattr("models.feature_engineering.batch_regime_labels", fake_batch_regime_labels)

    result = build_mtf_features(daily_df, hourly_df)
    # Day 1: daily itself is still UNKNOWN (genuine warmup) - both outputs must be real NaN.
    assert pd.isna(result["mtf_both_directional"].iloc[0])
    assert pd.isna(result["mtf_agree"].iloc[0])
    # Day 2: daily=UP (known), hourly=RANGING (known, not UNKNOWN) - both known, so NOT NaN, uses the neutral sentinel.
    assert result["mtf_both_directional"].iloc[1] == 0.0
    assert result["mtf_agree"].iloc[1] == 0.5


@requires_real_data
@requires_real_hourly_data
def test_build_mtf_features_on_real_data_is_nan_before_hourly_history_then_populated():
    features = build_mtf_features(NIFTY_DAILY, NIFTY_HOURLY)
    assert len(features) == len(NIFTY_DAILY)
    # the earliest daily rows have no hourly data at all yet - a genuine
    # warmup gap, so mtf_both_directional must be real NaN there (never
    # fabricated as a fake 0.0), and by the END of the series (well
    # within the real hourly window), it's a real 0.0/1.0 mix, not
    # universally absent.
    hourly_start = NIFTY_HOURLY["timestamp"].min()
    rows_before_hourly = NIFTY_DAILY[NIFTY_DAILY["timestamp"] < hourly_start]
    if len(rows_before_hourly) > 0:
        assert features["mtf_both_directional"].iloc[: len(rows_before_hourly)].isna().all()
    assert features["mtf_both_directional"].iloc[-30:].notna().any()  # sanity: real values exist late in the series, not NaN/garbage throughout
    assert set(features["mtf_both_directional"].dropna().unique()) <= {0.0, 1.0}
    assert set(features["mtf_agree"].dropna().unique()) <= {0.0, 0.5, 1.0}


@requires_real_data
@requires_real_hourly_data
def test_build_mtf_features_is_causal_not_leaking_future_bars():
    features = build_mtf_features(NIFTY_DAILY, NIFTY_HOURLY)
    as_of = min(500, len(NIFTY_DAILY) - 2)

    mutated_daily = NIFTY_DAILY.copy()
    mutated_daily.loc[mutated_daily.index[as_of + 1]:, ["open", "high", "low", "close", "volume"]] = 99999.0
    # The real "as of" cutoff row `as_of` uses is that day's OWN 15:30 close,
    # not raw midnight - mutating anything after midnight would wrongly
    # corrupt that SAME day's own legitimately-included hourly bars too.
    as_of_cutoff = NIFTY_DAILY["timestamp"].iloc[as_of].normalize() + pd.Timedelta(hours=15, minutes=30)
    mutated_hourly = NIFTY_HOURLY.copy()
    mutated_hourly.loc[mutated_hourly["timestamp"] > as_of_cutoff, ["open", "high", "low", "close", "volume"]] = 99999.0

    mutated_features = build_mtf_features(mutated_daily, mutated_hourly)
    pd.testing.assert_frame_equal(
        features.iloc[: as_of + 1].reset_index(drop=True),
        mutated_features.iloc[: as_of + 1].reset_index(drop=True),
    )
