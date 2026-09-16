import dataclasses

from trading_bot.engine.regime import (
    REGIME_LABELS,
    MarketRegime,
    RegimeConfig,
    RegimeInputs,
    classify_market_regime,
    classify_regime,
)

CFG = RegimeConfig()


def _x(**over) -> RegimeInputs:
    base = dict(
        quality_ok=True, minutes_since_open=30, trend_score=0.0, trend_label="NEUTRAL",
        trend_transition=False, trend_unstable=False, adx=18.0, atr_percentile=50.0,
        bb_width_percentile=50.0, ema_fast_cross_count=0, close=100.0, prev_close=100.0,
        bar_range_atr=1.0, opening_range=(101.0, 99.0), pdh=103.0, pdl=97.0, in_swing_range=True,
    )
    base.update(over)
    return RegimeInputs(**base)


def test_labels_are_the_spec_seventeen():
    assert len(REGIME_LABELS) == 17 and len(set(REGIME_LABELS)) == 17


def test_no_trade_on_bad_quality_and_first_minutes():
    assert classify_market_regime(_x(quality_ok=False), None, CFG).primary == "NO_TRADE"
    assert classify_market_regime(_x(minutes_since_open=2), None, CFG).primary == "NO_TRADE"


def test_safety_labels_bypass_hysteresis():
    prev = classify_market_regime(_x(trend_score=0.6, adx=25), None, CFG)
    assert prev.primary == "BULL"
    r = classify_market_regime(_x(trend_unstable=True), prev, CFG)
    assert r.primary == "UNSTABLE" and r.transition == "BULL->UNSTABLE"


def test_priority_transition_over_breakout():
    x = _x(trend_transition=True, close=104, prev_close=102, bar_range_atr=2.0)
    assert classify_market_regime(x, None, CFG).primary == "TRANSITION"


def test_breakout_needs_level_break_and_expansion():
    x = _x(close=104.0, prev_close=102.0, bar_range_atr=2.0)  # through PDH 103 with a wide bar
    assert classify_market_regime(x, None, CFG).primary == "BREAKOUT"
    narrow = dataclasses.replace(x, bar_range_atr=0.5)
    assert classify_market_regime(narrow, None, CFG).primary != "BREAKOUT"
    down = _x(close=96.0, prev_close=98.0, bar_range_atr=2.0)  # through PDL 97
    assert classify_market_regime(down, None, CFG).primary == "BREAKDOWN"


def test_compression_and_expansion():
    assert classify_market_regime(_x(bb_width_percentile=10, atr_percentile=15), None, CFG).primary == "COMPRESSION"
    assert classify_market_regime(_x(bb_width_percentile=90, bar_range_atr=2.0), None, CFG).primary == "EXPANSION"


def test_volatility_extremes_and_chop_and_range():
    assert classify_market_regime(_x(atr_percentile=90), None, CFG).primary == "HIGH_VOLATILITY"
    assert classify_market_regime(_x(atr_percentile=10), None, CFG).primary == "LOW_VOLATILITY"
    assert classify_market_regime(_x(adx=10, ema_fast_cross_count=4), None, CFG).primary == "CHOPPY"
    assert classify_market_regime(_x(adx=12), None, CFG).primary == "RANGE"


def test_trend_labels_by_score_and_adx():
    assert classify_market_regime(_x(trend_score=0.8, adx=30), None, CFG).primary == "STRONG_BULL"
    assert classify_market_regime(_x(trend_score=0.5, adx=22), None, CFG).primary == "BULL"
    assert classify_market_regime(_x(trend_score=0.25, adx=22), None, CFG).primary == "WEAK_BULL"
    assert classify_market_regime(_x(trend_score=-0.8, adx=30), None, CFG).primary == "STRONG_BEAR"
    # low ADX means direction axis is NONE even with a score
    assert classify_market_regime(_x(trend_score=0.8, adx=12), None, CFG).primary == "RANGE"


def test_hysteresis_requires_two_consecutive_bars():
    prev = classify_market_regime(_x(adx=12), None, CFG)
    assert prev.primary == "RANGE"
    bull = _x(trend_score=0.6, adx=25)
    r1 = classify_market_regime(bull, prev, CFG)
    assert r1.primary == "RANGE" and r1.candidate == "BULL" and r1.pending_bars == 1 and r1.transition is None
    r2 = classify_market_regime(bull, r1, CFG)
    assert r2.primary == "BULL" and r2.transition == "RANGE->BULL" and r2.pending_bars == 0
    # a different candidate resets the pending counter
    r3 = classify_market_regime(_x(atr_percentile=90), r1, CFG)
    assert r3.primary == "RANGE" and r3.candidate == "HIGH_VOLATILITY" and r3.pending_bars == 1


def test_stable_regime_reports_no_transition():
    prev = MarketRegime("BULL", "BULL", "BULL", "NORMAL", "TREND", None, 0)
    r = classify_market_regime(_x(trend_score=0.6, adx=25), prev, CFG)
    assert r.primary == "BULL" and r.transition is None and r.pending_bars == 0


# --- promoted basic regime keeps its contract --------------------------------


def test_basic_regime_empty_and_shim():
    r = classify_regime([])
    assert r.trend_direction == "none" and r.volatility_bucket == "unknown"
    from research.framework.regime import classify_regime as shim
    assert shim is classify_regime
