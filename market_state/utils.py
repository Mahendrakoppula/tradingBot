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
