import datetime as dt

import pytest

from trading_bot.indicators import (
    bollinger,
    obv,
    percentile_rank_series,
    relative_volume_series,
    roc,
    session_vwap,
    slope,
    stochastic,
)


def _c(o, h, l, c, v=0, **extra):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, **extra}


def test_roc_basic():
    assert roc([100, 110, 121], 1) == [None, pytest.approx(10.0), pytest.approx(10.0)]
    assert roc([100, 0, 50], 1)[2] is None  # division guard


def test_stochastic_extremes_and_flat():
    candles = [_c(1, 10, 0, 5)] * 3 + [_c(1, 10, 0, 10)]
    k, d = stochastic(candles, k_period=4, d_period=1)
    assert k[:3] == [None, None, None]
    assert k[3] == pytest.approx(100.0)
    assert d[3] == pytest.approx(100.0)
    flat = [_c(5, 5, 5, 5)] * 4
    assert stochastic(flat, 4, 1)[0][3] == pytest.approx(50.0)


def test_stochastic_d_is_sma_of_k():
    candles = [_c(0, 10, 0, v) for v in (2, 4, 6, 8, 10, 8, 6)]
    k, d = stochastic(candles, k_period=3, d_period=2)
    assert d[3] == pytest.approx((k[2] + k[3]) / 2)


def test_bollinger_width_and_bands():
    closes = [10.0] * 5
    mid, up, lo, width = bollinger(closes, period=5, num_std=2)
    assert mid[4] == 10 and up[4] == 10 and lo[4] == 10 and width[4] == 0
    closes = [8, 12, 8, 12, 10]  # mean 10, pop-sd 1.7889
    mid, up, lo, width = bollinger(closes, period=5, num_std=2)
    sd = (sum((x - 10) ** 2 for x in closes) / 5) ** 0.5
    assert up[4] == pytest.approx(10 + 2 * sd)
    assert lo[4] == pytest.approx(10 - 2 * sd)
    assert width[4] == pytest.approx(4 * sd / 10 * 100)


def test_obv_signed_cumulative():
    candles = [_c(0, 0, 0, 10, 100), _c(0, 0, 0, 11, 50), _c(0, 0, 0, 11, 70), _c(0, 0, 0, 9, 20)]
    assert obv(candles) == [0, 50, 50, 30]


def test_percentile_rank_series():
    vals = [1, 2, 3, 4, 5]
    ranks = percentile_rank_series(vals, lookback=5)
    assert ranks[:4] == [None] * 4
    assert ranks[4] == pytest.approx(80.0)  # 4 of 5 below
    ranks = percentile_rank_series([5, 4, 3, 2, 1], lookback=5)
    assert ranks[4] == pytest.approx(0.0)


def test_percentile_rank_skips_none():
    vals = [None, 1, 2, 3]
    assert percentile_rank_series(vals, lookback=3)[3] == pytest.approx(200 / 3)


def test_relative_volume_excludes_current_bar():
    candles = [_c(0, 0, 0, 0, v) for v in (100, 100, 100, 300)]
    rv = relative_volume_series(candles, period=3)
    assert rv[:3] == [None] * 3
    assert rv[3] == pytest.approx(3.0)
    zero = [_c(0, 0, 0, 0, 0)] * 3 + [_c(0, 0, 0, 0, 10)]
    assert relative_volume_series(zero, 3)[3] is None


def test_slope_raw_and_atr_normalised():
    vals = [10.0, 11.0, 12.0, 14.0]
    assert slope(vals, 2)[3] == pytest.approx(3.0)
    assert slope(vals, 2, atr_series=[None, None, None, 1.5])[3] == pytest.approx(2.0)
    assert slope(vals, 2, atr_series=[None, None, None, 0.0])[3] is None
    assert slope([None, 1.0, 2.0], 1)[1] is None


def test_session_vwap_resets_on_date_change():
    d1, d2 = dt.date(2026, 9, 15), dt.date(2026, 9, 16)
    candles = [
        _c(10, 10, 10, 10, 100, date=d1),
        _c(20, 20, 20, 20, 100, date=d1),  # cum: (10*100 + 20*100)/200 = 15
        _c(30, 30, 30, 30, 100, date=d2),  # new session -> 30
    ]
    assert session_vwap(candles) == [pytest.approx(10.0), pytest.approx(15.0), pytest.approx(30.0)]


def test_session_vwap_is_none_with_zero_volume_not_close():
    d = dt.date(2026, 9, 15)
    candles = [_c(10, 12, 8, 11, 0, date=d), _c(11, 13, 9, 12, 0, date=d)]
    assert session_vwap(candles) == [None, None]
