"""Realized volatility state - ATR-based, classified by percentile rank
against its own trailing history (see market_state/utils.py) rather than
a fixed rupee/point threshold that wouldn't generalize across
instruments or across a single instrument's own regime changes over
time.
"""
from dataclasses import dataclass

import pandas as pd

from market_state.utils import percentile_rank_of_last

DEFAULT_ATR_PERIOD = 14
DEFAULT_LOOKBACK = 100
LOW_PERCENTILE = 0.33
HIGH_PERCENTILE = 0.67


@dataclass
class VolatilityState:
    label: str  # "LOW" | "NORMAL" | "HIGH" | "INSUFFICIENT_DATA"
    atr: float | None
    percentile: float | None


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = DEFAULT_ATR_PERIOD) -> pd.Series:
    return true_range(df).rolling(window=period, min_periods=period).mean()


def classify_volatility(df: pd.DataFrame, atr_period: int = DEFAULT_ATR_PERIOD, lookback: int = DEFAULT_LOOKBACK) -> VolatilityState:
    atr_series = atr(df, atr_period).dropna()
    if atr_series.empty:
        return VolatilityState("INSUFFICIENT_DATA", None, None)

    window = atr_series.tail(lookback)
    pct = percentile_rank_of_last(window)
    latest_atr = float(window.iloc[-1])

    if pct < LOW_PERCENTILE:
        label = "LOW"
    elif pct > HIGH_PERCENTILE:
        label = "HIGH"
    else:
        label = "NORMAL"
    return VolatilityState(label, latest_atr, pct)
