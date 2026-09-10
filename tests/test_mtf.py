import pandas as pd

from features.mtf import classify_mtf, fuse_states
from market_state.classifier import MarketState
from market_state.liquidity import LiquidityState
from market_state.momentum import MomentumState
from market_state.structure import StructureState
from market_state.volatility import VolatilityState
from tests.test_market_state_structure import _zigzag_prices


def _ms(regime: str) -> MarketState:
    """Minimal MarketState stand-in - fuse_states only reads .regime, so
    the sub-states just need to exist, not be realistic."""
    return MarketState(
        structure=StructureState("UPTREND" if regime == "TRENDING_UP" else "RANGE"),
        volatility=VolatilityState("NORMAL", 1.0, 0.5),
        momentum=MomentumState("FLAT", 0.0, 0.5),
        liquidity=LiquidityState("NORMAL", 1.0),
        regime=regime,
    )


def test_all_timeframes_trending_up_is_fully_aligned():
    states = {tf: _ms("TRENDING_UP") for tf in ["ONE_MINUTE", "FIVE_MINUTE", "ONE_HOUR", "ONE_DAY"]}
    result = fuse_states(states)
    assert result.aligned_direction == "UP"
    assert result.alignment_score == 1.0
    assert result.conflicting is False


def test_lower_timeframe_conflicts_with_higher_timeframe():
    states = {"ONE_MINUTE": _ms("TRENDING_DOWN"), "ONE_HOUR": _ms("TRENDING_UP"), "ONE_DAY": _ms("TRENDING_UP")}
    result = fuse_states(states)
    assert result.aligned_direction == "UP"
    assert result.alignment_score == 2 / 3
    assert result.conflicting is True


def test_ranging_timeframes_are_not_votes_either_way():
    states = {"ONE_MINUTE": _ms("RANGING"), "ONE_HOUR": _ms("TRENDING_UP")}
    result = fuse_states(states)
    assert result.aligned_direction == "UP"
    assert result.alignment_score == 1.0
    assert result.conflicting is False


def test_no_directional_timeframes_is_unknown():
    states = {"ONE_MINUTE": _ms("RANGING"), "ONE_HOUR": _ms("VOLATILE")}
    result = fuse_states(states)
    assert result.aligned_direction == "UNKNOWN"
    assert result.alignment_score == 0.0


def test_equal_up_and_down_votes_is_mixed():
    states = {"ONE_MINUTE": _ms("TRENDING_UP"), "ONE_HOUR": _ms("TRENDING_DOWN")}
    result = fuse_states(states)
    assert result.aligned_direction == "MIXED"
    assert result.alignment_score == 0.5
    assert result.conflicting is True


def _df_from_prices(prices: list[float]) -> pd.DataFrame:
    n = len(prices)
    ts = pd.date_range("2026-01-01 09:15", periods=n, freq="1min")
    return pd.DataFrame({
        "timestamp": ts, "open": prices, "high": [p + 0.05 for p in prices], "low": [p - 0.05 for p in prices],
        "close": prices, "volume": [1000] * n,
    })


def test_classify_mtf_end_to_end_from_real_ohlcv():
    # Flat continuation must pick up exactly where the zigzag itself
    # leaves off (its actual last value, not a round number) - anything
    # else creates a spurious discontinuity that can register as its own
    # swing point and corrupt trend detection right at the boundary.
    up_zigzag = _zigzag_prices([100, 110, 105, 120, 112, 130])
    up_prices = up_zigzag + [up_zigzag[-1]] * 30
    range_zigzag = _zigzag_prices([100, 110, 100, 110, 100, 110])
    range_prices = range_zigzag + [range_zigzag[-1]] * 30
    result = classify_mtf({"ONE_MINUTE": _df_from_prices(up_prices), "ONE_DAY": _df_from_prices(range_prices)})
    assert result.per_timeframe_regime["ONE_MINUTE"] == "TRENDING_UP"
    assert result.per_timeframe_regime["ONE_DAY"] == "RANGING"
    assert result.aligned_direction == "UP"
    assert result.alignment_score == 1.0
