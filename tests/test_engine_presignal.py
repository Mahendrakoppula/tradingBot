import datetime as dt

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.presignal import (
    STAGES,
    TERMINAL_STAGES,
    PreSignalConfig,
    PreSignalTracker,
    StageEvent,
)
from trading_bot.timeutil import IST

CFG = PreSignalConfig()


def _ctx(bar: int, spot: float = 25000.0, **over) -> ContextSnapshot:
    """Default context carries NO evidence; tests opt in via overrides."""
    base = dict(
        ts=dt.datetime(2026, 9, 16, 9, 15, tzinfo=IST) + dt.timedelta(minutes=5 * bar),
        underlying="NIFTY", trigger_tf="5m", spot=spot, session_phase="open_drive", quality="OK",
        bar_index=bar, indicators={"atr": 20.0}, levels={}, alignment={}, regime={}, structure={},
        price_action={"labels": []}, volume={}, trends={},
    )
    base.update(over)
    return ContextSnapshot(**base)


# evidence bundles ------------------------------------------------------------

def _near_level(spot=25000.0, level=25008.0, touches=0):
    # 8 points / 20 ATR = 0.4 ATR <= 0.5 proximity
    return {"nearest_above": {"name": "pdh", "price": level, "distance": level - spot,
                              "distance_atr": (level - spot) / 20.0, "touches": touches}}


UP_EVIDENCE = dict(
    levels=_near_level(),                                      # level_approach
    regime={"primary": "COMPRESSION"},                         # compression
    indicators={"atr": 20.0, "ema20": 24990.0, "ema50": 24995.0, "macd_hist": 0.5, "rsi": 58.0},  # ema_compression + momentum
    volume={"relative_volume": 1.5},                           # volume_buildup
    alignment={"label": "TREND_ALIGNMENT", "direction_preference": "up"},  # htf_alignment
)


def _stages(events):
    return [e.to_stage for e in events]


def _up(events):
    return [e for e in events if e.direction == "up"]


# --- tests ------------------------------------------------------------------------


def test_stage_names_match_spec():
    assert STAGES[0] == "NO_SETUP" and STAGES[4] == "TRADE_READY"
    assert TERMINAL_STAGES <= set(STAGES) and len(STAGES) == 9


def test_no_setup_without_enough_evidence():
    t = PreSignalTracker(CFG)
    assert t.update(_ctx(0)) == []
    # one piece of evidence only
    assert t.update(_ctx(1, levels=_near_level())) == []
    assert t.active() == []


def test_two_evidence_opens_early_development():
    t = PreSignalTracker(CFG)
    ups = _up(t.update(_ctx(0, levels=_near_level(), volume={"relative_volume": 1.5})))
    assert _stages(ups) == ["EARLY_DEVELOPMENT"]
    assert ups[0].from_stage == "NO_SETUP" and ups[0].reason_code == "evidence_threshold"
    assert "level_approach" in ups[0].details["evidence"]


def test_presignal_requires_location_evidence():
    t = PreSignalTracker(CFG)
    # four pieces of evidence but none of them is a level approach
    no_loc = dict(
        regime={"primary": "COMPRESSION"}, volume={"relative_volume": 1.5},
        indicators={"atr": 20.0, "ema20": 24990.0, "ema50": 24995.0, "macd_hist": 0.5},
        alignment={"label": "TREND_ALIGNMENT", "direction_preference": "up"},
    )
    t.update(_ctx(0, **no_loc))
    ev = t.update(_ctx(1, **no_loc))
    assert all(e.to_stage != "PRE_SIGNAL" for e in ev)
    up = t.setups[("NIFTY", "up")]
    assert up.stage == "EARLY_DEVELOPMENT" and len(up.evidence) >= 4


