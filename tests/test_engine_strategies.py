import datetime as dt

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.strategies import FAMILIES, Candidate, NoTrade, StrategyParams, fingerprint, route
from trading_bot.engine.strategies.families import (
    CompressionBreakoutRetest,
    EMAPullback,
    LiquiditySweepBOS,
    MomentumExpansion,
    MTFConfluence,
    OpeningRangeBreakout,
    PDHPDLTrap,
    RangeExtremeReversal,
    TrendPullbackContinuation,
    VWAPReclaim,
)
from trading_bot.timeutil import IST

P = StrategyParams()
ATR = 20.0
SPOT = 25000.0


def _ctx(**over) -> ContextSnapshot:
    """A quiet, neutral context: BULL 5m, TREND_ALIGNMENT up, regime BULL,
    ATR 20, no levels close by. Tests override what their family needs."""
    base = dict(
        ts=dt.datetime(2026, 9, 16, 10, 30, tzinfo=IST), underlying="NIFTY", trigger_tf="5m", spot=SPOT,
        session_phase="10:00-11:30", quality="OK", bar_index=20,
        trends={"1d": {"label": "BULL", "score": 0.5}, "30m": {"label": "BULL", "score": 0.5},
                "5m": {"label": "BULL", "score": 0.5}, "1m": {"label": "NEUTRAL", "score": 0.0}},
        alignment={"label": "TREND_ALIGNMENT", "direction_preference": "up", "weighted_score": 0.6},
        regime={"primary": "BULL", "recent_primaries": ["BULL"] * 6},
        structure={"last_event": None, "swing_high": SPOT + 60, "swing_low": SPOT - 60, "recent_sweeps": []},
        levels={"nearest_above": {"name": "pdh", "price": SPOT + 60, "distance": 60, "distance_atr": 3.0, "touches": 0},
                "nearest_below": {"name": "pdl", "price": SPOT - 60, "distance": -60, "distance_atr": -3.0, "touches": 0}},
        price_action={"labels": [], "anatomy": {"range": 10.0, "close_loc": 0.5, "bullish": True}, "sweep": None},
        indicators={"atr": ATR, "rsi": 55.0, "macd_hist": 0.3, "adx": 24.0, "ema20": SPOT - 30, "ema50": SPOT - 50,
                    "vwap": SPOT - 20, "vwap_prev": SPOT - 21, "close_prev": SPOT - 5},
        volume={"relative_volume": 1.0, "volume_proxy": "futures"},
    )
    base.update(over)
    return ContextSnapshot(**base)


def _near_below(name, price, touches=0):
    return {"nearest_below": {"name": name, "price": price, "distance": price - SPOT, "distance_atr": (price - SPOT) / ATR, "touches": touches},
            "nearest_above": {"name": "pdh", "price": SPOT + 60, "distance": 60, "distance_atr": 3.0, "touches": 0}}


def _bos_up(index=19, price=SPOT - 10):
    return {"last_event": {"kind": "bos_up", "index": index, "price": price}, "swing_high": SPOT + 60, "swing_low": SPOT - 60, "recent_sweeps": []}


# --- invariants across all families ----------------------------------------------------


def test_every_family_has_a_spec_and_a_versioned_name():
    names = {f.spec.name for f in FAMILIES}
    assert len(FAMILIES) == 10 and len(names) == 10
    assert all(f.spec.version == "0.1" and f.spec.tier in (1, 2, 3) for f in FAMILIES)
    assert sorted(f.spec.tier for f in FAMILIES) == [1, 1, 1, 1, 2, 2, 3, 3, 3, 3]


def test_quiet_context_produces_no_trades():
    for fam in FAMILIES:
        for d in ("up", "down"):
            r = fam.evaluate(_ctx(), d, P)
            assert isinstance(r, NoTrade), (fam.spec.name, d, r)
            assert r.reason_code


# --- one positive scenario per family --------------------------------------------------


