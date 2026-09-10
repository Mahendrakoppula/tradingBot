import pandas as pd

from market_state.liquidity import classify_liquidity


def _df_with_volumes(volumes: list[int]) -> pd.DataFrame:
    n = len(volumes)
    ts = pd.date_range("2026-01-01 09:15", periods=n, freq="1min")
    return pd.DataFrame({
        "timestamp": ts, "open": [100] * n, "high": [100.5] * n, "low": [99.5] * n,
        "close": [100] * n, "volume": volumes,
    })


def test_insufficient_data_when_too_few_bars():
    df = _df_with_volumes([1000] * 5)
    state = classify_liquidity(df, period=20)
    assert state.label == "INSUFFICIENT_DATA"


def test_volume_far_above_average_is_high():
    df = _df_with_volumes([1000] * 20 + [5000])
    state = classify_liquidity(df, period=20)
    assert state.label == "HIGH"
    assert state.relative_volume == 5.0


def test_volume_far_below_average_is_low():
    df = _df_with_volumes([1000] * 20 + [100])
    state = classify_liquidity(df, period=20)
    assert state.label == "LOW"


def test_volume_near_average_is_normal():
    df = _df_with_volumes([1000] * 20 + [1050])
    state = classify_liquidity(df, period=20)
    assert state.label == "NORMAL"
