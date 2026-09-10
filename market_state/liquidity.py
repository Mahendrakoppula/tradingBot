"""Liquidity state - relative volume vs. its own trailing rolling
average. LOW relative volume matters even at the index-option level
(spot-proxy scope, Phase 2's own known limitation) because it's still a
proxy for how much conviction is behind the current price move -
computed from spot volume, not option-contract volume, since only spot
history exists.
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