def test_liquidity_sweep_bos():
    ctx = _ctx(structure={**_bos_up(index=20), "recent_sweeps": [
        {"level_name": "pdl", "level": SPOT - 60, "side": "below", "excess": 5, "excess_atr": 0.25, "bar_index": 18, "wick": SPOT - 65}]})
    c = LiquiditySweepBOS().evaluate(ctx, "up", P)
    assert isinstance(c, Candidate)
    assert c.invalidation == round(SPOT - 65 - 0.25 * ATR, 2) and c.target_ref == SPOT + 60
    assert c.confirmation == "sweep_of_pdl_then_bos_up" and c.option_type == "CE" and not c.counter_trend
    # a break that happened BEFORE the sweep does not count
    ctx2 = _ctx(structure={**ctx.structure, "last_event": {"kind": "bos_up", "index": 17, "price": SPOT - 10}})
    assert LiquiditySweepBOS().evaluate(ctx2, "up", P).reason_code == "structure_break_precedes_sweep"


def test_compression_breakout_retest():
    ctx = _ctx(regime={"primary": "BREAKOUT", "recent_primaries": ["COMPRESSION"] * 4 + ["BREAKOUT", "BREAKOUT"]},
               levels=_near_below("or_high", SPOT - 5), price_action={"labels": ["displacement"], "anatomy": {}, "sweep": None})
    c = CompressionBreakoutRetest().evaluate(ctx, "up", P)
    assert isinstance(c, Candidate) and c.confirmation == "retest_hold" and c.evidence["retested"] is True
    assert c.invalidation < SPOT - 5
    far = _ctx(regime=ctx.regime, levels=_near_below("or_high", SPOT - 30), price_action={"labels": [], "anatomy": {}, "sweep": None})
    assert CompressionBreakoutRetest().evaluate(far, "up", P).reason_code == "extended_from_breakout_level"


def test_trend_pullback_continuation():
    ctx = _ctx(levels=_near_below("ema20", SPOT - 6), price_action={"labels": ["bullish_pin"], "anatomy": {}, "sweep": None})
    c = TrendPullbackContinuation().evaluate(ctx, "up", P)
    assert isinstance(c, Candidate) and c.confirmation == "pullback_rejection"
    assert c.invalidation == round(SPOT - 6 - 0.25 * ATR, 2)
    assert TrendPullbackContinuation().evaluate(_ctx(levels=_near_below("ema20", SPOT - 6)), "up", P).reason_code == "no_continuation_confirmation"
    weak = _ctx(alignment={"label": "WEAK_ALIGNMENT", "direction_preference": "up"}, levels=_near_below("ema20", SPOT - 6))
    assert TrendPullbackContinuation().evaluate(weak, "up", P).reason_code == "not_aligned"


def test_mtf_confluence():
    ctx = _ctx(alignment={"label": "STRONG_TREND_ALIGNMENT", "direction_preference": "up"}, structure=_bos_up())
    c = MTFConfluence().evaluate(ctx, "up", P)
    assert isinstance(c, Candidate) and c.evidence["tfs"] == ["1d", "30m", "5m"] and c.confirmation == "bos_up"
    assert c.invalidation == round(SPOT - 60 - 0.25 * ATR, 2)
    two = _ctx(alignment=ctx.alignment, structure=_bos_up(), trends={**ctx.trends, "30m": {"label": "NEUTRAL", "score": 0}})
    assert MTFConfluence().evaluate(two, "up", P).reason_code == "fewer_than_three_tfs_agree"


def test_opening_range_breakout():
    lv = {"or_high": SPOT - 8, "or_low": SPOT - 48, **_near_below("or_high", SPOT - 8)}
    ctx = _ctx(session_phase="09:15-10:00", levels=lv, volume={"relative_volume": 1.4, "volume_proxy": "futures"})
    c = OpeningRangeBreakout().evaluate(ctx, "up", P)
    assert isinstance(c, Candidate) and c.invalidation == (SPOT - 8 + SPOT - 48) / 2
    assert c.target_ref == SPOT - 8 + 40  # measured move (pdh at +60 is further)
    late = _ctx(session_phase="13:00-14:00", levels=lv, volume=ctx.volume)
    assert OpeningRangeBreakout().evaluate(late, "up", P).reason_code == "outside_orb_window"
    thin = _ctx(session_phase="09:15-10:00", levels=lv, volume={"relative_volume": 0.8, "volume_proxy": "futures"})
    assert OpeningRangeBreakout().evaluate(thin, "up", P).reason_code == "breakout_without_participation"


