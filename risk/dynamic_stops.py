"""Dynamic stop-loss/target/trailing (spec: individual trade stops must
NOT be hardcoded to a fixed rupee amount - computed from market
structure/ATR/volatility). This first pass uses ATR and swing structure
only, both already built (Phases 3/5). The spec's own EV-based target
and ML-probability-based trailing explicitly depend on Models 2
(direction-probability) and 6 (MFE predictor), which don't exist yet -
Model 1 didn't even beat its baseline (see models/EXPERIMENTS.md).
Building a fake EV/ML-driven version now would mean pretending those
models exist; this is instead an honest, currently-buildable rule-based
version - a real placeholder for structure/ATR-only inputs, to be
upgraded once real ML signal exists to add, not before.
"""
from dataclasses import dataclass

import pandas as pd

from market_state.structure import find_swing_points
from market_state.volatility import atr

DEFAULT_STOP_ATR_MULTIPLIER = 1.5
DEFAULT_TARGET_ATR_MULTIPLIER = 3.0  # keeps a >=2:1 reward:risk skeleton by default, not a claim of validated EV
DEFAULT_TRAIL_ATR_MULTIPLIER = 2.0


@dataclass
class StopTargetLevels:
    entry_price: float
    stop_price: float
    target_price: float
    stop_distance_points: float
    target_distance_points: float
    stop_source: str  # "atr" | "structure"
    target_source: str  # "atr" (only source built so far)


def _nearest_swing_low_below(df: pd.DataFrame, entry_index: int, entry_price: float, window: int = 3) -> float | None:
    _, swing_lows = find_swing_points(df.iloc[: entry_index + 1], window)
    candidates = [price for _, price in swing_lows if price < entry_price]
    return max(candidates) if candidates else None  # nearest below = the highest of them


def _nearest_swing_high_above(df: pd.DataFrame, entry_index: int, entry_price: float, window: int = 3) -> float | None:
    swing_highs, _ = find_swing_points(df.iloc[: entry_index + 1], window)
    candidates = [price for _, price in swing_highs if price > entry_price]
    return min(candidates) if candidates else None  # nearest above = the lowest of them


def compute_stop_and_target(
    df: pd.DataFrame,
    entry_index: int,
    direction: str,
    atr_period: int = 14,
    stop_atr_multiplier: float = DEFAULT_STOP_ATR_MULTIPLIER,
    target_atr_multiplier: float = DEFAULT_TARGET_ATR_MULTIPLIER,
) -> StopTargetLevels:
    """direction: "CE" (long-bias) or "PE" (short-bias). Raises
    ValueError if there isn't enough history at entry_index to compute
    ATR - never silently substitutes a fixed/guessed stop distance."""
    if direction not in ("CE", "PE"):
        raise ValueError(f"direction must be 'CE' or 'PE', got {direction!r}")

    entry_price = float(df["close"].iloc[entry_index])
    atr_series = atr(df.iloc[: entry_index + 1], atr_period)
    atr_value = atr_series.iloc[-1] if len(atr_series) else float("nan")
    if pd.isna(atr_value) or atr_value <= 0:
        raise ValueError(f"Not enough history at entry_index={entry_index} to compute a {atr_period}-period ATR")

    atr_stop_distance = atr_value * stop_atr_multiplier
    target_distance = atr_value * target_atr_multiplier

    if direction == "CE":
        structure_level = _nearest_swing_low_below(df, entry_index, entry_price)
        structure_stop_distance = (entry_price - structure_level) if structure_level is not None else None
    else:
        structure_level = _nearest_swing_high_above(df, entry_index, entry_price)
        structure_stop_distance = (structure_level - entry_price) if structure_level is not None else None

    # The WIDER of ATR vs. structure, when a structural level exists - a
    # structural stop placed too close to entry gets stopped out by
    # ordinary noise, which defeats the point of using structure at all.
    if structure_stop_distance is not None and structure_stop_distance > atr_stop_distance:
        stop_distance, stop_source = structure_stop_distance, "structure"
    else:
        stop_distance, stop_source = atr_stop_distance, "atr"

    if direction == "CE":
        stop_price = entry_price - stop_distance
        target_price = entry_price + target_distance
    else:
        stop_price = entry_price + stop_distance
        target_price = entry_price - target_distance

    return StopTargetLevels(
        entry_price=entry_price, stop_price=stop_price, target_price=target_price,
        stop_distance_points=stop_distance, target_distance_points=target_distance,
        stop_source=stop_source, target_source="atr",
    )


def update_trailing_stop(
    direction: str,
    current_stop_price: float,
    current_price: float,
    atr_value: float,
    trail_atr_multiplier: float = DEFAULT_TRAIL_ATR_MULTIPLIER,
) -> float:
    """Chandelier-style ATR trail. Only ever tightens (moves in the
    favorable direction) - never loosens, so an adverse move never
    widens the stop back out."""
    if direction not in ("CE", "PE"):
        raise ValueError(f"direction must be 'CE' or 'PE', got {direction!r}")
    if direction == "CE":
        candidate = current_price - trail_atr_multiplier * atr_value
        return max(current_stop_price, candidate)
    candidate = current_price + trail_atr_multiplier * atr_value
    return min(current_stop_price, candidate)
