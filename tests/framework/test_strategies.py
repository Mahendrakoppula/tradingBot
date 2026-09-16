from research.framework.market_structure import StructureEvent
from research.framework.regime import Regime
from research.framework.scoring import ScoringInputs
from research.framework.strategies import NoTrade, Signal, breakout, mean_reversion, momentum, trend_following


def _regime(direction="none", strength=None, vol_bucket="normal"):
    return Regime(
        trend_direction=direction, trend_strength=strength, volatility_bucket=vol_bucket,
        plus_di=None, minus_di=None, atr=10.0, atr_percentile=50.0,
    )


# --- trend_following ---

def test_trend_following_no_trend_is_no_trade():
    result = trend_following(_regime(direction="none"), ScoringInputs())
    assert isinstance(result, NoTrade)
    assert result.reason["rejected_because"] == "no_established_trend"


def test_trend_following_strong_aligned_trend_is_a_signal():
    regime = _regime(direction="up", strength=45.0)
    inputs = ScoringInputs(
        rsi=65.0, macd_histogram=1.0, relative_volume=2.0,
        recent_structure_events=[StructureEvent(kind="bos_up", index=1, price=100.0)],
        entry_trigger_confirmed=True,
    )
    result = trend_following(regime, inputs)
    assert isinstance(result, Signal)
    assert result.direction == "up"


def test_trend_following_weak_score_is_no_trade():
    regime = _regime(direction="up", strength=5.0)
    result = trend_following(regime, ScoringInputs())
    assert isinstance(result, NoTrade)
    assert result.reason["rejected_because"] == "score_below_threshold"


# --- breakout ---

def test_breakout_requires_trigger():
    regime = _regime(direction="up", strength=30.0)
    inputs = ScoringInputs(entry_trigger_confirmed=False, relative_volume=3.0)
    result = breakout(regime, inputs, "up")
    assert isinstance(result, NoTrade)
    assert result.reason["rejected_because"] == "no_breakout_trigger_this_bar"


def test_breakout_requires_volume():
    regime = _regime(direction="up", strength=30.0)
    inputs = ScoringInputs(entry_trigger_confirmed=True, relative_volume=1.0)
    result = breakout(regime, inputs, "up")
    assert isinstance(result, NoTrade)
    assert result.reason["rejected_because"] == "insufficient_volume_confirmation"


def test_breakout_confirmed_with_volume_is_a_signal():
    regime = _regime(direction="up", strength=30.0, vol_bucket="high")
    inputs = ScoringInputs(
        entry_trigger_confirmed=True, relative_volume=3.0, rsi=60.0, macd_histogram=0.5,
        recent_structure_events=[StructureEvent(kind="bos_up", index=1, price=100.0)],
    )
    result = breakout(regime, inputs, "up")
    assert isinstance(result, Signal)
    assert result.direction == "up"


# --- mean_reversion ---

def test_mean_reversion_requires_rsi():
    result = mean_reversion(_regime(), ScoringInputs(rsi=None))
    assert isinstance(result, NoTrade)
    assert result.reason["rejected_because"] == "no_rsi_reading"


def test_mean_reversion_rsi_not_extreme_is_no_trade():
    result = mean_reversion(_regime(), ScoringInputs(rsi=50.0))
    assert isinstance(result, NoTrade)
    assert result.reason["rejected_because"] == "rsi_not_at_extreme"


def test_mean_reversion_refuses_to_fade_a_strong_trend():
    # RSI overbought (fade direction = down) but a strong UP trend is running -
    # fading into a strong trend is exactly what this gate should refuse.
    regime = _regime(direction="up", strength=40.0)
    result = mean_reversion(regime, ScoringInputs(rsi=75.0), max_adx_to_fade=25.0)
    assert isinstance(result, NoTrade)
    assert result.reason["rejected_because"] == "trend_too_strong_to_fade"


def test_mean_reversion_overbought_in_range_is_a_fade_signal():
    # No trend to fight (direction="none"), low-volatility range (mean
    # reversion's preferred regime), strong volume/structure confirmation -
    # note RSI itself only partially confirms the "down" fade (that's
    # inherent to mean reversion: the fade direction is, by construction,
    # against the very momentum reading that triggered it).
    regime = _regime(direction="none", vol_bucket="low")
    inputs = ScoringInputs(
        rsi=80.0, macd_histogram=-0.5, relative_volume=2.0,
        recent_structure_events=[StructureEvent(kind="bos_down", index=1, price=100.0)],
        entry_trigger_confirmed=True,
    )
    result = mean_reversion(regime, inputs)
    assert isinstance(result, Signal)
    assert result.direction == "down"


# --- momentum ---

def test_momentum_requires_rsi_and_macd():
    result = momentum(_regime(), ScoringInputs(rsi=60.0, macd_histogram=None), "up")
    assert isinstance(result, NoTrade)
    assert result.reason["rejected_because"] == "insufficient_momentum_data"


def test_momentum_requires_agreement():
    result = momentum(_regime(), ScoringInputs(rsi=60.0, macd_histogram=-0.5), "up")
    assert isinstance(result, NoTrade)
    assert result.reason["rejected_because"] == "momentum_not_confirmed"


def test_momentum_confirmed_is_a_signal():
    regime = _regime(direction="up", strength=30.0)
    inputs = ScoringInputs(
        rsi=65.0, macd_histogram=1.0, relative_volume=2.0,
        recent_structure_events=[StructureEvent(kind="bos_up", index=1, price=100.0)],
    )
    result = momentum(regime, inputs, "up")
    assert isinstance(result, Signal)
    assert result.direction == "up"
