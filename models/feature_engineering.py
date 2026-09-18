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
import datetime as dt

import pandas as pd

from features.mtf import _direction_of
from features.theoretical_options import theoretical_option_snapshot
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


def build_options_features(
    df: pd.DataFrame,
    strike_increment: float = 50.0,
    days_to_expiry: int = 7,
    risk_free_rate: float = 0.07,
    vol_window: int = 20,
) -> pd.DataFrame:
    """Options-derived features - a MATERIALLY different information
    source from build_features()'s pure spot-technicals (ATR/ROC): the
    realized-vol level itself, plus theoretical gamma/vega, which
    encode convexity/vol-sensitivity that no spot-price indicator
    captures directly. Built specifically to test whether Models 1/2's
    "no signal with this feature set" conclusion (models/EXPERIMENTS.md,
    Experiments 001-005) is a feature-set limitation rather than an
    absence of any learnable structure at all.

    Uses a FIXED, synthetic ATM-strike/7-day-expiry convention per row
    (matching backtesting/event_loop.py's own BacktestConfig defaults)
    purely to have a well-defined, reproducible contract to price at
    each point in time - this is NOT a claim that this specific
    contract was ever tradable or real, exactly like every other
    theoretical-pricing use in this project (features/theoretical_options.py's
    own documented spot-proxy limitation applies here too).

    Deliberately excludes delta/theta: unlike gamma and vega, they
    differ between calls and puts (put-call parity), so including one
    fixed option_type's delta/theta would inject an arbitrary,
    direction-coupled asymmetry into a feature set meant to help
    predict direction from scratch - a subtle leak risk, not a real
    signal. gamma and vega are IDENTICAL for calls and puts at the same
    spot/strike/vol/rate, so the fixed "CE" choice below is provably
    inert for these two outputs specifically.

    Returns NaN for rows with insufficient history for the realized-vol
    estimate (mirrors theoretical_option_snapshot() returning None) -
    dropped downstream the same way build_features()'s own warmup NaNs
    are, never fabricated."""
    n = len(df)
    realized_vol = [float("nan")] * n
    gamma = [float("nan")] * n
    vega = [float("nan")] * n

    for t in range(n):
        ts = df["timestamp"].iloc[t]
        as_of_date = ts.date() if hasattr(ts, "date") else ts
        spot = float(df["close"].iloc[t])
        strike = round(spot / strike_increment) * strike_increment
        expiry = as_of_date + dt.timedelta(days=days_to_expiry)

        snapshot = theoretical_option_snapshot(df, t, strike, expiry, "CE", risk_free_rate, vol_window)
        if snapshot is None:
            continue
        realized_vol[t] = snapshot.volatility_used
        gamma[t] = snapshot.greeks.gamma
        vega[t] = snapshot.greeks.vega_per_1pct_vol

    return pd.DataFrame({
        "realized_vol": realized_vol,
        "theoretical_gamma": gamma,
        "theoretical_vega": vega,
    }, index=df.index)


