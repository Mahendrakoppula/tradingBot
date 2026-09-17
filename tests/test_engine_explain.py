import datetime as dt

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.explain import EXPLANATION_KEYS, NOT_EVALUATED, build_explanation, render
from trading_bot.engine.presignal import StageEvent
from trading_bot.timeutil import IST


def _ctx() -> ContextSnapshot:
    return ContextSnapshot(
        ts=dt.datetime(2026, 9, 16, 9, 20, tzinfo=IST), underlying="NIFTY", trigger_tf="5m", spot=25012.0,
        session_phase="open_drive", quality="OK", bar_index=3,
        trends={"1d": {"label": "BULL", "score": 0.55}, "5m": {"label": "STRONG_BULL", "score": 0.8}},
        alignment={"label": "TREND_ALIGNMENT", "weighted_score": 0.62, "direction_preference": "up"},
        regime={"primary": "BREAKOUT", "transition": "COMPRESSION->BREAKOUT"},
        structure={"last_event": {"kind": "bos_up", "index": 2, "price": 25008.0}},
        levels={"nearest_above": {"name": "pdh", "price": 25008.0, "distance_atr": 0.4, "touches": 2}},
        price_action={"labels": ["displacement", "bullish_engulfing"]},
        indicators={"atr": 20.0, "rsi": 61.2, "macd_hist": 0.42, "adx": 24.0},
        volume={"relative_volume": 1.6, "volume_proxy": "futures", "obv_slope": 0.12},
    )


def _event(to="TRADE_READY", reason="confirmation_closed", **details):
    base = {"trigger_level": 25008.0, "extension_atr": 0.35, "family_hint": "compression_breakout"}
    base.update(details)
    return StageEvent("s-1", "NIFTY", "up", "CONFIRMING", to, 0.7, reason, 3, base)


def test_template_has_exactly_the_twenty_keys_in_order():
    ex = build_explanation(_ctx(), _event())
    assert tuple(ex) == EXPLANATION_KEYS and len(ex) == 20
    assert all(isinstance(v, str) for v in ex.values())


def test_would_be_signal_content():
    ex = build_explanation(_ctx(), _event())
    assert ex["Direction"].startswith("up") and "CE" in ex["Direction"]
    assert "1d=BULL(0.55)" in ex["Trend"] and "5m=STRONG_BULL(0.80)" in ex["Trend"]
    assert ex["Regime"] == "BREAKOUT (transition COMPRESSION->BREAKOUT)"
    assert "pdh @ 25008.00" in ex["Location"] and "touches=2" in ex["Location"]
    assert "bos_up" in ex["Structure"]
    assert ex["Price Action"] == "displacement, bullish_engulfing"
    assert "25008.00" in ex["Confirmation"] and "0.35 ATR" in ex["Confirmation"]
    assert "futures proxy" in ex["Volume"]
    assert "RSI=61.2" in ex["Momentum"]
    assert ex["MTF"].startswith("TREND_ALIGNMENT")
    assert ex["Decision"].startswith("WOULD-BE SIGNAL")
    for k in ("Expected Move", "Remaining Move", "Option", "Strike", "Expiry", "Risk",
              "Expected Net Reward", "Expected Value"):
        assert ex[k] == NOT_EVALUATED


def test_rejection_and_watching_decisions():
    rej = build_explanation(_ctx(), _event(to="REJECTED", reason="failed_trigger"))
    assert rej["Decision"] == "NO TRADE - rejected (failed_trigger)"
    watch = build_explanation(_ctx(), _event(to="CONFIRMING", reason="displacement_break", level=25008.0))
    assert watch["Decision"] == "WATCHING - confirming"
    assert "trigger candidate: displacement_break" in watch["Confirmation"]


def test_missing_context_degrades_to_na_not_errors():
    bare = ContextSnapshot(ts=dt.datetime(2026, 9, 16, 9, 20, tzinfo=IST), underlying="SENSEX", trigger_tf="5m",
                           spot=80000.0, session_phase="open_drive", quality="OK", bar_index=0)
    ev = StageEvent("s", "SENSEX", "down", "NO_SETUP", "EARLY_DEVELOPMENT", 0.3, "evidence_threshold", 0)
    ex = build_explanation(bare, ev)
    assert ex["Trend"] == "n/a" and ex["Location"] == "no reference level within reach"
    assert ex["Structure"] == "no recent structure event" and ex["Volume"].startswith("no volume")
    assert "PE" in ex["Direction"]


def test_render_is_one_line_per_key():
    text = render(build_explanation(_ctx(), _event()))
    lines = text.splitlines()
    assert len(lines) == 20 and lines[0].startswith("Direction: ") and lines[-1].startswith("Decision: ")
