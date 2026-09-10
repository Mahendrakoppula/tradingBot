import pandas as pd

from market_state.classifier import MarketState
from market_state.liquidity import LiquidityState
from market_state.momentum import MomentumState
from market_state.structure import StructureState
from market_state.volatility import VolatilityState
from strategies.base import Signal, Strategy
from strategies.portfolio import generate_candidate_signals


class _AlwaysSignalsStrategy(Strategy):
    name = "always"
    eligible_regimes = frozenset({"TRENDING_UP"})

    def generate(self, df, market_state, mtf=None):
        return Signal(self.name, "CE", 0.9, "always fires when eligible")


class _NeverSignalsStrategy(Strategy):
    name = "never"
    eligible_regimes = frozenset({"TRENDING_UP"})

    def generate(self, df, market_state, mtf=None):
        return None


TRENDING_UP = MarketState(
    structure=StructureState("UPTREND"), volatility=VolatilityState("NORMAL", 1.0, 0.5),
    momentum=MomentumState("WEAK_UP", 0.01, 0.5), liquidity=LiquidityState("NORMAL", 1.0), regime="TRENDING_UP",
)
RANGING = MarketState(
    structure=StructureState("RANGE"), volatility=VolatilityState("NORMAL", 1.0, 0.5),
    momentum=MomentumState("FLAT", 0.0, 0.5), liquidity=LiquidityState("NORMAL", 1.0), regime="RANGING",
)
DF = pd.DataFrame({"timestamp": pd.bdate_range("2026-01-01", periods=3), "open": [100] * 3,
                    "high": [101] * 3, "low": [99] * 3, "close": [100] * 3, "volume": [1000] * 3})


def test_only_eligible_strategies_are_evaluated():
    strategies = [_AlwaysSignalsStrategy(), _NeverSignalsStrategy()]
    signals = generate_candidate_signals(DF, RANGING, strategies=strategies)
    assert signals == []  # neither strategy is eligible in RANGING


def test_eligible_strategies_that_return_none_are_excluded():
    strategies = [_AlwaysSignalsStrategy(), _NeverSignalsStrategy()]
    signals = generate_candidate_signals(DF, TRENDING_UP, strategies=strategies)
    assert len(signals) == 1
    assert signals[0].strategy_name == "always"


def test_default_strategy_list_runs_without_error():
    signals = generate_candidate_signals(DF, RANGING)
    assert isinstance(signals, list)
