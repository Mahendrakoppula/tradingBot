import math

from trading_bot.engine.trend import TFTrend, TrendConfig, align, classify_tf_trend


def _trending(n: int, step: float, start: float = 100.0, wiggle: float = 0.3) -> list[dict]:
    """Synthetic monotone trend with small alternating wiggle so swings exist."""
    out = []
    px = start
    for i in range(n):
        px += step
        w = wiggle if i % 2 == 0 else -wiggle
        o = px - step / 2
        c = px + w
        hi, lo = max(o, c) + abs(step) * 0.4, min(o, c) - abs(step) * 0.4
        out.append({"open": o, "high": hi, "low": lo, "close": c, "volume": 100})
    return out


def _flat(n: int, level: float = 100.0) -> list[dict]:
    out = []
    for i in range(n):
        d = 0.5 if i % 2 == 0 else -0.5
        out.append({"open": level, "high": level + 0.8, "low": level - 0.8, "close": level + d, "volume": 100})
    return out


CFG = TrendConfig()


def test_insufficient_history_is_neutral():
    t = classify_tf_trend(_trending(10, 1.0), "5m", CFG)
    assert t.label == "NEUTRAL" and t.score == 0.0 and t.evidence["reason"] == "insufficient_history"


def test_clear_uptrend_scores_positive_and_bullish():
    t = classify_tf_trend(_trending(260, 0.8), "5m", CFG)
    assert t.score > 0.4
    assert t.label in ("BULL", "STRONG_BULL")
    assert t.direction == "up"
    assert t.evidence["ema_order"] == 1.0


def test_clear_downtrend_scores_negative_and_bearish():
    t = classify_tf_trend(_trending(260, -0.8), "5m", CFG)
    assert t.score < -0.4
    assert t.label in ("BEAR", "STRONG_BEAR")
    assert t.direction == "down"


def test_flat_market_is_neutral_or_weak():
    t = classify_tf_trend(_flat(260), "5m", CFG)
    assert abs(t.score) < 0.4
    assert t.label in ("NEUTRAL", "WEAK_BULL", "WEAK_BEAR")


def test_persistence_and_acceleration_use_prev():
    candles = _trending(260, 0.8)
    t1 = classify_tf_trend(candles, "5m", CFG, i=250)
    t2 = classify_tf_trend(candles, "5m", CFG, i=259, prev=t1)
    assert t2.persistence_bars == t1.persistence_bars + 1
    assert math.isclose(t2.acceleration, t2.score - t1.score, abs_tol=1e-6)


def test_counter_trend_when_lower_tf_opposes_strong_higher_tf():
    higher = TFTrend("30m", "STRONG_BULL", 0.9, 30.0, 10, 0.0, False, False)
    t = classify_tf_trend(_trending(260, -0.8), "5m", CFG, higher=higher)
    assert t.label == "COUNTER_TREND"
    assert t.score < 0  # underlying read is still bearish


def test_unstable_when_labels_keep_flipping():
    history = ["BULL", "BEAR", "BULL", "BEAR", "WEAK_BULL"]
    t = classify_tf_trend(_trending(260, -0.8), "5m", CFG, recent_labels=history)
    assert t.label == "UNSTABLE"


def test_vwap_side_only_applies_intraday():
    candles = _trending(260, 0.8)
    with_vwap = classify_tf_trend(candles, "5m", CFG, vwap_value=candles[-1]["close"] - 10)
    daily = classify_tf_trend(candles, "1d", CFG, vwap_value=candles[-1]["close"] - 10)
    assert "vwap_side" in with_vwap.evidence and with_vwap.evidence["vwap_side"] == 1.0
    assert "vwap_side" not in daily.evidence


# --- alignment -------------------------------------------------------------

W = {"1d": 0.25, "30m": 0.30, "5m": 0.30, "1m": 0.15}


def _t(tf, label, score):
    return TFTrend(tf, label, score, None, 1, 0.0, False, False)


def test_all_bull_is_strong_alignment():
    trends = {tf: _t(tf, "STRONG_BULL", 0.9) for tf in W}
    a = align(trends, W)
    assert a.label == "STRONG_TREND_ALIGNMENT" and a.direction_preference == "up"
    assert math.isclose(a.weighted_score, 1.0)


def test_mixed_strength_same_direction_is_alignment():
    trends = {"1d": _t("1d", "BULL", 0.5), "30m": _t("30m", "BULL", 0.5), "5m": _t("5m", "WEAK_BULL", 0.3), "1m": _t("1m", "BULL", 0.5)}
    assert align(trends, W).label == "TREND_ALIGNMENT"


def test_lower_tf_against_strong_higher_is_counter_trend():
    trends = {"1d": _t("1d", "STRONG_BULL", 0.9), "30m": _t("30m", "BULL", 0.6), "5m": _t("5m", "BEAR", -0.6), "1m": _t("1m", "BEAR", -0.5)}
    assert align(trends, W).label == "COUNTER_TREND"


def test_transition_on_30m_or_5m_wins():
    trends = {"1d": _t("1d", "BULL", 0.6), "30m": _t("30m", "TREND_TRANSITION", 0.2), "5m": _t("5m", "BULL", 0.5)}
    assert align(trends, W).label == "TREND_TRANSITION"


def test_neutral_when_nothing_present_or_flat():
    assert align({}, W).label == "NEUTRAL"
    trends = {tf: _t(tf, "NEUTRAL", 0.0) for tf in W}
    a = align(trends, W)
    assert a.label == "NEUTRAL" and a.direction_preference == "none"


def test_missing_timeframes_renormalise_weights():
    trends = {"5m": _t("5m", "STRONG_BULL", 0.9), "1m": _t("1m", "STRONG_BULL", 0.9)}
    assert math.isclose(align(trends, W).weighted_score, 1.0)
