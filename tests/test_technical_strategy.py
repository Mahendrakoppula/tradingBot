from unittest.mock import patch

import pytest

import trading_bot.technical_strategy as ts
from trading_bot.support_resistance import PriceLevel


def _candles(n, close=100.0, volume=1000, high=None, low=None):
    high = high if high is not None else close + 1
    low = low if low is not None else close - 1
    return [{"open": close, "high": high, "low": low, "close": close, "volume": volume} for _ in range(n)]


def _daily_candles(n, close=100.0):
    return [{"open": close, "high": close + 1, "low": close - 1, "close": close} for _ in range(n)]


# --- candles_from_rows ---


def test_candles_from_rows_converts_shape():
    rows = [["2026-01-01 09:15", 100, 101, 99, 100.5, 1000]]
    assert ts.candles_from_rows(rows) == [
        {"time": "2026-01-01 09:15", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000}
    ]


def test_candles_from_rows_handles_none_or_empty():
    assert ts.candles_from_rows(None) == []
    assert ts.candles_from_rows([]) == []


# --- scalp_signal ---


def test_scalp_signal_not_enough_candles():
    option_type, reason = ts.scalp_signal(_candles(5), ema_fast=9, ema_slow=21, avg_volume_period=20, min_relative_volume=1.5)
    assert option_type is None
    assert "warmup" in reason


def test_scalp_signal_no_crossover_returns_none():
    with patch.object(ts, "detect_ma_crossover", return_value=None):
        option_type, reason = ts.scalp_signal(_candles(30), 9, 21, 20, 1.5)
    assert option_type is None


def test_scalp_signal_golden_cross_with_volume_and_vwap_confirms_ce():
    candles = _candles(29, close=100.0, volume=50)
    candles.append({"open": 100, "high": 111, "low": 99, "close": 110, "volume": 1000})
    with patch.object(ts, "detect_ma_crossover", return_value="golden_cross"):
        option_type, reason = ts.scalp_signal(candles, 9, 21, 20, 1.5)
    assert option_type == "CE"
    assert "volume confirmed" in reason


def test_scalp_signal_low_relative_volume_blocks_signal():
    candles = _candles(30, close=100.0, volume=100)
    with patch.object(ts, "detect_ma_crossover", return_value="golden_cross"):
        option_type, reason = ts.scalp_signal(candles, 9, 21, 20, 1.5)
    assert option_type is None
    assert "volume" in reason


def test_scalp_signal_no_volume_data_skips_volume_and_vwap_gates():
    candles = _candles(30, close=100.0, volume=0)
    with patch.object(ts, "detect_ma_crossover", return_value="golden_cross"):
        option_type, reason = ts.scalp_signal(candles, 9, 21, 20, 1.5)
    assert option_type == "CE"
    assert "no volume data" in reason


def test_scalp_signal_death_cross_no_volume_data_means_pe():
    candles = _candles(30, close=100.0, volume=0)
    with patch.object(ts, "detect_ma_crossover", return_value="death_cross"):
        option_type, reason = ts.scalp_signal(candles, 9, 21, 20, 1.5)
    assert option_type == "PE"


# --- intraday_signal ---


def test_intraday_signal_not_enough_bars():
    direction, reason = ts.intraday_signal(_candles(10), None, 20, 50, 14, 20, 1.5)
    assert direction is None
    assert "warmup" in reason


def test_intraday_signal_ema_cross_confirmed_by_volume_ce():
    bars = _candles(59, volume=100)
    bars.append({"open": 100, "high": 102, "low": 99, "close": 101, "volume": 500})
    with patch.object(ts, "detect_ma_crossover", return_value="golden_cross"), \
         patch.object(ts, "rsi", return_value=[50.0] * 60):
        direction, reason = ts.intraday_signal(bars, None, 20, 50, 14, 20, 1.5)
    assert direction == "CE"
    assert "EMA cross" in reason


def test_intraday_signal_rsi_extreme_blocks_long():
    bars = _candles(59, volume=100)
    bars.append({"open": 100, "high": 102, "low": 99, "close": 101, "volume": 500})
    with patch.object(ts, "detect_ma_crossover", return_value="golden_cross"), \
         patch.object(ts, "rsi", return_value=[75.0] * 60):
        direction, reason = ts.intraday_signal(bars, None, 20, 50, 14, 20, 1.5)
    assert direction is None


def test_intraday_signal_pivot_breakout_without_ema_cross():
    bars = _candles(59, volume=100)
    bars.append({"open": 100, "high": 151, "low": 99, "close": 150, "volume": 500})
    pivots = {"r1": 140.0, "s1": 90.0}
    with patch.object(ts, "detect_ma_crossover", return_value=None), \
         patch.object(ts, "rsi", return_value=[50.0] * 60):
        direction, reason = ts.intraday_signal(bars, pivots, 20, 50, 14, 20, 1.5)
    assert direction == "CE"
    assert "pivot" in reason


