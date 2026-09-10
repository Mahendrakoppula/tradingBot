"""Price structure: swing high/low detection and trend structure
classification from raw price action - no indicators. A bar is a
confirmed swing high/low only once `window` bars exist on BOTH sides
that don't exceed it, i.e. confirmation is inherently lagged by
`window` bars - this is a structural fact about swing detection, not a
bug, and callers must not treat the most recent `window` bars as having
a known swing status yet.
"""
from dataclasses import dataclass, field

import pandas as pd

DEFAULT_SWING_WINDOW = 3


@dataclass
class StructureState:
    trend: str  # "UPTREND" | "DOWNTREND" | "RANGE" | "INSUFFICIENT_DATA"
    swing_highs: list[tuple[int, float]] = field(default_factory=list)  # (row index, price), chronological
    swing_lows: list[tuple[int, float]] = field(default_factory=list)


def find_swing_points(df: pd.DataFrame, window: int = DEFAULT_SWING_WINDOW) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    n = len(df)
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    for i in range(window, n - window):
        left_h, right_h = highs[i - window:i], highs[i + 1:i + window + 1]
        if highs[i] > left_h.max() and highs[i] > right_h.max():
            swing_highs.append((i, float(highs[i])))
        left_l, right_l = lows[i - window:i], lows[i + 1:i + window + 1]
        if lows[i] < left_l.min() and lows[i] < right_l.min():
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def classify_structure(df: pd.DataFrame, window: int = DEFAULT_SWING_WINDOW) -> StructureState:
    """UPTREND: the last two confirmed swing highs AND the last two swing
    lows are both rising. DOWNTREND: mirror. Anything else (including one
    rising and one falling - a common pre-reversal/broadening pattern) is
    RANGE, not guessed at. Needs at least 2 of each to classify at all."""
    swing_highs, swing_lows = find_swing_points(df, window)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return StructureState("INSUFFICIENT_DATA", swing_highs, swing_lows)

    higher_highs = swing_highs[-1][1] > swing_highs[-2][1]
    higher_lows = swing_lows[-1][1] > swing_lows[-2][1]
    lower_highs = swing_highs[-1][1] < swing_highs[-2][1]
    lower_lows = swing_lows[-1][1] < swing_lows[-2][1]

    if higher_highs and higher_lows:
        trend = "UPTREND"
    elif lower_highs and lower_lows:
        trend = "DOWNTREND"
    else:
        trend = "RANGE"
    return StructureState(trend, swing_highs, swing_lows)
