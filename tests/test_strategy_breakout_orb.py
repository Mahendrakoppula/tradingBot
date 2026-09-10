import pandas as pd

from market_state.classifier import MarketState
from market_state.liquidity import LiquidityState
from market_state.momentum import MomentumState
from market_state.structure import StructureState
from market_state.volatility import VolatilityState
from strategies.breakout_orb import OpeningRangeBreakoutStrategy

STRATEGY = OpeningRangeBreakoutStrategy(opening_range_bars=5)

RANGING = MarketState(
    structure=StructureState("RANGE"), volatility=VolatilityState("NORMAL", 1.0, 0.5),
    momentum=MomentumState("FLAT", 0.0, 0.5), liquidity=LiquidityState("NORMAL", 1.0), regime="RANGING",
)
UNKNOWN = MarketState(
    structure=StructureState("INSUFFICIENT_DATA"), volatility=VolatilityState("INSUFFICIENT_DATA", None, None),
    momentum=MomentumState("INSUFFICIENT_DATA", None, None), liquidity=LiquidityState("INSUFFICIENT_DATA", None),
    regime="UNKNOWN",
)


def _session_df(closes_after_range: list[float], range_high: float = 101.0, range_low: float = 99.0) -> pd.DataFrame:
    """5 opening-range bars (high=101/low=99, close=100) followed by the
    given post-range closes, all on the same trading day."""
    n_range = 5
    ts = pd.date_range("2026-01-05 09:15", periods=n_range + len(closes_after_range), freq="1min")
    highs = [range_high] * n_range + [c + 0.1 for c in closes_after_range]
    lows = [range_low] * n_range + [c - 0.1 for c in closes_after_range]
    closes = [100.0] * n_range + closes_after_range
    return pd.DataFrame({"timestamp": ts, "open": closes, "high": highs, "low": lows, "close": closes, "volume": [1000] * len(ts)})


def test_not_eligible_when_regime_unknown():
    df = _session_df([102.0])
    assert STRATEGY.generate(df, UNKNOWN) is None


def test_no_signal_while_still_inside_the_opening_range():
    df = _session_df([])  # only the 5 range-defining bars exist so far
    assert STRATEGY.generate(df, RANGING) is None


def test_breakout_above_range_high_gives_ce():
    df = _session_df([100.5, 101.5])  # last close breaks above range_high=101
    signal = STRATEGY.generate(df, RANGING)
    assert signal is not None
    assert signal.direction == "CE"


def test_breakout_below_range_low_gives_pe():
    df = _session_df([99.5, 98.5])  # last close breaks below range_low=99
    signal = STRATEGY.generate(df, RANGING)
    assert signal is not None
    assert signal.direction == "PE"


def test_close_still_inside_the_range_gives_no_signal():
    df = _session_df([100.2, 100.5])  # never left [99, 101]
    assert STRATEGY.generate(df, RANGING) is None


def test_only_uses_todays_bars_for_the_opening_range():
    day1 = _session_df([100.5, 101.5])  # day1 breaks out
    day2_ts = pd.date_range("2026-01-06 09:15", periods=7, freq="1min")
    # day2's own opening range is 200-210, and its last close (205) is
    # comfortably inside it - day1's range/breakout must not leak in.
    day2 = pd.DataFrame({
        "timestamp": day2_ts, "open": [205.0] * 7, "high": [210.0] * 5 + [205.1, 205.1],
        "low": [200.0] * 5 + [204.9, 204.9], "close": [205.0] * 7, "volume": [1000] * 7,
    })
    combined = pd.concat([day1, day2], ignore_index=True)
    assert STRATEGY.generate(combined, RANGING) is None