def test_vwap_reclaim():
    ind = {"atr": ATR, "vwap": SPOT - 5, "vwap_prev": SPOT - 5, "close_prev": SPOT - 12, "macd_hist": 0.1, "adx": 20}
    ctx = _ctx(indicators=ind, structure=_bos_up())
    c = VWAPReclaim().evaluate(ctx, "up", P)
    assert isinstance(c, Candidate) and c.confirmation == "vwap_reclaim" and c.invalidation == round(SPOT - 5 - 0.5 * ATR, 2)
    assert VWAPReclaim().evaluate(_ctx(indicators=ind), "up", P).reason_code == "vwap_cross_without_structure"
    no_cross = _ctx(indicators={**ind, "close_prev": SPOT - 2}, structure=_bos_up())
    assert VWAPReclaim().evaluate(no_cross, "up", P).reason_code == "no_vwap_cross"


def test_pdh_pdl_trap_is_a_reversal_with_vwap_target():
    sweep = {"level_name": "pdl", "level": SPOT - 3, "side": "below", "excess": 4, "excess_atr": 0.2, "bar_index": 20, "wick": SPOT - 7}
    ctx = _ctx(structure={"last_event": None, "recent_sweeps": [sweep], "swing_high": SPOT + 60, "swing_low": SPOT - 60},
               price_action={"labels": ["bullish_engulfing"], "anatomy": {}, "sweep": sweep},
               indicators={"atr": ATR, "vwap": SPOT + 25, "vwap_prev": SPOT + 25, "close_prev": SPOT - 4})
    c = PDHPDLTrap().evaluate(ctx, "up", P)
    assert isinstance(c, Candidate) and c.target_ref == SPOT + 25 and c.invalidation == round(SPOT - 7 - 0.25 * ATR, 2)
    below = _ctx(spot=SPOT - 10, structure=ctx.structure, price_action=ctx.price_action)
    assert PDHPDLTrap().evaluate(below, "up", P).reason_code == "not_back_inside_level"


def test_ema_pullback():
    ctx = _ctx(levels=_near_below("ema50", SPOT - 4), price_action={"labels": ["rejection_of_low"], "anatomy": {}, "sweep": None})
    c = EMAPullback().evaluate(ctx, "up", P)
    assert isinstance(c, Candidate) and c.confirmation == "rejection_at_ema50"
    assert c.invalidation == round(SPOT - 50 - 0.25 * ATR, 2)  # anchored on the lower of the two EMAs
    assert EMAPullback().evaluate(_ctx(levels=_near_below("ema50", SPOT - 4)), "up", P).reason_code == "no_rejection_at_ema"


def test_momentum_expansion():
    ctx = _ctx(regime={"primary": "EXPANSION", "recent_primaries": []},
               price_action={"labels": ["displacement"], "anatomy": {"range": 30.0, "close_loc": 0.9, "bullish": True}, "sweep": None},
               volume={"relative_volume": 1.8, "volume_proxy": "futures"})
    c = MomentumExpansion().evaluate(ctx, "up", P)
    assert isinstance(c, Candidate)
    bar_low = SPOT - 0.9 * 30.0
    assert c.invalidation == round(bar_low - 0.25 * ATR, 2)
    against = _ctx(regime=ctx.regime, price_action={**ctx.price_action, "anatomy": {"range": 30.0, "close_loc": 0.1, "bullish": False}}, volume=ctx.volume)
    assert MomentumExpansion().evaluate(against, "up", P).reason_code == "displacement_against_direction"
    thin = _ctx(regime=ctx.regime, price_action=ctx.price_action, volume={"relative_volume": 0.9, "volume_proxy": "futures"})
    assert MomentumExpansion().evaluate(thin, "up", P).reason_code == "volume_not_expanding"


