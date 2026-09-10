from research.framework.regime import classify_regime


def _candle(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def test_classify_regime_empty_candles():
    r = classify_regime([])
    assert r.trend_direction == "none"
    assert r.trend_strength is None
    assert r.volatility_bucket == "unknown"


def test_classify_regime_relentless_uptrend_is_up_and_strong():
    price = 100.0
    candles = []
    for _ in range(60):
        candles.append(_candle(price, price + 2, price, price + 1))
        price += 2
    r = classify_regime(candles, adx_period=14)
    assert r.trend_direction == "up"
    assert r.trend_strength is not None
    assert r.trend_strength > 20.0
    assert r.plus_di > r.minus_di


def test_classify_regime_choppy_range_is_none():
    # oscillates around 100 with no net directional movement
    candles = []
    for i in range(60):
        base = 100.0 + (2 if i % 2 == 0 else -2)
        candles.append(_candle(base, base + 1, base - 1, base))
    r = classify_regime(candles, adx_period=14, trend_adx_threshold=20.0)
    assert r.trend_direction == "none"


def test_classify_regime_volatility_bucket_high_when_range_widens():
    # First 40 bars: tight range (low ATR). Last 20 bars: much wider range
    # (high ATR) - the latest reading should rank near the top of its own
    # recent history.
    candles = []
    price = 100.0
    for _ in range(40):
        candles.append(_candle(price, price + 0.5, price - 0.5, price))
    for _ in range(20):
        candles.append(_candle(price, price + 10, price - 10, price))
    r = classify_regime(candles, atr_period=14, atr_lookback=100)
    assert r.volatility_bucket == "high"
    assert r.atr_percentile is not None
    assert r.atr_percentile >= 67.0
