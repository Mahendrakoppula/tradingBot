from unittest.mock import patch

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


# --- swing_should_exit ---


def test_swing_should_exit_long_reclaim_true():
    assert ts.swing_should_exit("long", broken_level=100.0, latest_close=97.0, reclaim_buffer_pct=2.0) is True


def test_swing_should_exit_long_no_reclaim_yet():
    assert ts.swing_should_exit("long", broken_level=100.0, latest_close=99.0, reclaim_buffer_pct=2.0) is False


def test_swing_should_exit_short_reclaim_true():
    assert ts.swing_should_exit("short", broken_level=100.0, latest_close=103.0, reclaim_buffer_pct=2.0) is True
