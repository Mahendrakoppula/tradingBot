import pytest

from trading_bot.indicators import atr, ema, macd, rolling_avg_volume, rsi, sma, vwap


def test_sma_basic():
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]


def test_sma_not_enough_data_returns_all_none():
    assert sma([1, 2], 3) == [None, None]


def test_ema_basic_linear_series():
    # Hand-computed: seed = mean(1,2,3) = 2 at idx 2, multiplier = 2/(3+1) = 0.5
    # idx3: (4-2)*0.5+2 = 3, idx4: (5-3)*0.5+3 = 4
    assert ema([1, 2, 3, 4, 5], 3) == [None, None, 2, 3, 4]


def test_rsi_monotonic_increase_is_100():
    closes = list(range(1, 17))  # 16 strictly increasing closes, period=14
    result = rsi(closes, period=14)
    assert result[:14] == [None] * 14
    assert result[14] == pytest.approx(100.0)
    assert result[15] == pytest.approx(100.0)


def test_rsi_monotonic_decrease_is_0():
    closes = list(range(16, 0, -1))
    result = rsi(closes, period=14)
    assert result[14] == pytest.approx(0.0)


def test_rsi_not_enough_data_returns_all_none():
    assert rsi([1, 2, 3], period=14) == [None, None, None]


def test_atr_constant_true_range():
    candles = [
        {"high": 10, "low": 8, "close": 9},
        {"high": 11, "low": 9, "close": 10},
        {"high": 12, "low": 10, "close": 11},
        {"high": 13, "low": 11, "close": 12},
    ]
    result = atr(candles, period=2)
    assert result[0] is None and result[1] is None
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(2.0)


def test_vwap_cumulative():
    candles = [
        {"high": 10, "low": 8, "close": 9, "volume": 100},
        {"high": 11, "low": 9, "close": 10, "volume": 200},
    ]
    result = vwap(candles)
    assert result[0] == pytest.approx(9.0)
    assert result[1] == pytest.approx(2900 / 300)


def test_vwap_zero_volume_falls_back_to_close():
    candles = [{"high": 10, "low": 8, "close": 9, "volume": 0}]
    assert vwap(candles) == [9.0]


def test_rolling_avg_volume():
    candles = [{"volume": v} for v in (10, 20, 30, 40)]
    assert rolling_avg_volume(candles, 2) == [None, 15, 25, 35]


def test_macd_lengths_and_histogram_consistency():
    closes = [100 + i * 0.5 for i in range(60)]  # steady uptrend, enough bars for slow(26)+signal(9)
    macd_line, signal_line, hist = macd(closes, fast=12, slow=26, signal=9)
    assert len(macd_line) == len(signal_line) == len(hist) == len(closes)
    assert macd_line[:25] == [None] * 25  # slow EMA needs 26 bars -> first value at idx 25
    assert macd_line[25] is not None
    for m, s, h in zip(macd_line, signal_line, hist):
        if m is not None and s is not None:
            assert h == pytest.approx(m - s)
        else:
            assert h is None
