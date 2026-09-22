import datetime as dt

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.scoring import CAPS, PENALTIES, ExternalInputs, Score, rank, score
from trading_bot.engine.strategies.base import Candidate
from trading_bot.timeutil import IST

SPOT, ATR = 25000.0, 20.0


def _ctx(**over) -> ContextSnapshot:
    base = dict(
        ts=dt.datetime(2026, 9, 16, 10, 30, tzinfo=IST), underlying="NIFTY", trigger_tf="5m", spot=SPOT,
        session_phase="10:00-11:30", quality="OK", bar_index=20,
        trends={"1d": {"label": "BULL"}, "30m": {"label": "BULL"}, "5m": {"label": "STRONG_BULL"}, "1m": {"label": "BULL"}},
        alignment={"label": "STRONG_TREND_ALIGNMENT", "direction_preference": "up"},
        regime={"primary": "BULL"},
        structure={"last_event": {"kind": "bos_up", "index": 19, "price": SPOT - 10}, "mss": "mss_up",
                   "recent_sweeps": [{"side": "below"}]},
        levels={"nearest_above": {"name": "pdh", "price": SPOT + 60, "distance_atr": 3.0, "touches": 0},
                "nearest_below": {"name": "pdl", "price": SPOT - 4, "distance_atr": -0.2, "touches": 3}},
        price_action={"labels": ["bullish_engulfing", "displacement"], "anatomy": {"close_loc": 0.95, "bullish": True}},
        indicators={"atr": ATR, "rsi": 62.0, "macd_hist": 0.5, "adx": 30.0, "atr_percentile": 55.0},
        volume={"relative_volume": 2.0, "obv_slope": 0.5},
    )
    base.update(over)
    return ContextSnapshot(**base)


def _cand(direction="up", counter=False, **over) -> Candidate:
    base = dict(strategy="TREND_PULLBACK", version="0.1", underlying="NIFTY", direction=direction, entry_ref=SPOT,
                invalidation=SPOT - 30 if direction == "up" else SPOT + 30, target_ref=SPOT + 60 if direction == "up" else SPOT - 60,
                confirmation="x", counter_trend=counter)
    base.update(over)
    return Candidate(**base)


def test_a_plus_context_scores_near_the_top_of_context_components():
    s = score(_ctx(), _cand())
    assert isinstance(s, Score)
    assert set(s.components) == set(CAPS)
    for k in ("price_action", "structure", "key_location", "mtf_alignment", "volume", "momentum", "volatility"):
        assert s.components[k] >= 0.8 * CAPS[k], (k, s.components[k])
    # no option/liquidity facts -> those two score zero and no penalty
    assert s.components["liquidity_execution"] == 0 and s.components["option_quality"] == 0
    assert s.penalties == {} and 75 <= s.total <= 90


def test_components_never_exceed_caps_and_total_is_bounded():
    s = score(_ctx(), _cand(), ExternalInputs(spread_pct=0.1, open_interest=100000, theta_pct_of_premium=0.01))
    assert all(0 <= v <= CAPS[k] for k, v in s.components.items())
    assert s.total == min(100, s.raw) and s.total <= 100


def test_bad_context_scores_low_and_lists_penalties():
    ctx = _ctx(
        trends={"1d": {"label": "BEAR"}, "30m": {"label": "BEAR"}, "5m": {"label": "BULL"}, "1m": {"label": "NEUTRAL"}},
        alignment={"label": "COUNTER_TREND", "direction_preference": "down"}, regime={"primary": "CHOPPY"},
        structure={"last_event": {"kind": "bos_down", "index": 19, "price": SPOT}, "recent_sweeps": []},
        levels={"nearest_above": {"name": "swing_high", "price": SPOT + 5, "distance_atr": 0.25, "touches": 0},
                "nearest_below": {"name": "ema200", "price": SPOT - 50, "distance_atr": -2.5, "touches": 0}},
        price_action={"labels": ["doji"], "anatomy": {"close_loc": 0.3, "bullish": False}},
        indicators={"atr": ATR, "rsi": 80.0, "macd_hist": -0.2, "adx": 12.0, "atr_percentile": 5.0,
                    "vwap_distance_atr": -1.2, "vwap_slope_atr": -0.4},  # long, below a falling VWAP
        volume={"relative_volume": 0.5},
    )
    s = score(ctx, _cand(counter=True), ExternalInputs(spread_pct=5.0, open_interest=100, theta_pct_of_premium=0.2,
                                                     remaining_move_atr=0.2, move_consumed_atr=3.0))
    assert s.total == 0
    assert set(s.penalties) == set(PENALTIES)


