"""Momentum state from raw rate-of-change, NOT RSI/MACD - per the spec's
explicit "do not start with RSI/MACD" philosophy, classic oscillators
are supporting features for a later phase's ML feature set, not part of
this pre-indicator engine. Magnitude is classified by percentile rank
against its own trailing ROC history (see market_state/utils.py),
direction by sign.
"""
from dataclasses import dataclass

import pandas as pd

from market_state.utils import percentile_rank_of_last

DEFAULT_LOOKBACK = 10
DEFAULT_HISTORY = 100
STRONG_PERCENTILE = 0.80  # top/bottom 20% of trailing |ROC| magnitude


@dataclass
class MomentumState:
    label: str  # "STRONG_UP" | "WEAK_UP" | "FLAT" | "WEAK_DOWN" | "STRONG_DOWN" | "INSUFFICIENT_DATA"
    roc: float | None
    magnitude_percentile: float | None


def rate_of_change(df: pd.DataFrame, lookback: int = DEFAULT_LOOKBACK) -> pd.Series:
    return df["close"].pct_change(periods=lookback)


def classify_momentum(df: pd.DataFrame, lookback: int = DEFAULT_LOOKBACK, history: int = DEFAULT_HISTORY) -> MomentumState:
    roc_series = rate_of_change(df, lookback).dropna()
    if roc_series.empty:
        return MomentumState("INSUFFICIENT_DATA", None, None)

    window = roc_series.tail(history)
    latest_roc = float(window.iloc[-1])
    magnitude_pct = percentile_rank_of_last(window.abs())

    is_strong = magnitude_pct >= STRONG_PERCENTILE
    if abs(latest_roc) < 1e-12:
        label = "FLAT"
    elif latest_roc > 0:
        label = "STRONG_UP" if is_strong else "WEAK_UP"
    else:
        label = "STRONG_DOWN" if is_strong else "WEAK_DOWN"
    return MomentumState(label, latest_roc, magnitude_pct)