def build_mtf_features(
    daily_df: pd.DataFrame,
    hourly_df: pd.DataFrame,
    structure_window: int = DEFAULT_SWING_WINDOW,
    atr_period: int = 14,
    vol_lookback: int = 100,
) -> pd.DataFrame:
    """Multi-timeframe alignment features - the other "materially
    different feature set" Experiment 006 identified but deferred as
    harder/more leakage-risk-prone than theoretical Greeks (which was
    tried first, in Experiment 006). features/mtf.py's own point-in-time
    fuse_states()/classify_mtf() aren't directly usable for whole-series
    ML training (same quadratic-rescan problem batch_regime_labels() was
    built to avoid for the single-timeframe case) - this is that
    module's batch equivalent, reusing batch_regime_labels() on each
    timeframe's OWN bars rather than reimplementing regime
    classification.

    LEAKAGE-SAFETY, the real risk this module's own docstring warns
    about repeating: daily bars are stored with a MIDNIGHT timestamp
    (verified directly - NIFTY's own real data confirms 00:00:00+05:30),
    not the real 15:30 IST close the bar's OHLC actually represents. A
    naive merge_asof keyed on the raw daily timestamp would silently
    exclude that SAME day's own intraday bars (09:15-15:15, all AFTER
    midnight) - understating available information, not leaking future
    information, but still wrong. Fixed by explicitly computing each
    daily row's real "as of" cutoff as that calendar date's own 15:30
    close, so every intraday bar from that SAME trading session (which
    genuinely closed by the time the daily bar itself is "known") is
    correctly included, and nothing from the NEXT day ever is.

    Returns `mtf_both_directional` (1.0 if daily AND the intraday
    timeframe both show a confirmed trend, else 0.0) and `mtf_agree`
    (1.0 if both directional and the SAME direction, 0.0 if both
    directional and opposite, 0.5 if NOT both directional but both
    ARE known - RANGING/VOLATILE is a real, informative state, not
    missing data, and must not be thrown away just for not trending).
    Both are genuine NaN (dropped downstream the same way every other
    feature's warmup NaN is, never fabricated) only when there is
    truly no information yet: either series' own "UNKNOWN" warmup
    period, or every row before `hourly_df`'s own history begins at
    all - real intraday data only covers a recent window (see
    backtesting/BACKTESTS.md's Run 011/012), a genuine, disclosed
    limitation of this feature set, not a bug."""
    daily_regime = batch_regime_labels(daily_df, structure_window, atr_period, vol_lookback)
    hourly_regime = batch_regime_labels(hourly_df, structure_window, atr_period, vol_lookback)

    as_of = daily_df["timestamp"].dt.normalize() + pd.Timedelta(hours=15, minutes=30)
    hourly_lookup = pd.DataFrame({
        "timestamp": hourly_df["timestamp"].values, "hourly_regime": hourly_regime.values,
    }).sort_values("timestamp").reset_index(drop=True)
    daily_as_of = pd.DataFrame({"as_of": as_of.values})

    merged = pd.merge_asof(daily_as_of, hourly_lookup, left_on="as_of", right_on="timestamp", direction="backward")
    aligned_hourly_regime = merged["hourly_regime"].reset_index(drop=True)
    daily_regime_r = daily_regime.reset_index(drop=True)
    daily_directions = daily_regime_r.map(_direction_of)
    hourly_directions = aligned_hourly_regime.map(_direction_of)

    # "UNKNOWN" (or no merge_asof match at all, i.e. real NaN) means
    # genuinely no information yet - the same warmup/no-data case every
    # other feature in this module handles by producing NaN, dropped
    # downstream, never fabricated. RANGING/VOLATILE is NOT that - it's
    # a fully known, real, non-directional state and must NOT be treated
    # as missing just because it isn't trending.
    daily_known = daily_regime_r != "UNKNOWN"
    hourly_known = aligned_hourly_regime.notna() & (aligned_hourly_regime != "UNKNOWN")

    both_directional = []
    agree = []
    for d_known, h_known, d, h in zip(daily_known, hourly_known, daily_directions, hourly_directions):
        if not (d_known and h_known):
            both_directional.append(float("nan"))
            agree.append(float("nan"))
            continue
        # pandas' own .map() silently coerces the None _direction_of()
        # returns into float NaN once stored in a Series - "is None"
        # never fires against that (nan is None is False), the same
        # numpy/pandas type-coercion gotcha already caught once this
        # session (backtesting/moneyness_analysis.py). pd.isna() catches
        # both real None and NaN correctly.
        if pd.isna(d) or pd.isna(h):
            both_directional.append(0.0)
            agree.append(0.5)  # known, non-directional (RANGING/VOLATILE) on at least one side - a real, informative state, not missing data
        else:
            both_directional.append(1.0)
            agree.append(1.0 if d == h else 0.0)

    return pd.DataFrame({"mtf_both_directional": both_directional, "mtf_agree": agree}, index=daily_df.index)
