import numpy as np
import pandas as pd

from market_state.classifier import classify_market_state
from tests.test_market_state_structure import _zigzag_prices


def _df_from_prices(prices: list[float], volumes: list[int] | None = None) -> pd.DataFrame:
    n = len(prices)
    volumes = volumes or [1000] * n
    ts = pd.date_range("2026-01-01 09:15", periods=n, freq="1min")
    return pd.DataFrame({
        "timestamp": ts, "open": prices, "high": [p + 0.05 for p in prices], "low": [p - 0.05 for p in prices],
        "close": prices, "volume": volumes,
    })


def _with_calm_tail(prices: list[float], n_flat_bars: int = 30) -> list[float]:
    """Appends flat (zero true-range) bars after the zigzag so the
    volatility engine's percentile-based lookback has enough calm history
    to correctly read LOW/NORMAL instead of an artifact of the zigzag's
    own small sample size - a pure structure fixture is not automatically
    a calm-volatility fixture too, they need to be composed deliberately."""
    return prices + [prices[-1]] * n_flat_bars


def test_calm_uptrend_classifies_as_trending_up():
    prices = _with_calm_tail(_zigzag_prices([100, 110, 105, 120, 112, 130]))
    df = _df_from_prices(prices)
    state = classify_market_state(df)
    assert state.structure.trend == "UPTREND"
    assert state.volatility.label != "HIGH"
    assert state.regime == "TRENDING_UP"


def test_calm_downtrend_classifies_as_trending_down():
    prices = _with_calm_tail(_zigzag_prices([120, 130, 110, 120, 100, 110]))
    df = _df_from_prices(prices)
    state = classify_market_state(df)
    assert state.structure.trend == "DOWNTREND"
    assert state.volatility.label != "HIGH"
    assert state.regime == "TRENDING_DOWN"


def test_flat_range_classifies_as_ranging():
    prices = _with_calm_tail(_zigzag_prices([100, 110, 100, 110, 100, 110]))
    df = _df_from_prices(prices)
    state = classify_market_state(df)
    assert state.structure.trend == "RANGE"
    assert state.volatility.label != "HIGH"
    assert state.regime == "RANGING"


def test_short_series_regime_is_unknown():
    df = _df_from_prices([100] * 6)
    state = classify_market_state(df)
    assert state.regime == "UNKNOWN"


def test_high_volatility_dominates_regime_even_in_an_uptrend():
    # Real, confirmed uptrend structure, PLUS a calm buffer so the eventual
    # volatility spike lands well clear of the swing-confirmation window -
    # otherwise inflating high/low near the trend's own final swing point
    # would corrupt structure detection itself, conflating two different
    # things this test needs to vary independently.
    prices = _with_calm_tail(_zigzag_prices([100, 110, 105, 120, 112, 130]), n_flat_bars=15)
    df = _df_from_prices(prices)
    df.loc[df.index[-5:], "high"] = df.loc[df.index[-5:], "close"] + 20
    df.loc[df.index[-5:], "low"] = df.loc[df.index[-5:], "close"] - 20
    state = classify_market_state(df)
    assert state.structure.trend == "UPTREND"
    assert state.volatility.label == "HIGH"
    assert state.regime == "VOLATILE"
