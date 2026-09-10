"""Combines structure + volatility + momentum + liquidity into one
MarketState snapshot and a coarse `regime` tag for later phases
(strategy eligibility-by-regime, Phase 5) to gate on.

The combination rule below is a first-pass heuristic, not a validated
signal - per the spec, whether this regime tag actually helps strategy
selection is something the strategy-framework/backtesting phases must
test, not something this module gets to assume.
"""
from dataclasses import dataclass

import pandas as pd

from market_state.liquidity import LiquidityState, classify_liquidity
from market_state.momentum import MomentumState, classify_momentum
from market_state.structure import StructureState, classify_structure
from market_state.volatility import VolatilityState, classify_volatility


@dataclass
class MarketState:
    structure: StructureState
    volatility: VolatilityState
    momentum: MomentumState
    liquidity: LiquidityState
    regime: str  # "TRENDING_UP" | "TRENDING_DOWN" | "RANGING" | "VOLATILE" | "UNKNOWN"


def _combine_regime(structure: StructureState, volatility: VolatilityState) -> str:
    if structure.trend == "INSUFFICIENT_DATA" or volatility.label == "INSUFFICIENT_DATA":
        return "UNKNOWN"
    # High volatility dominates the regime tag regardless of trend
    # direction - a high-vol uptrend and a high-vol downtrend both carry
    # the same elevated risk-sizing implications for a later phase, which
    # matters more than the directional label at that point.
    if volatility.label == "HIGH":
        return "VOLATILE"
    if structure.trend == "UPTREND":
        return "TRENDING_UP"
    if structure.trend == "DOWNTREND":
        return "TRENDING_DOWN"
    return "RANGING"


def classify_market_state(df: pd.DataFrame) -> MarketState:
    structure = classify_structure(df)
    volatility = classify_volatility(df)
    momentum = classify_momentum(df)
    liquidity = classify_liquidity(df)
    regime = _combine_regime(structure, volatility)
    return MarketState(structure, volatility, momentum, liquidity, regime)
