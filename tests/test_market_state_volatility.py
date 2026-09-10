import numpy as np
import pandas as pd

from market_state.volatility import classify_volatility


def _df_from_closes(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    ts = pd.date_range("2026-01-01 09:15", periods=n, freq="1min")
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return pd.DataFrame({"timestamp": ts, "open": closes, "high": highs, "low": lows, "close": closes, "volume": [1000] * n})


def test_insufficient_data_when_fewer_bars_than_atr_period():
    df = _df_from_closes([100, 101, 102, 103, 104])
    state = classify_volatility(df, atr_period=14)
    assert state.label == "INSUFFICIENT_DATA"


def test_a_calm_period_then_a_volatile_spike_is_classified_high():
    rng = np.random.default_rng(0)
    calm = 100 + np.cumsum(rng.normal(0, 0.05, 150))
    # widen the true range sharply for the final bars (large high/low
    # excursions), not just the close - this is what ATR measures.
    df = _df_from_closes(list(calm))
    df.loc[df.index[-5:], "high"] = df.loc[df.index[-5:], "close"] + 10
    df.loc[df.index[-5:], "low"] = df.loc[df.index[-5:], "close"] - 10
    state = classify_volatility(df, atr_period=14, lookback=150)
    assert state.label == "HIGH"


def test_a_volatile_period_then_calm_is_classified_low():
    rng = np.random.default_rng(1)
    volatile = 100 + np.cumsum(rng.normal(0, 2.0, 150))
    df = _df_from_closes(list(volatile))
    # flatten the final bars' true range right down to nothing
    df.loc[df.index[-20:], "high"] = df.loc[df.index[-20:], "close"] + 0.01
    df.loc[df.index[-20:], "low"] = df.loc[df.index[-20:], "close"] - 0.01
    state = classify_volatility(df, atr_period=14, lookback=150)
    assert state.label == "LOW"
