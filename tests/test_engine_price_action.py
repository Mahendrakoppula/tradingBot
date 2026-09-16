import pytest

from trading_bot.engine.price_action import (
    anatomy,
    detect_patterns,
    is_displacement,
    is_doji,
    is_engulfing,
    is_inside_bar,
    is_pin_bar,
    is_rejection,
    is_star,
)


def _c(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def test_anatomy_basic_bullish():
    a = anatomy(_c(100, 110, 95, 108), atr_value=10.0)
    assert a.range == 15 and a.body == 8 and a.upper_wick == 2 and a.lower_wick == 5
    assert a.body_ratio == pytest.approx(8 / 15)
    assert a.close_loc == pytest.approx(13 / 15)
    assert a.rel_size == pytest.approx(1.5)
    assert a.displacement == pytest.approx(0.8)
    assert a.bullish is True


def test_anatomy_zero_range_is_safe():
    a = anatomy(_c(100, 100, 100, 100), atr_value=None)
    assert a.body_ratio == 0 and a.close_loc == 0.5 and a.rel_size is None and a.displacement is None


def test_doji_and_pin_bars():
    assert is_doji(anatomy(_c(100, 105, 95, 100.5), 1)) is True
    assert is_doji(anatomy(_c(100, 105, 95, 104), 1)) is False
    bullish_pin = anatomy(_c(100, 101, 90, 100.5), 1)  # long lower wick, closes near high
    assert is_pin_bar(bullish_pin) == "bullish_pin"
    bearish_pin = anatomy(_c(100, 110, 99, 99.5), 1)
    assert is_pin_bar(bearish_pin) == "bearish_pin"
    assert is_pin_bar(anatomy(_c(100, 110, 90, 109), 1)) is None  # big body


def test_engulfing_both_directions_and_none():
    assert is_engulfing(_c(105, 106, 99, 100), _c(99, 108, 98, 106)) == "bullish_engulfing"
    assert is_engulfing(_c(100, 106, 99, 105), _c(106, 107, 97, 99)) == "bearish_engulfing"
    assert is_engulfing(_c(100, 106, 99, 105), _c(105, 110, 104, 109)) is None  # same direction


def test_inside_bar():
    assert is_inside_bar(_c(100, 110, 90, 105), _c(102, 108, 95, 103)) is True
    assert is_inside_bar(_c(100, 110, 90, 105), _c(102, 111, 95, 103)) is False


def test_displacement_requires_big_body_relative_to_atr():
    assert is_displacement(anatomy(_c(100, 116, 99, 115), atr_value=10.0)) is True
    assert is_displacement(anatomy(_c(100, 105, 99, 104), atr_value=10.0)) is False
    assert is_displacement(anatomy(_c(100, 116, 99, 115), atr_value=None)) is False


def test_rejection_labels():
    assert is_rejection(anatomy(_c(100, 120, 99, 101), 1)) == "rejection_of_high"
    assert is_rejection(anatomy(_c(100, 101, 80, 99), 1)) == "rejection_of_low"
    assert is_rejection(anatomy(_c(100, 108, 97, 105), 1)) is None  # wicks 3/3 on an 11 range


def test_morning_and_evening_star():
    c1, c2, c3 = _c(110, 111, 99, 100), _c(100, 101, 99, 100.2), _c(100, 112, 99, 111)
    assert is_star(c1, c2, c3, atr_value=None) == "morning_star"
    c1, c2, c3 = _c(100, 111, 99, 110), _c(110, 111, 109, 110.2), _c(110, 111, 98, 99)
    assert is_star(c1, c2, c3, atr_value=None) == "evening_star"
    assert is_star(c1, _c(110, 120, 100, 119), c3, atr_value=None) is None  # middle not small


def test_detect_patterns_orders_and_dedupes():
    candles = [
        _c(110, 111, 99, 100),
        _c(100.2, 101, 99, 100),  # tiny bearish pause
        _c(100, 112, 99, 111),  # morning star completes here; also engulfs c2's body
    ]
    labels = detect_patterns(candles, 2, atr_value=5.0)
    assert labels[0] == "morning_star"
    assert "bullish_engulfing" in labels
    assert "displacement" in labels  # body 11 vs ATR 5
    assert detect_patterns(candles, 99, 1.0) == []
