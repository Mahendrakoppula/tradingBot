"""Batch (whole-series) feature and label computation for ML training.

market_state/classifier.py's classify_market_state() is written for a
single point-in-time call (Phase 3's live-wiring shape: "what's the
regime right now"). Calling it once per historical row by re-slicing
df.iloc[:t+1] would be quadratic for a long series (each call re-scans
its own O(n) swing-detection pass) - intractable for training-set sizes.

Every function here is a VECTORIZED, whole-series equivalent: for every
row t, it produces exactly what classify_market_state(df.iloc[:t+1])
would have produced, using only information available up to and
including t (rolling/backward-looking only - the causality discipline
enforced everywhere else in this project, e.g.
features/realized_volatility.py's as_of pattern). This equivalence is
not just asserted - tests/test_feature_engineering.py cross-checks
batch_regime_labels() against classify_market_state() at multiple
sampled rows on real data.
"""
import pandas as pd

from market_state.momentum import STRONG_PERCENTILE, rate_of_change
from market_state.structure import DEFAULT_SWING_WINDOW, find_swing_points
from market_state.utils import rolling_percentile_rank
from market_state.volatility import HIGH_PERCENTILE, LOW_PERCENTILE, atr


def _rolling_trend_labels(df: pd.DataFrame, window: int = DEFAULT_SWING_WINDOW) -> list[str]:
    """Whole-series equivalent of market_state.structure.classify_structure's
    .trend, one label per row. A swing point at row index p is only
    "confirmed" (knowable) once `window` more bars exist after it - same
    confirmation-lag rule find_swing_points already enforces, just
    walked forward once instead of re-scanned per row."""
    swing_highs, swing_lows = find_swing_points(df, window)
    sh_confirmed = [(idx + window, price) for idx, price in swing_highs]
    sl_confirmed = [(idx + window, price) for idx, price in swing_lows]

    n = len(df)
    trend = ["INSUFFICIENT_DATA"] * n
    sh_ptr = sl_ptr = 0
    last_two_sh: list[float] = []
    last_two_sl: list[float] = []

    for t in range(n):
        while sh_ptr < len(sh_confirmed) and sh_confirmed[sh_ptr][0] <= t:
            last_two_sh.append(sh_confirmed[sh_ptr][1])
            last_two_sh = last_two_sh[-2:]
            sh_ptr += 1
        while sl_ptr < len(sl_confirmed) and sl_confirmed[sl_ptr][0] <= t:
            last_two_sl.append(sl_confirmed[sl_ptr][1])
            last_two_sl = last_two_sl[-2:]
            sl_ptr += 1

        if len(last_two_sh) < 2 or len(last_two_sl) < 2:
            continue
        higher_highs = last_two_sh[-1] > last_two_sh[-2]
        higher_lows = last_two_sl[-1] > last_two_sl[-2]
        lower_highs = last_two_sh[-1] < last_two_sh[-2]
        lower_lows = last_two_sl[-1] < last_two_sl[-2]
        if higher_highs and higher_lows:
            trend[t] = "UPTREND"
        elif lower_highs and lower_lows:
            trend[t] = "DOWNTREND"
        else:
            trend[t] = "RANGE"

    return trend


def _rolling_volatility_labels(df: pd.DataFrame, atr_period: int, lookback: int) -> pd.Series:
    atr_series = atr(df, atr_period)
    atr_valid = atr_series.dropna()
    pct_valid = rolling_percentile_rank(atr_valid, lookback)
    pct = pct_valid.reindex(df.index)

    def _label(p: float) -> str:
        if pd.isna(p):
            return "INSUFFICIENT_DATA"
        if p < LOW_PERCENTILE:
            return "LOW"
        if p > HIGH_PERCENTILE:
            return "HIGH"
        return "NORMAL"

    return pct.apply(_label)


def _combine_regime(structure_trend: str, volatility_label: str) -> str:
    """Deliberately duplicated (not imported) from
    market_state.classifier._combine_regime, which is a private helper
    on that module - tests/test_feature_engineering.py cross-checks this
    against the live classifier's actual output so any future drift
    between the two is caught immediately, not silently."""
    if structure_trend == "INSUFFICIENT_DATA" or volatility_label == "INSUFFICIENT_DATA":
        return "UNKNOWN"
    if volatility_label == "HIGH":
        return "VOLATILE"
    if structure_trend == "UPTREND":
        return "TRENDING_UP"
    if structure_trend == "DOWNTREND":
        return "TRENDING_DOWN"
    return "RANGING"


def batch_regime_labels(
    df: pd.DataFrame,
    structure_window: int = DEFAULT_SWING_WINDOW,
    atr_period: int = 14,
    vol_lookback: int = 100,
) -> pd.Series:
    trend = _rolling_trend_labels(df, structure_window)
    vol_label = _rolling_volatility_labels(df, atr_period, vol_lookback)
    regimes = [_combine_regime(t, v) for t, v in zip(trend, vol_label)]
    return pd.Series(regimes, index=df.index, name="regime")


def build_features(
    df: pd.DataFrame,
    atr_period: int = 14,
    vol_lookback: int = 100,
    mom_lookback: int = 10,
    mom_history: int = 100,
) -> pd.DataFrame:
    """Numeric feature matrix - every column is causal (row t uses only
    data up to and including t). NOT the same thing as
    batch_regime_labels()'s output, which is the ML TARGET this feeds a
    model to predict at some horizon - see models/regime_classifier.py.

    Deliberately has NO volume-derived feature: real historical data
    confirmed SmartAPI reports index spot volume as 0 always, for all
    three in-scope indices at every interval down to ONE_MINUTE (see
    market_state/liquidity.py's docstring) - a relative-volume feature
    would just be 0/0 = NaN on every row, not a real signal. Reintroduce
    one if a future data source provides real index volume, or if scope
    ever expands to stock-level instruments (which DO have real volume -
    see research/fetch_historical.py on `main`)."""
    atr_series = atr(df, atr_period)
    atr_valid = atr_series.dropna()
    atr_pct = rolling_percentile_rank(atr_valid, vol_lookback).reindex(df.index)

    roc_series = rate_of_change(df, mom_lookback)
    roc_valid = roc_series.dropna()
    roc_mag_pct = rolling_percentile_rank(roc_valid.abs(), mom_history).reindex(df.index)

    return pd.DataFrame({
        "atr": atr_series,
        "atr_percentile": atr_pct,
        "roc": roc_series,
        "roc_magnitude_percentile": roc_mag_pct,
        "is_strong_momentum": (roc_mag_pct >= STRONG_PERCENTILE).astype(float),
    }, index=df.index)
