import datetime as dt

import numpy as np
import pandas as pd
import pytest

from features.theoretical_options import approximate_time_to_expiry_years, theoretical_option_snapshot


def test_approximate_time_to_expiry_years_scales_by_weekday_fraction():
    # 7 calendar days -> 5 "trading" days -> 5/252 years
    result = approximate_time_to_expiry_years(dt.date(2026, 1, 1), dt.date(2026, 1, 8))
    assert result == pytest.approx(5 / 252)


def test_expiry_in_the_past_gives_zero_not_negative():
    result = approximate_time_to_expiry_years(dt.date(2026, 1, 10), dt.date(2026, 1, 1))
    assert result == 0.0


def _make_ohlcv(n: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 24500 + np.cumsum(rng.normal(0, 30, n))
    ts = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({
        "timestamp": ts, "open": closes, "high": closes + 20, "low": closes - 20, "close": closes,
        "volume": [1_000_000] * n,
    })


def test_returns_none_when_not_enough_history_for_the_vol_window():
    df = _make_ohlcv(5)
    result = theoretical_option_snapshot(
        df, as_of_index=4, strike=24500, expiry=dt.date(2026, 2, 1), option_type="CE",
        risk_free_rate=0.07, vol_window=20,
    )
    assert result is None


def test_produces_a_snapshot_once_enough_history_exists():
    df = _make_ohlcv(60)
    as_of_index = 40
    expiry = df["timestamp"].iloc[as_of_index].date() + dt.timedelta(days=30)
    result = theoretical_option_snapshot(
        df, as_of_index=as_of_index, strike=float(df["close"].iloc[as_of_index]), expiry=expiry,
        option_type="CE", risk_free_rate=0.07, vol_window=20,
    )
    assert result is not None
    assert result.spot == pytest.approx(float(df["close"].iloc[as_of_index]))
    assert result.time_to_expiry_years > 0
    assert result.volatility_used > 0
    assert result.price >= 0
    assert 0.0 <= result.greeks.delta <= 1.0  # ATM call


def test_snapshot_does_not_use_bars_after_as_of_index():
    df = _make_ohlcv(60)
    as_of_index = 40
    expiry = df["timestamp"].iloc[as_of_index].date() + dt.timedelta(days=30)
    kwargs = dict(strike=24500.0, expiry=expiry, option_type="CE", risk_free_rate=0.07, vol_window=20)

    before = theoretical_option_snapshot(df, as_of_index, **kwargs)

    mutated = df.copy()
    mutated.loc[mutated.index[as_of_index + 1]:, "close"] = 999999.0
    after = theoretical_option_snapshot(mutated, as_of_index, **kwargs)

    assert before.price == pytest.approx(after.price)
    assert before.volatility_used == pytest.approx(after.volatility_used)


def test_expiry_on_or_before_as_of_date_gives_intrinsic_value_only():
    df = _make_ohlcv(60)
    as_of_index = 40
    as_of_date = df["timestamp"].iloc[as_of_index].date()
    strike = float(df["close"].iloc[as_of_index]) - 500
    result = theoretical_option_snapshot(
        df, as_of_index=as_of_index, strike=strike, expiry=as_of_date, option_type="CE",
        risk_free_rate=0.07, vol_window=20,
    )
    assert result.time_to_expiry_years == 0.0
    assert result.price == pytest.approx(result.spot - strike)
    assert result.greeks.gamma == 0.0
