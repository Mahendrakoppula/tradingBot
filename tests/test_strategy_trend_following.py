import pandas as pd

from features.mtf import MTFAlignment
from market_state.classifier import MarketState
from market_state.liquidity import LiquidityState
from market_state.momentum import MomentumState
from market_state.structure import StructureState
from market_state.volatility import VolatilityState
from strategies.trend_following import TrendFollowingStrategy

STRATEGY = TrendFollowingStrategy()
DF = pd.DataFrame({"timestamp": pd.bdate_range("2026-01-01", periods=5), "open": [100] * 5,
                    "high": [101] * 5, "low": [99] * 5, "close": [100] * 5, "volume": [1000] * 5})


def _state(regime: str, momentum_label: str = "WEAK_UP") -> MarketState:
    return MarketState(
        structure=StructureState("UPTREND" if regime == "TRENDING_UP" else "RANGE"),
        volatility=VolatilityState("NORMAL", 1.0, 0.5),
        momentum=MomentumState(momentum_label, 0.01, 0.5),
        liquidity=LiquidityState("NORMAL", 1.0),
        regime=regime,
    )


def test_not_eligible_outside_trending_regimes():
    assert STRATEGY.generate(DF, _state("RANGING")) is None
    assert STRATEGY.generate(DF, _state("VOLATILE")) is None
    assert STRATEGY.generate(DF, _state("UNKNOWN")) is None


def test_trending_up_gives_a_ce_signal():
    signal = STRATEGY.generate(DF, _state("TRENDING_UP"))
    assert signal is not None
    assert signal.direction == "CE"


def test_trending_down_gives_a_pe_signal():
    signal = STRATEGY.generate(DF, _state("TRENDING_DOWN", momentum_label="WEAK_DOWN"))
    assert signal is not None
    assert signal.direction == "PE"


def test_strong_momentum_increases_confidence_over_weak():
    weak = STRATEGY.generate(DF, _state("TRENDING_UP", momentum_label="WEAK_UP"))
    strong = STRATEGY.generate(DF, _state("TRENDING_UP", momentum_label="STRONG_UP"))
    assert strong.confidence > weak.confidence


def test_mtf_agreement_increases_confidence():
    no_mtf = STRATEGY.generate(DF, _state("TRENDING_UP"))
    agreeing_mtf = MTFAlignment(per_timeframe_regime={}, aligned_direction="UP", alignment_score=1.0, conflicting=False)
    with_mtf = STRATEGY.generate(DF, _state("TRENDING_UP"), mtf=agreeing_mtf)
    assert with_mtf.confidence > no_mtf.confidence


def test_conflicting_mtf_suppresses_the_signal():
    disagreeing_mtf = MTFAlignment(per_timeframe_regime={}, aligned_direction="DOWN", alignment_score=1.0, conflicting=True)
    signal = STRATEGY.generate(DF, _state("TRENDING_UP"), mtf=disagreeing_mtf)
    assert signal is None


def test_unknown_mtf_direction_does_not_suppress_the_signal():
    unknown_mtf = MTFAlignment(per_timeframe_regime={}, aligned_direction="UNKNOWN", alignment_score=0.0, conflicting=False)
    signal = STRATEGY.generate(DF, _state("TRENDING_UP"), mtf=unknown_mtf)
    assert signal is not None


def test_confidence_is_never_above_one():
    strong_and_aligned_mtf = MTFAlignment(per_timeframe_regime={}, aligned_direction="UP", alignment_score=1.0, conflicting=False)
    signal = STRATEGY.generate(DF, _state("TRENDING_UP", momentum_label="STRONG_UP"), mtf=strong_and_aligned_mtf)
    assert signal.confidence <= 1.0
