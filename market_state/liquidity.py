"""Liquidity state - relative volume vs. its own trailing rolling
average.

CONFIRMED DATA LIMITATION (real historical data, all three in-scope
indices, every interval down to ONE_MINUTE): SmartAPI reports index spot
volume as 0, always - indices aren't a traded security with their own
volume the way a stock is. classify_liquidity() already handles this
correctly (trailing_avg<=0 returns INSUFFICIENT_DATA, never divides by
zero or crashes), but callers should expect this dimension of
market_state to be permanently INSUFFICIENT_DATA for NIFTY/BANKNIFTY/
SENSEX with this data source - not a bug, not "not implemented yet".
models/feature_engineering.py's build_features() excludes a
volume-derived feature entirely for exactly this reason.
"""
from dataclasses import dataclass

import pandas as pd

DEFAULT_PERIOD = 20
LOW_RATIO = 0.5
HIGH_RATIO = 1.5


@dataclass
class LiquidityState:
    label: str  # "LOW" | "NORMAL" | "HIGH" | "INSUFFICIENT_DATA"
    relative_volume: float | None


def classify_liquidity(df: pd.DataFrame, period: int = DEFAULT_PERIOD) -> LiquidityState:
    if len(df) < period + 1:
        return LiquidityState("INSUFFICIENT_DATA", None)

    volume = df["volume"]
    trailing_avg = volume.iloc[-(period + 1):-1].mean()  # excludes the latest bar itself
    if trailing_avg <= 0:
        return LiquidityState("INSUFFICIENT_DATA", None)

    relative = float(volume.iloc[-1] / trailing_avg)
    if relative < LOW_RATIO:
        label = "LOW"
    elif relative > HIGH_RATIO:
        label = "HIGH"
    else:
        label = "NORMAL"
    return LiquidityState(label, relative)
