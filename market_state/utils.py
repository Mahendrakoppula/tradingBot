"""Shared percentile-rank helper - volatility/momentum states are
classified by where the LATEST value falls within its own trailing
history, not against a hardcoded absolute threshold, so the same logic
works whether the instrument/interval is inherently choppy or calm.
"""
import pandas as pd


def percentile_rank_of_last(series: pd.Series) -> float:
    """Fraction (0.0-1.0) of values in `series` that the last value is
    >= to. Returns 0.5 (neutral) if there's only one value - not enough
    history to say anything about where it sits in a distribution."""
    if len(series) <= 1:
        return 0.5
    last = series.iloc[-1]
    return float((series <= last).sum() / len(series))


def rolling_percentile_rank(series: pd.Series, lookback: int) -> pd.Series:
    """Vectorized, per-row equivalent of calling
    percentile_rank_of_last(series.loc[:t].tail(lookback)) for every row
    t - i.e. the percentile rank AS OF each point in time, using only
    that point's own trailing history. Used by models/feature_engineering.py
    to label an entire historical series efficiently (one pass) instead
    of re-slicing and re-scanning the series once per row, which would
    be quadratic for a long series."""
    return series.rolling(window=lookback, min_periods=1).apply(
        lambda window: 0.5 if len(window) <= 1 else (window <= window[-1]).mean(), raw=True,
    )
