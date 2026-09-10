import pandas as pd

from market_state.momentum import classify_momentum


def _df_from_closes(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    ts = pd.date_range("2026-01-01 09:15", periods=n, freq="1min")
    return pd.DataFrame({
        "timestamp": ts, "open": closes, "high": [c + 0.1 for c in closes], "low": [c - 0.1 for c in closes],
        "close": closes, "volume": [1000] * n,
    })


def test_insufficient_data_when_shorter_than_lookback():
    df = _df_from_closes([100, 101, 102])
    state = classify_momentum(df, lookback=10)
    assert state.label == "INSUFFICIENT_DATA"


def test_flat_prices_classify_as_flat():
    df = _df_from_closes([100.0] * 30)
    state = classify_momentum(df, lookback=10)
    assert state.label == "FLAT"


def test_a_sharp_late_rally_after_a_calm_period_is_strong_up():
    # 100 calm bars (tiny drift) then a sharp run-up in the last stretch -
    # the most recent 10-bar ROC should be an outlier vs. the trailing
    # ROC distribution built mostly from the calm period.
    closes = [100 + 0.01 * i for i in range(100)]
    closes += [closes[-1] * (1.02 ** i) for i in range(1, 15)]
    df = _df_from_closes(closes)
    state = classify_momentum(df, lookback=10, history=110)
    assert state.label == "STRONG_UP"
    assert state.roc > 0


def test_a_sharp_late_decline_after_a_calm_period_is_strong_down():
    closes = [100 - 0.01 * i for i in range(100)]
    closes += [closes[-1] * (0.98 ** i) for i in range(1, 15)]
    df = _df_from_closes(closes)
    state = classify_momentum(df, lookback=10, history=110)
    assert state.label == "STRONG_DOWN"
    assert state.roc < 0