def test_full_happy_path_to_trade_ready():
    t = PreSignalTracker(CFG)
    assert "EARLY_DEVELOPMENT" in _stages(_up(t.update(_ctx(0, **UP_EVIDENCE))))
    assert _stages(_up(t.update(_ctx(1, **UP_EVIDENCE)))) == ["PRE_SIGNAL"]
    # trigger: displacement close beyond the level
    trig = dict(UP_EVIDENCE, price_action={"labels": ["displacement"]})
    up2 = _up(t.update(_ctx(2, spot=25012.0, **trig)))
    assert _stages(up2) == ["CONFIRMING"] and up2[0].reason_code == "displacement_break"
    assert up2[0].details["level"] == 25008.0
    # confirmation bar closes still beyond the level -> TRADE_READY
    up3 = _up(t.update(_ctx(3, spot=25015.0, **UP_EVIDENCE)))
    assert _stages(up3) == ["TRADE_READY"] and up3[0].reason_code == "confirmation_closed"
    assert up3[0].details["counter_trend"] is False
    assert t.setups[("NIFTY", "up")].stage == "TRADE_READY"
    # a StageEvent is plain data
    assert isinstance(up3[0], StageEvent) and up3[0].setup_id == up2[0].setup_id


def test_confirming_rejected_when_price_falls_back_through_level():
    t = PreSignalTracker(CFG)
    t.update(_ctx(0, **UP_EVIDENCE))
    t.update(_ctx(1, **UP_EVIDENCE))
    t.update(_ctx(2, spot=25012.0, **dict(UP_EVIDENCE, price_action={"labels": ["displacement"]})))
    up = _up(t.update(_ctx(3, spot=25000.0, **UP_EVIDENCE)))
    assert _stages(up) == ["REJECTED"] and up[0].reason_code == "failed_trigger"
    assert ("NIFTY", "up") not in t.setups  # terminal setups are dropped


def test_extended_when_chased_too_far():
    t = PreSignalTracker(CFG)
    t.update(_ctx(0, **UP_EVIDENCE))
    t.update(_ctx(1, **UP_EVIDENCE))
    t.update(_ctx(2, spot=25012.0, **dict(UP_EVIDENCE, price_action={"labels": ["displacement"]})))
    up = _up(t.update(_ctx(3, spot=25008.0 + 2.5 * 20.0, **UP_EVIDENCE)))  # 2.5 ATR past trigger
    assert _stages(up) == ["EXTENDED"] and up[0].details["extension_atr"] == 2.5


def test_counter_trend_needs_more_evidence():
    t = PreSignalTracker(CFG)
    # alignment says COUNTER_TREND -> htf_alignment evidence is absent -> 4 keys (incl. location)
    ct = dict(UP_EVIDENCE, alignment={"label": "COUNTER_TREND", "direction_preference": "down"})
    t.update(_ctx(0, **ct))
    t.update(_ctx(1, **ct))
    assert t.setups[("NIFTY", "up")].stage == "PRE_SIGNAL"
    t.update(_ctx(2, spot=25012.0, **dict(ct, price_action={"labels": ["displacement"]})))
    assert t.setups[("NIFTY", "up")].stage == "CONFIRMING"
    ev = t.update(_ctx(3, spot=25015.0, **ct))
    assert all(e.to_stage != "TRADE_READY" for e in _up(ev))
    assert t.setups[("NIFTY", "up")].stage == "CONFIRMING"  # waits, does not promote
    # adding vwap + repeated tests reaches the counter-trend bar (6) -> allowed
    more = dict(ct, indicators=dict(ct["indicators"], vwap=25014.0),
                levels=_near_level(spot=25015.0, level=25008.0, touches=3))
    ev = _up(t.update(_ctx(4, spot=25015.0, **more)))
    assert _stages(ev) == ["TRADE_READY"] and ev[0].details["counter_trend"] is True


def test_confidence_decays_and_expires():
    cfg = PreSignalConfig(ttl_bars=50)  # make decay, not ttl, the expiry cause
    t = PreSignalTracker(cfg)
    t.update(_ctx(0, levels=_near_level(), volume={"relative_volume": 1.5}))
    s = t.setups[("NIFTY", "up")]
    assert s.confidence == cfg.confidence_start + 2 * cfg.confidence_per_evidence
    bar, last = 1, None
    while ("NIFTY", "up") in t.setups:
        last = t.update(_ctx(bar))
        bar += 1
    up = _up(last)
    assert _stages(up) == ["EXPIRED"] and up[0].reason_code == "confidence_decayed"
    assert up[0].confidence < cfg.min_conf


