"""Strategy portfolio orchestrator - runs every eligible-for-the-current-
regime strategy and collects candidate signals. Deliberately does NOT
rank, size, or pick a single "best" signal - that's Trade Ranking
(a later phase, consuming this module's output alongside a later ML
meta-model), kept separate on purpose.
"""
import pandas as pd

from features.mtf import MTFAlignment
from market_state.classifier import MarketState
from strategies.base import Signal, Strategy
from strategies.breakout_orb import OpeningRangeBreakoutStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.trend_following import TrendFollowingStrategy

DEFAULT_STRATEGIES: list[Strategy] = [
    TrendFollowingStrategy(),
    MeanReversionStrategy(),
    OpeningRangeBreakoutStrategy(),
]


def generate_candidate_signals(
    df: pd.DataFrame,
    market_state: MarketState,
    mtf: MTFAlignment | None = None,
    strategies: list[Strategy] | None = None,
) -> list[Signal]:
    strategies = DEFAULT_STRATEGIES if strategies is None else strategies
    signals: list[Signal] = []
    for strategy in strategies:
        if not strategy.is_eligible(market_state):
            continue
        signal = strategy.generate(df, market_state, mtf)
        if signal is not None:
            signals.append(signal)
    return signals