def test_vwap_bias_adds_to_volume_and_penalises_only_when_both_side_and_drift_oppose():
    from trading_bot.engine.scoring import vwap_bias
    base = dict(volume={"relative_volume": 1.4, "obv_slope": None})
    ind = {"atr": ATR, "rsi": 55.0, "macd_hist": 0.1, "adx": 22.0, "atr_percentile": 50.0}
    neutral = score(_ctx(indicators=ind, **base), _cand())
    with_both = score(_ctx(indicators={**ind, "vwap_distance_atr": 0.8, "vwap_slope_atr": 0.3}, **base), _cand())
    against_both = score(_ctx(indicators={**ind, "vwap_distance_atr": -0.8, "vwap_slope_atr": -0.3}, **base), _cand())
    against_side_only = score(_ctx(indicators={**ind, "vwap_distance_atr": -0.8, "vwap_slope_atr": 0.02}, **base), _cand())
    assert with_both.components["volume"] == neutral.components["volume"] + 3 and "against_vwap" not in with_both.penalties
    assert against_both.components["volume"] == neutral.components["volume"] and against_both.penalties["against_vwap"] == 5
    assert "against_vwap" not in against_side_only.penalties  # flat VWAP: a level, not a drift
    assert vwap_bias(_ctx(indicators={**ind, "vwap_distance_atr": 0.05, "vwap_slope_atr": None}), "up") == (None, None)
    assert vwap_bias(_ctx(indicators={**ind, "vwap_distance_atr": 0.5, "vwap_slope_atr": -0.2}), "down") == ("against", "with")


def test_direction_flips_the_read():
    up, down = score(_ctx(), _cand("up")), score(_ctx(), _cand("down"))
    assert up.total > down.total
    assert "regime_mismatch" in down.penalties and "conflicting_timeframes" not in up.penalties


def test_nearby_opposing_level_penalty_not_applied_when_it_is_the_target():
    ctx = _ctx(levels={**_ctx().levels, "nearest_above": {"name": "pdh", "price": SPOT + 5, "distance_atr": 0.25, "touches": 0}})
    blocked = score(ctx, _cand(target_ref=SPOT + 60))
    targeted = score(ctx, _cand(target_ref=SPOT + 5))
    assert "nearby_opposing_level" in blocked.penalties and "nearby_opposing_level" not in targeted.penalties


def test_unknown_external_facts_are_neutral():
    a = score(_ctx(), _cand())
    b = score(_ctx(), _cand(), ExternalInputs())
    assert a.total == b.total


# --- ranking ------------------------------------------------------------------------------


def _sc(total):
    return Score(total=total)


def test_rank_orders_by_score_and_selects_one_per_correlated_direction():
    a = _cand(strategy="A", underlying="NIFTY")
    b = _cand(strategy="B", underlying="BANKNIFTY")
    c = _cand(strategy="C", underlying="SENSEX", direction="down")
    out = rank([(a, _sc(70)), (b, _sc(85)), (c, _sc(60))], max_selected=3)
    assert [r.candidate.strategy for r in out] == ["B", "A", "C"]
    assert [r.selected for r in out] == [True, False, True]
    assert out[1].reason == "correlated_exposure" and out[2].reason == "selected"


def test_rank_respects_open_positions_and_min_score():
    a = _cand(strategy="A", underlying="NIFTY")
    b = _cand(strategy="B", underlying="BANKNIFTY", direction="down")
    out = rank([(a, _sc(80)), (b, _sc(30))], open_directions={"SENSEX": "up"}, min_score=40)
    assert out[0].reason == "correlated_exposure" and not out[0].selected
    assert out[1].reason == "below_min_score"


def test_rank_ties_are_deterministic():
    a = _cand(strategy="ZED"); b = _cand(strategy="ALPHA")
    assert [r.candidate.strategy for r in rank([(a, _sc(50)), (b, _sc(50))])] == ["ALPHA", "ZED"]