def test_ttl_expiry_without_progress():
    cfg = PreSignalConfig(ttl_bars=3, decay=1.0)  # no decay, only the clock
    t = PreSignalTracker(cfg)
    t.update(_ctx(0, levels=_near_level(), volume={"relative_volume": 1.5}))
    assert t.update(_ctx(1)) == [] and t.update(_ctx(2)) == []
    up = _up(t.update(_ctx(3)))
    assert _stages(up) == ["EXPIRED"] and up[0].reason_code == "ttl_exceeded" and up[0].details["idle_bars"] == 3


def test_new_evidence_raises_confidence_and_resets_ttl():
    cfg = PreSignalConfig(ttl_bars=2, decay=1.0)
    t = PreSignalTracker(cfg)
    t.update(_ctx(0, levels=_near_level(), volume={"relative_volume": 1.5}))
    c0 = t.setups[("NIFTY", "up")].confidence
    t.update(_ctx(1, regime={"primary": "COMPRESSION"}))  # new evidence at bar 1
    s = t.setups[("NIFTY", "up")]
    assert s.confidence > c0 and s.last_progress_bar == 1
    assert t.update(_ctx(2)) == []  # 1 idle bar, ttl is 2
    assert _stages(t.update(_ctx(3))) == ["EXPIRED"]


def test_structure_break_trigger_and_exhaustion():
    t = PreSignalTracker(CFG)
    t.update(_ctx(0, **UP_EVIDENCE))
    t.update(_ctx(1, **UP_EVIDENCE))
    bos = dict(UP_EVIDENCE, structure={"last_event": {"kind": "bos_up", "index": 2, "price": 25008.0}})
    up = _up(t.update(_ctx(2, spot=25010.0, **bos)))
    assert _stages(up) == ["CONFIRMING"] and up[0].reason_code == "structure_break"
    up = _up(t.update(_ctx(3, spot=25012.0, **dict(UP_EVIDENCE, trends={"5m": {"label": "BULL", "exhaustion": True}}))))
    assert _stages(up) == ["EXHAUSTED"]


def test_down_direction_mirrors_up():
    t = PreSignalTracker(CFG)
    lv = {"nearest_below": {"name": "pdl", "price": 24992.0, "distance": 8.0, "distance_atr": 0.4, "touches": 3}}
    down = dict(levels=lv, regime={"primary": "COMPRESSION"}, volume={"relative_volume": 1.5},
                indicators={"atr": 20.0, "macd_hist": -0.4, "rsi": 42.0},
                alignment={"label": "STRONG_TREND_ALIGNMENT", "direction_preference": "down"})
    t.update(_ctx(0, **down))
    t.update(_ctx(1, **down))
    s = t.setups[("NIFTY", "down")]
    assert s.stage == "PRE_SIGNAL" and "repeated_tests" in s.evidence
    ev = t.update(_ctx(2, spot=24985.0, **dict(down, price_action={"labels": ["bearish_engulfing", "displacement"]})))
    d = [e for e in ev if e.direction == "down"]
    assert _stages(d) == ["CONFIRMING"] and d[0].reason_code == "displacement_break"
    ev = t.update(_ctx(3, spot=24980.0, **down))
    assert _stages([e for e in ev if e.direction == "down"]) == ["TRADE_READY"]
    assert t.setups[("NIFTY", "down")].family_hint == "compression_breakout"


def test_rejection_at_level_trigger():
    t = PreSignalTracker(CFG)
    t.update(_ctx(0, **UP_EVIDENCE))
    t.update(_ctx(1, **UP_EVIDENCE))
    up = _up(t.update(_ctx(2, **dict(UP_EVIDENCE, price_action={"labels": ["bullish_pin"]}))))
    assert _stages(up) == ["CONFIRMING"] and up[0].reason_code == "rejection_at_level"
    assert up[0].details["patterns"] == ["bullish_pin"]


def test_underlyings_are_tracked_independently():
    t = PreSignalTracker(CFG)
    t.update(_ctx(0, **UP_EVIDENCE))
    t.update(_ctx(0, underlying="BANKNIFTY", **UP_EVIDENCE))
    assert len(t.active()) == 2 and len(t.active("NIFTY")) == 1
