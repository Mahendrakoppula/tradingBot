import pytest

from trading_bot.support_resistance import classic_pivot_points, cluster_levels, find_swing_points


def _candle(high, low):
    return {"high": high, "low": low}


def test_find_swing_points_detects_high_and_low():
    # index 3 is a clear swing high (10 > all of 5,6,7 before and 6,5,4 after)
    # index 7 is a clear swing low (1 < all of 4,3,2 before and 2,3,4 after)
    highs_lows = [
        (5, 3), (6, 4), (7, 5), (10, 8), (6, 4), (5, 3), (4, 2),
        (2, 1), (3, 2), (4, 3), (5, 4),
    ]
    candles = [_candle(h, l) for h, l in highs_lows]
    points = find_swing_points(candles, left=3, right=3)
    kinds_by_index = {p.index: p.kind for p in points}
    assert kinds_by_index.get(3) == "high"
    assert kinds_by_index.get(7) == "low"


def test_find_swing_points_needs_enough_candles():
    candles = [_candle(10, 8), _candle(11, 9)]
    assert find_swing_points(candles, left=3, right=3) == []


def test_cluster_levels_merges_close_points_and_counts_touches():
    from trading_bot.support_resistance import SwingPoint

    points = [
        SwingPoint(index=1, price=100.0, kind="high"),
        SwingPoint(index=5, price=100.3, kind="high"),  # within 0.5% of 100.0
        SwingPoint(index=9, price=110.0, kind="high"),  # far away -> separate level
    ]
    levels = cluster_levels(points, tolerance_pct=0.5)
    high_levels = sorted([lv for lv in levels if lv.kind == "high"], key=lambda lv: lv.price)
    assert len(high_levels) == 2
    assert high_levels[0].touches == 2
    assert high_levels[0].last_touched_index == 5
    assert high_levels[1].touches == 1
    assert high_levels[1].price == pytest.approx(110.0)


def test_cluster_levels_does_not_drift_through_chained_small_steps():
    from trading_bot.support_resistance import SwingPoint

    # Each point is 0.4% above the last - under the 0.5% tolerance pairwise,
    # but the chain spans ~7.9% end to end. Anchoring to the group's first
    # point (not the previous point) must split this into multiple levels,
    # not merge all 20 into one.
    points = []
    price = 100.0
    for i in range(20):
        points.append(SwingPoint(index=i, price=price, kind="high"))
        price *= 1.004
    levels = cluster_levels(points, tolerance_pct=0.5)
    assert len(levels) > 1
    for lv in levels:
        assert lv.touches < len(points)


def test_classic_pivot_points_formula():
    result = classic_pivot_points(prev_high=110, prev_low=90, prev_close=100)
    pp = (110 + 90 + 100) / 3
    assert result["pp"] == pytest.approx(pp)
    assert result["r1"] == pytest.approx(2 * pp - 90)
    assert result["s1"] == pytest.approx(2 * pp - 110)
    assert result["r2"] == pytest.approx(pp + 20)
    assert result["s2"] == pytest.approx(pp - 20)