def test_range_extreme_reversal_targets_the_midpoint():
    ctx = _ctx(regime={"primary": "RANGE", "recent_primaries": ["RANGE"] * 6},
               alignment={"label": "NEUTRAL", "direction_preference": "none"},
               trends={tf: {"label": "NEUTRAL", "score": 0.0} for tf in ("1d", "30m", "5m", "1m")},
               levels=_near_below("swing_low", SPOT - 5), structure={"last_event": None, "swing_high": SPOT + 55, "swing_low": SPOT - 5, "recent_sweeps": []},
               price_action={"labels": ["bullish_pin"], "anatomy": {}, "sweep": None})
    c = RangeExtremeReversal().evaluate(ctx, "up", P)
    assert isinstance(c, Candidate) and c.target_ref == SPOT + 25 and c.invalidation == round(SPOT - 5 - 0.35 * ATR, 2)
    assert RangeExtremeReversal().evaluate(_ctx(levels=_near_below("swing_low", SPOT - 5)), "up", P).reason_code == "not_a_range"


# --- routing ------------------------------------------------------------------------------


def test_route_blocks_no_trade_regime_and_bad_quality():
    assert route(_ctx(regime={"primary": "NO_TRADE"}), "up").gate == "regime_no_trade"
    assert route(_ctx(regime={"primary": "UNSTABLE"}), "up").gate == "regime_unstable"
    assert route(_ctx(quality="STALE"), "up").gate == "data_quality_stale"


def test_route_records_every_family_decision():
    ctx = _ctx(levels=_near_below("ema20", SPOT - 6), price_action={"labels": ["bullish_pin"], "anatomy": {}, "sweep": None})
    r = route(ctx, "up")
    assert r.gate is None
    names = {c.strategy for c in r.candidates}
    assert "TREND_PULLBACK" in names and "EMA_PULLBACK" in names
    assert len(r.candidates) + len(r.rejections) == 10
    assert r.best_tier == 1
    assert all(c.evidence["fingerprint"] for c in r.candidates)
    reasons = {n.strategy: n.reason_code for n in r.rejections}
    assert reasons["RANGE_EXTREME_REVERSAL"] == "regime_incompatible"
    assert reasons["ORB"] == "opening_range_incomplete"  # 10:00-11:30 is inside the ORB window; no OR levels in this ctx


def test_route_counter_trend_requires_clearance_and_family_permission():
    sweep = {"level_name": "pdl", "level": SPOT - 3, "side": "below", "excess": 4, "excess_atr": 0.2, "bar_index": 20, "wick": SPOT - 7}
    ctx = _ctx(alignment={"label": "COUNTER_TREND", "direction_preference": "down"},
               structure={"last_event": {"kind": "bos_up", "index": 20, "price": SPOT - 1}, "recent_sweeps": [sweep], "swing_high": SPOT + 60, "swing_low": SPOT - 60},
               price_action={"labels": ["bullish_engulfing"], "anatomy": {}, "sweep": sweep})
    blocked = route(ctx, "up", counter_trend_cleared=False)
    assert blocked.candidates == []
    assert {n.reason_code for n in blocked.rejections} >= {"counter_trend_evidence_insufficient", "counter_trend_not_allowed"}
    cleared = route(ctx, "up", counter_trend_cleared=True)
    names = {c.strategy for c in cleared.candidates}
    assert "LIQUIDITY_SWEEP_BOS" in names and "PDH_PDL_TRAP" in names
    assert all(c.counter_trend for c in cleared.candidates)
    assert "TREND_PULLBACK" not in names  # never counter-trend


