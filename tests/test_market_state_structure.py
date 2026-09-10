import pandas as pd

from market_state.structure import classify_structure


def _zigzag_prices(extrema: list[float], window: int = 3) -> list[float]:
    """Piecewise-linear zigzag through the given extrema (alternating
    trough/peak/trough/peak/...), `window` bars per leg, with lead/tail
    padding so even the first and last extrema get enough neighboring
    bars to be confirmed as swing points. Verified against
    market_state.structure.classify_structure before use here."""
    prices = [extrema[0]]
    prev = extrema[0]
    for target in extrema[1:]:
        for step in range(1, window + 1):
            prices.append(prev + (target - prev) * step / window)
        prev = target
    lead_slope = prices[1] - prices[0]
    lead = [prices[0] - lead_slope * k for k in range(window, 0, -1)]
    final_slope = prices[-1] - prices[-2]
    tail = [prices[-1] - final_slope * k for k in range(1, window + 1)]
    return lead + prices + tail


def _make_df(extrema: list[float], window: int = 3) -> pd.DataFrame:
    prices = _zigzag_prices(extrema, window)
    n = len(prices)
    ts = pd.date_range("2026-01-01 09:15", periods=n, freq="1min")
    return pd.DataFrame({
        "timestamp": ts,
        "open": prices, "high": [p + 0.05 for p in prices], "low": [p - 0.05 for p in prices],
        "close": prices, "volume": [1000] * n,
    })


def test_rising_peaks_and_troughs_classify_as_uptrend():
    df = _make_df([100, 110, 105, 120, 112, 130])
    state = classify_structure(df)
    assert state.trend == "UPTREND"


def test_falling_peaks_and_troughs_classify_as_downtrend():
    df = _make_df([120, 130, 110, 120, 100, 110])
    state = classify_structure(df)
    assert state.trend == "DOWNTREND"


def test_flat_alternating_extrema_classify_as_range():
    df = _make_df([100, 110, 100, 110, 100, 110])
    state = classify_structure(df)
    assert state.trend == "RANGE"


def test_mixed_higher_high_but_lower_low_is_range_not_guessed():
    # peaks rising (110 -> 120) but troughs also rising is UPTREND - flip
    # the troughs to falling while peaks still rise: ambiguous, must be RANGE.
    df = _make_df([100, 110, 95, 120, 90, 130])
    state = classify_structure(df)
    assert state.trend == "RANGE"


def test_short_series_is_insufficient_data():
    ts = pd.date_range("2026-01-01 09:15", periods=6, freq="1min")
    df = pd.DataFrame({
        "timestamp": ts, "open": [100] * 6, "high": [100.1] * 6, "low": [99.9] * 6,
        "close": [100] * 6, "volume": [1000] * 6,
    })
    state = classify_structure(df)
    assert state.trend == "INSUFFICIENT_DATA"