def test_intraday_signal_no_pivots_and_no_cross_means_no_signal():
    bars = _candles(59, volume=100)
    bars.append({"open": 100, "high": 102, "low": 99, "close": 101, "volume": 500})
    with patch.object(ts, "detect_ma_crossover", return_value=None), \
         patch.object(ts, "rsi", return_value=[50.0] * 60):
        direction, reason = ts.intraday_signal(bars, None, 20, 50, 14, 20, 1.5)
    assert direction is None


# --- swing_signal ---


def test_swing_signal_not_enough_history():
    direction, level, reason = ts.swing_signal(_daily_candles(50), 50, 200, 14, 5, 1.0, 2)
    assert direction is None
    assert level is None


def test_swing_signal_long_breakout_of_resistance():
    candles = _daily_candles(260, close=100.0)
    candles[-1]["close"] = 105.0
    resistance = PriceLevel(price=102.0, touches=3, kind="high", last_touched_index=200)
    with patch.object(ts, "find_swing_points", return_value=[]), \
         patch.object(ts, "cluster_levels", return_value=[resistance]), \
         patch.object(ts, "detect_breakout", return_value=True), \
         patch.object(ts, "detect_ma_crossover", return_value=None), \
         patch.object(ts, "rsi", return_value=[50.0] * 260):
        direction, broken_level, reason = ts.swing_signal(candles, 50, 200, 14, 5, 1.0, 2)
    assert direction == "long"
    assert broken_level == 102.0


def test_swing_signal_short_breakdown_of_support():
    candles = _daily_candles(260, close=100.0)
    candles[-1]["close"] = 95.0
    support = PriceLevel(price=98.0, touches=2, kind="low", last_touched_index=200)
    with patch.object(ts, "find_swing_points", return_value=[]), \
         patch.object(ts, "cluster_levels", return_value=[support]), \
         patch.object(ts, "detect_breakout", return_value=True), \
         patch.object(ts, "detect_ma_crossover", return_value=None), \
         patch.object(ts, "rsi", return_value=[50.0] * 260):
        direction, broken_level, reason = ts.swing_signal(candles, 50, 200, 14, 5, 1.0, 2)
    assert direction == "short"
    assert broken_level == 98.0


def test_swing_signal_downtrend_regime_blocks_long_breakout():
    candles = _daily_candles(260, close=100.0)
    candles[-1]["close"] = 105.0
    resistance = PriceLevel(price=102.0, touches=3, kind="high", last_touched_index=200)

    def _cross(fast, slow, index):
        return "death_cross" if index == 210 else None

    with patch.object(ts, "find_swing_points", return_value=[]), \
         patch.object(ts, "cluster_levels", return_value=[resistance]), \
         patch.object(ts, "detect_breakout", return_value=True), \
         patch.object(ts, "detect_ma_crossover", side_effect=_cross), \
         patch.object(ts, "rsi", return_value=[50.0] * 260):
        direction, broken_level, reason = ts.swing_signal(candles, 50, 200, 14, 5, 1.0, 2)
    assert direction is None


def test_swing_signal_no_breakout_means_no_signal():
    candles = _daily_candles(260, close=100.0)
    with patch.object(ts, "find_swing_points", return_value=[]), \
         patch.object(ts, "cluster_levels", return_value=[]), \
         patch.object(ts, "detect_ma_crossover", return_value=None), \
         patch.object(ts, "rsi", return_value=[50.0] * 260):
        direction, broken_level, reason = ts.swing_signal(candles, 50, 200, 14, 5, 1.0, 2)
    assert direction is None
    assert broken_level is None


# --- atr_stop_target / trailing ---


def _atr_candles(n, base=100.0, rng=2.0):
    # constant high-low range each bar -> a predictable, stable ATR
    return [{"open": base, "high": base + rng / 2, "low": base - rng / 2, "close": base} for _ in range(n)]


def test_atr_stop_target_not_enough_history():
    assert ts.atr_stop_target(_atr_candles(5), "long", 100.0, period=14, stop_mult=1.5, target_mult=2.5) is None


def test_atr_stop_target_long():
    candles = _atr_candles(30, base=100.0, rng=2.0)  # true range ~2.0 every bar -> ATR settles near 2.0
    result = ts.atr_stop_target(candles, "long", entry_price=100.0, period=14, stop_mult=1.5, target_mult=2.5)
    assert result is not None
    stop_price, target_price, atr_value = result
    assert atr_value == pytest.approx(2.0, abs=0.05)
    assert stop_price == pytest.approx(100.0 - 1.5 * atr_value, abs=0.01)
    assert target_price == pytest.approx(100.0 + 2.5 * atr_value, abs=0.01)
    assert stop_price < 100.0 < target_price