def test_route_family_hint_prefilter():
    ctx = _ctx(regime={"primary": "BREAKOUT", "recent_primaries": ["COMPRESSION"] * 4 + ["BREAKOUT", "BREAKOUT"]},
               levels=_near_below("or_high", SPOT - 5), price_action={"labels": ["displacement"], "anatomy": {}, "sweep": None})
    r = route(ctx, "up", family_hint="vwap_reclaim")
    reasons = {n.strategy: n.reason_code for n in r.rejections}
    assert reasons.get("COMPRESSION_BREAKOUT") == "family_hint_mismatch"


# --- fingerprint ----------------------------------------------------------------------------


def test_fingerprint_is_closed_vocabulary_and_deterministic():
    ctx = _ctx(levels=_near_below("ema20", SPOT - 6), price_action={"labels": ["bullish_pin"], "anatomy": {}, "sweep": None},
               volume={"relative_volume": 1.6, "volume_proxy": "futures"})
    c = TrendPullbackContinuation().evaluate(ctx, "up", P)
    fp = fingerprint(ctx, c)
    assert fp == "NIFTY|TREND_PULLBACK|BULL|DAILY+30M+5M|EMA|NO_SWEEP|NO_BREAK|REJECTION|HIGH_VOLUME|CE|MORNING"
    assert fingerprint(ctx, c) == fp
    down = _ctx(session_phase="14:00-15:00", volume={"relative_volume": None, "volume_proxy": "none"})
    c2 = Candidate("X", "0.1", "SENSEX", "down", SPOT, SPOT + 10, SPOT - 30, "vwap_loss", False)
    assert fingerprint(down, c2).split("|")[-3:] == ["NO_VOLUME", "PE", "AFTERNOON"]


def test_counter_trend_is_about_direction_not_timeframe_conflict():
    """First shadow session finding: align() says COUNTER_TREND whenever any
    lower TF disagrees; that must not make BOTH directions counter-trend."""
    from trading_bot.engine.strategies.base import is_counter_trend
    # regime BULL, 5m BULL, 30m neutral, 1m dipping -> alignment label COUNTER_TREND, preference none
    ctx = _ctx(alignment={"label": "COUNTER_TREND", "direction_preference": "none", "weighted_score": 0.05},
               trends={"1d": {"label": "NEUTRAL", "score": 0.1}, "30m": {"label": "NEUTRAL", "score": 0.0},
                       "5m": {"label": "BULL", "score": 0.6}, "1m": {"label": "BEAR", "score": -0.4}})
    assert not is_counter_trend(ctx, "up") and not is_counter_trend(ctx, "down")
    # routing no longer blocks on counter-trend; what remains are the families' own alignment rules
    r = route(_ctx(alignment=ctx.alignment, trends=ctx.trends, levels=_near_below("ema20", SPOT - 6),
                   price_action={"labels": ["bullish_pin"], "anatomy": {}, "sweep": None}), "up")
    reasons = {n.reason_code for n in r.rejections}
    assert not any(code.startswith("counter_trend") for code in reasons), reasons
    # and with a weak preference in the trade direction the pullback families do trade it
    weak = dict(ctx.alignment, label="WEAK_ALIGNMENT", direction_preference="up")
    r2 = route(_ctx(alignment=weak, trends=ctx.trends, levels=_near_below("ema20", SPOT - 6),
                    price_action={"labels": ["bullish_pin"], "anatomy": {}, "sweep": None}), "up")
    assert "EMA_PULLBACK" in {c.strategy for c in r2.candidates}
    # against a decisive daily read it IS counter-trend, whatever the label says
    strong_daily = _ctx(alignment={"label": "WEAK_ALIGNMENT", "direction_preference": "none"},
                        trends={"1d": {"label": "BEAR", "score": -0.7}, "30m": {"label": "NEUTRAL", "score": 0.0},
                                "5m": {"label": "BULL", "score": 0.5}, "1m": {"label": "BULL", "score": 0.3}})
    assert is_counter_trend(strong_daily, "up") and not is_counter_trend(strong_daily, "down")
    # against the weighted preference is counter-trend
    assert is_counter_trend(_ctx(alignment={"label": "TREND_ALIGNMENT", "direction_preference": "down"}), "up")
