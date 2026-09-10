import pandas as pd

from market_state.classifier import MarketState
from market_state.liquidity import LiquidityState
from market_state.momentum import MomentumState
from market_state.structure import StructureState
from market_state.volatility import VolatilityState
from strategies.mean_reversion import MeanReversionStrategy

STRATEGY = MeanReversionStrategy(lookback=20, edge_threshold=0.15)

RANGING = MarketState(
    structure=StructureState("RANGE"), volatility=VolatilityState("NORMAL", 1.0, 0.5),
    momentum=MomentumState("FLAT", 0.0, 0.5), liquidity=LiquidityState("NORMAL", 1.0), regime="RANGING",
)
TRENDING_UP = MarketState(
    structure=StructureState("UPTREND"), volatility=VolatilityState("NORMAL", 1.0, 0.5),
    momentum=MomentumState("STRONG_UP", 0.05, 0.9), liquidity=LiquidityState("NORMAL", 1.0), regime="TRENDING_UP",
)


def _df(highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    ts = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"timestamp": ts, "open": closes, "high": highs, "low": lows, "close": closes, "volume": [1000] * n})


def test_not_eligible_outside_ranging_regime():
    df = _df([110] * 20, [100] * 20, [101] * 20)
    assert STRATEGY.generate(df, TRENDING_UP) is None


def test_too_little_history_gives_no_signal():
    df = _df([110] * 5, [100] * 5, [101] * 5)
    assert STRATEGY.generate(df, RANGING) is None


def test_close_near_range_low_gives_ce():
    highs = [110] * 20
    lows = [100] * 20
    closes = [105] * 19 + [100.5]  # sits right at the bottom of the 100-110 range
    df = _df(highs, lows, closes)
    signal = STRATEGY.generate(df, RANGING)
    assert signal is not None
    assert signal.direction == "CE"


def test_close_near_range_high_gives_pe():
    highs = [110] * 20
    lows = [100] * 20
    closes = [105] * 19 + [109.5]  # sits right at the top of the 100-110 range
    df = _df(highs, lows, closes)
    signal = STRATEGY.generate(df, RANGING)
    assert signal is not None
    assert signal.direction == "PE"


def test_close_in_the_middle_of_the_range_gives_no_signal():
    highs = [110] * 20
    lows = [100] * 20
    closes = [105] * 20  # dead center
    df = _df(highs, lows, closes)
    assert STRATEGY.generate(df, RANGING) is None


def test_flat_range_of_zero_width_gives_no_signal():
    df = _df([100] * 20, [100] * 20, [100] * 20)
    assert STRATEGY.generate(df, RANGING) is None