def test_atr_stop_target_short_mirrors_long():
    candles = _atr_candles(30, base=100.0, rng=2.0)
    stop_price, target_price, atr_value = ts.atr_stop_target(candles, "short", entry_price=100.0, period=14, stop_mult=1.5, target_mult=2.5)
    assert stop_price > 100.0 > target_price


def test_should_activate_trailing_long():
    assert ts.should_activate_trailing("long", entry_price=100.0, current_price=103.0, atr_value=2.0, activate_mult=1.0) is True
    assert ts.should_activate_trailing("long", entry_price=100.0, current_price=101.0, atr_value=2.0, activate_mult=1.0) is False


def test_should_activate_trailing_short():
    assert ts.should_activate_trailing("short", entry_price=100.0, current_price=97.0, atr_value=2.0, activate_mult=1.0) is True
    assert ts.should_activate_trailing("short", entry_price=100.0, current_price=99.0, atr_value=2.0, activate_mult=1.0) is False


def test_trailing_stop_price_long_trails_below_extreme():
    assert ts.trailing_stop_price("long", favorable_extreme=110.0, atr_value=2.0, trail_mult=1.0) == pytest.approx(108.0)


def test_trailing_stop_price_short_trails_above_extreme():
    assert ts.trailing_stop_price("short", favorable_extreme=90.0, atr_value=2.0, trail_mult=1.0) == pytest.approx(92.0)


# --- stop_breached / target_reached / update_trailing_stop ---


def test_stop_breached_long():
    assert ts.stop_breached("long", stop_price=95.0, current_price=94.0) is True
    assert ts.stop_breached("long", stop_price=95.0, current_price=96.0) is False


def test_stop_breached_short():
    assert ts.stop_breached("short", stop_price=105.0, current_price=106.0) is True
    assert ts.stop_breached("short", stop_price=105.0, current_price=104.0) is False


def test_target_reached_long():
    assert ts.target_reached("long", target_price=110.0, current_price=111.0) is True
    assert ts.target_reached("long", target_price=110.0, current_price=109.0) is False


def test_update_trailing_stop_not_yet_activated_leaves_stop_unchanged():
    favorable, stop, active = ts.update_trailing_stop(
        "long", entry_price=100.0, current_price=100.5, atr_value=2.0,
        favorable_extreme=100.0, stop_price=97.0, trailing_active=False,
        activate_mult=1.0, trail_mult=1.0,
    )
    assert active is False
    assert stop == 97.0  # unchanged - not enough favorable move yet
    assert favorable == 100.5  # still tracks the best price seen


def test_update_trailing_stop_activates_and_tightens():
    # favorable move of 3.0 >= activate_mult(1.0) x atr(2.0) -> activates
    favorable, stop, active = ts.update_trailing_stop(
        "long", entry_price=100.0, current_price=103.0, atr_value=2.0,
        favorable_extreme=103.0, stop_price=97.0, trailing_active=False,
        activate_mult=1.0, trail_mult=1.0,
    )
    assert active is True
    assert stop == pytest.approx(101.0)  # 103 - 1.0*2.0, tighter than the original 97.0


def test_update_trailing_stop_never_loosens():
    # price retraces after activation - stop must not move back down
    favorable, stop, active = ts.update_trailing_stop(
        "long", entry_price=100.0, current_price=101.0, atr_value=2.0,
        favorable_extreme=105.0, stop_price=103.0, trailing_active=True,
        activate_mult=1.0, trail_mult=1.0,
    )
    assert stop == 103.0  # candidate (105-2=103) ties; retraced price alone can't loosen it
    assert favorable == 105.0  # extreme doesn't reset just because price pulled back


def test_update_trailing_stop_short_direction():
    favorable, stop, active = ts.update_trailing_stop(
        "short", entry_price=100.0, current_price=96.0, atr_value=2.0,
        favorable_extreme=96.0, stop_price=103.0, trailing_active=False,
        activate_mult=1.0, trail_mult=1.0,
    )
    assert active is True
    assert stop == pytest.approx(98.0)  # 96 + 1.0*2.0, tighter than 103.0


# --- swing_should_exit ---


def test_swing_should_exit_long_reclaim_true():
    assert ts.swing_should_exit("long", broken_level=100.0, latest_close=97.0, reclaim_buffer_pct=2.0) is True


def test_swing_should_exit_long_no_reclaim_yet():
    assert ts.swing_should_exit("long", broken_level=100.0, latest_close=99.0, reclaim_buffer_pct=2.0) is False


def test_swing_should_exit_short_reclaim_true():
    assert ts.swing_should_exit("short", broken_level=100.0, latest_close=103.0, reclaim_buffer_pct=2.0) is True
