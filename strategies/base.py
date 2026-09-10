"""Strategy interface. Every strategy declares which market-state
regimes (see market_state/classifier.py) it's eligible to even be
evaluated in - a mean-reversion strategy has no business generating a
signal during a strong trend, and vice versa. Regime-gating happens
BEFORE a strategy's own entry logic runs, not as an afterthought filter
on its output.

A Strategy returns a Signal (a CANDIDATE, not a sized/risk-checked
trade) or None. Position sizing, risk limits, and trade ranking across
multiple candidate signals are later phases' responsibility, not this
one's - keeping signal generation and risk/sizing decisions in separate
modules is deliberate, per the spec's layered architecture.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from features.mtf import MTFAlignment
from market_state.classifier import MarketState


@dataclass
class Signal:
    strategy_name: str
    direction: str  # "CE" | "PE"
    confidence: float  # 0.0-1.0 - NOT a probability from a calibrated model (that's Phase 9's job), a relative strength-of-setup score this strategy assigns its own candidates
    rationale: str


class Strategy(ABC):
    name: str
    eligible_regimes: frozenset[str]

    def is_eligible(self, market_state: MarketState) -> bool:
        return market_state.regime in self.eligible_regimes

    @abstractmethod
    def generate(self, df: pd.DataFrame, market_state: MarketState, mtf: MTFAlignment | None = None) -> Signal | None:
        """`df` is the OHLCV history up to and including the current
        decision bar - implementations must never look past df.iloc[-1],
        the same lookahead discipline enforced elsewhere in this project
        (see features/realized_volatility.py's as_of pattern)."""
        raise NotImplementedError
