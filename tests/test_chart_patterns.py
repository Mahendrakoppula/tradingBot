from trading_bot.chart_patterns import (
    detect_breakout,
    detect_double_top_bottom,
    detect_ma_crossover,
    detect_trend_structure,
)
from trading_bot.support_resistance import SwingPoint


def test_detect_ma_crossover_golden_cross():
    fast = [9, 9.5, 10.5]
    slow = [10, 10, 10]
    assert detect_ma_crossover(fast, slow) == "golden_cross"


def test_detect_ma_crossover_death_cross():
    fast = [11, 10.5, 9.5]
    slow = [10, 10, 10]
    assert detect_ma_crossover(fast, slow) == "death_cross"


def test_detect_ma_crossover_no_cross():
    fast = [11, 11.5, 12]
    slow = [10, 10, 10]
    assert detect_ma_crossover(fast, slow) is None


def test_detect_ma_crossover_none_values_no_crash():
    assert detect_ma_crossover([None, 9.5], [None, 10]) is None


def test_detect_breakout_up_and_down():
    candles = [{"close": 100}, {"close": 105}]
    assert detect_breakout(candles, level=102, direction="up") is True
    assert detect_breakout(candles, level=110, direction="up") is False
    assert detect_breakout(candles, level=102, direction="down") is False


def test_detect_breakout_invalid_direction_raises():
    try:
        detect_breakout([{"close": 100}], level=90, direction="sideways")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_detect_trend_structure_uptrend():
    points = [
        SwingPoint(index=0, price=100, kind="low"),
        SwingPoint(index=1, price=110, kind="high"),
        SwingPoint(index=2, price=105, kind="low"),
        SwingPoint(index=3, price=120, kind="high"),
    ]
    assert detect_trend_structure(points) == "uptrend"


def test_detect_trend_structure_downtrend():
    points = [
        SwingPoint(index=0, price=120, kind="high"),
        SwingPoint(index=1, price=100, kind="low"),
        SwingPoint(index=2, price=110, kind="high"),
        SwingPoint(index=3, price=90, kind="low"),
    ]
    assert detect_trend_structure(points) == "downtrend"


def test_detect_trend_structure_insufficient_points():
    assert detect_trend_structure([SwingPoint(index=0, price=100, kind="high")]) is None


def test_detect_double_top():
    points = [
        SwingPoint(index=0, price=100.0, kind="high"),
        SwingPoint(index=1, price=90.0, kind="low"),
        SwingPoint(index=2, price=100.3, kind="high"),  # within 0.5% of first high
    ]
    assert detect_double_top_bottom(points, tolerance_pct=0.5) == "double_top"


def test_detect_double_bottom():
    points = [
        SwingPoint(index=0, price=100.0, kind="low"),
        SwingPoint(index=1, price=110.0, kind="high"),
        SwingPoint(index=2, price=100.4, kind="low"),
    ]
    assert detect_double_top_bottom(points, tolerance_pct=0.5) == "double_bottom"


def test_detect_double_top_bottom_no_pattern():
    points = [
        SwingPoint(index=0, price=100.0, kind="high"),
        SwingPoint(index=1, price=90.0, kind="low"),
        SwingPoint(index=2, price=120.0, kind="high"),  # far away, not a double top
    ]
    assert detect_double_top_bottom(points, tolerance_pct=0.5) is None
