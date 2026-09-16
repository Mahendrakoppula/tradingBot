import datetime as dt

import pytest

from trading_bot.engine.structure import (
    StructureEvent,
    day_levels,
    detect_mss,
    failed_breakout,
    find_structure_events,
    gap,
    liquidity_sweep,
    nearest_levels,
    session_levels,
    visible_swings,
)
from trading_bot.support_resistance import SwingPoint as S


def _c(o, h, l, c, **extra):
    return {"open": o, "high": h, "low": l, "close": c, **extra}


# --- visibility / lookahead ------------------------------------------------


def test_visible_swings_excludes_unconfirmed_points():
    # clear swing high at index 3 (10 > neighbours), needs right=3 confirming candles
    highs = [5, 6, 7, 10, 6, 5, 4, 3]
    candles = [_c(h - 1, h, h - 2, h - 1) for h in highs]
    assert [p.index for p in visible_swings(candles, i=5, left=3, right=3)] == []  # only 2 candles after idx 3
    assert [p.index for p in visible_swings(candles, i=6, left=3, right=3)] == [3]
    assert visible_swings(candles, i=-1) == []


# --- structure events (promoted; behaviour preserved) ------------------------


def test_uptrend_produces_bos_up_then_choch_down_on_lower_low():
    pts = [S(0, 100, "low"), S(1, 110, "high"), S(2, 105, "low"), S(3, 120, "high"), S(4, 115, "low"),
           S(5, 130, "high"), S(6, 100, "low")]
    kinds = [e.kind for e in find_structure_events(pts)]
    assert kinds == ["bos_up", "bos_up", "choch_down"]


def test_downtrend_then_higher_high_is_choch_up():
    pts = [S(0, 130, "high"), S(1, 100, "low"), S(2, 120, "high"), S(3, 90, "low"), S(4, 125, "high")]
    kinds = [e.kind for e in find_structure_events(pts)]
    assert kinds == ["bos_down", "choch_up"]


def test_research_shim_still_exports():
    from research.framework.market_structure import find_structure_events as shim
    assert shim is find_structure_events


# --- MSS -----------------------------------------------------------------


def test_mss_requires_displacement_after_choch():
    events = [StructureEvent("choch_up", index=2, price=100)]
    small = [_c(100, 101, 99, 100.5)] * 5
    assert detect_mss(events, small, i=4, atr_value=2.0) is None
    big = small[:3] + [_c(100, 104, 99.5, 103.6)] + small[:1]  # body 3.6 vs ATR 2 -> displacement, bullish
    assert detect_mss(events, big, i=4, atr_value=2.0) == "mss_up"
    assert detect_mss(events, big, i=4, atr_value=None) is None
    assert detect_mss([StructureEvent("bos_up", 2, 100)], big, 4, 2.0) is None  # BOS isn't a shift


# --- levels ----------------------------------------------------------------


def _daily(day, h, l, c):
    return _c(c, h, l, c, date=day)


def test_day_levels_previous_day_and_previous_week():
    # ISO week 37 (Sep 7-11) then week 38 (Sep 14-16); "today" is Sep 17 so bars end at Sep 16
    days = [dt.date(2026, 9, d) for d in (7, 8, 9, 10, 11, 14, 15, 16)]
    highs = [100, 105, 110, 108, 107, 120, 125, 122]
    lows = [90, 95, 100, 98, 97, 110, 115, 118]
    candles = [_daily(d, h, l, (h + l) / 2) for d, h, l in zip(days, highs, lows)]
    lv = day_levels(candles)
    assert (lv.pdh, lv.pdl, lv.pdc) == (122, 118, 120)
    assert lv.pwh == 110 and lv.pwl == 90  # week 37 extremes


def test_day_levels_without_prior_week():
    lv = day_levels([_daily(dt.date(2026, 9, 16), 122, 118, 120)])
    assert lv.pdh == 122 and lv.pwh is None and lv.pwl is None
    assert day_levels([]) is None


def test_session_levels_and_opening_range_completion():
    bars = [_c(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(10)]
    partial = session_levels(bars, opening_range_bars=15)
    assert partial.or_complete is False and partial.or_high is None
    assert partial.session_high == 110 and partial.session_low == 99
    full = session_levels(bars, opening_range_bars=5)
    assert full.or_complete is True and full.or_high == 105 and full.or_low == 99


# --- gaps / failed breakouts / sweeps --------------------------------------


def test_gap_detection_and_threshold():
    g = gap(prev_close=100.0, today_open=101.0, atr_value=2.0)
    assert g.direction == "up" and g.pct == pytest.approx(1.0) and g.atr_multiple == pytest.approx(0.5)
    assert gap(100.0, 100.05, 2.0) is None  # below 0.1% default
    assert gap(100.0, 98.0, None).atr_multiple is None


def test_failed_breakout():
    candles = [_c(0, 0, 0, 98), _c(0, 0, 0, 102), _c(0, 0, 0, 103), _c(0, 0, 0, 99)]
    assert failed_breakout(candles, level=100, i=3, direction="up") is True
    assert failed_breakout(candles, level=100, i=2, direction="up") is False  # still above
    down = [_c(0, 0, 0, 102), _c(0, 0, 0, 97), _c(0, 0, 0, 101)]
    assert failed_breakout(down, level=100, i=2, direction="down") is True
    with pytest.raises(ValueError):
        failed_breakout(candles, 100, 3, "sideways")


def test_liquidity_sweep_picks_largest_excess_and_respects_min():
    levels = [("PDH", 110.0), ("session_high", 108.0), ("PDL", 90.0)]
    c = _c(105, 112, 104, 107)  # wicks through 110 and 108, closes back under both
    s = liquidity_sweep(c, levels, atr_value=4.0)
    assert s.level_name == "session_high" and s.side == "above" and s.excess == pytest.approx(4.0)
    tiny = _c(109, 110.1, 108.5, 109)  # only PDH is pierced, by 0.1 = 0.025 ATR
    assert liquidity_sweep(tiny, levels, atr_value=4.0) is None
    below = _c(95, 96, 88, 94)
    assert liquidity_sweep(below, levels, atr_value=4.0).side == "below"
    assert liquidity_sweep(_c(100, 101, 99, 100), levels, 4.0) is None


# --- distances ---------------------------------------------------------------


def test_nearest_levels():
    r = nearest_levels(100.0, [("R1", 104.0), ("R2", 110.0), ("S1", 97.0), ("S2", 80.0)], atr_value=2.0)
    assert r.nearest_above.name == "R1" and r.nearest_above.distance == 4.0 and r.nearest_above.distance_atr == 2.0
    assert r.nearest_below.name == "S1" and r.nearest_below.distance == -3.0
    assert [d.name for d in r.all] == ["S1", "R1", "R2", "S2"]
    empty = nearest_levels(100.0, [], None)
    assert empty.nearest_above is None and empty.nearest_below is None and empty.all == []
