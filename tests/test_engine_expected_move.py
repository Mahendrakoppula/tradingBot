import datetime as dt

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.expected_move import (
    MoveParams,
    distance_check,
    estimate,
    no_chase,
    option_response,
    time_fraction,
)
from trading_bot.engine.strategies.base import Candidate
from trading_bot.timeutil import IST

SPOT, ATR = 25000.0, 20.0


def _ctx(hour=10, minute=0, **over) -> ContextSnapshot:
    base = dict(
        ts=dt.datetime(2026, 9, 16, hour, minute, tzinfo=IST), underlying="NIFTY", trigger_tf="5m", spot=SPOT,
        session_phase="10:00-11:30", quality="OK", bar_index=20,
        trends={"5m": {"label": "BULL", "exhaustion": False}},
        alignment={}, regime={"primary": "BULL"}, structure={},
        levels={"session_high": SPOT + 20, "session_low": SPOT - 40,
                "nearest_above": {"name": "pdh", "price": SPOT + 80, "distance_atr": 4.0},
                "nearest_below": {"name": "ema20", "price": SPOT - 10, "distance_atr": -0.5}},
        price_action={}, indicators={"atr": ATR, "macd_hist": 0.3, "vwap": SPOT - 15}, volume={},
    )
    base.update(over)
    return ContextSnapshot(**base)


def _cand(direction="up", target=SPOT + 60, sl=SPOT - 30):
    return Candidate("T", "0.1", "NIFTY", direction, SPOT, sl, target, "x", False)


def test_time_fraction_runs_from_open_to_cutoff():
    p = MoveParams()
    assert time_fraction(dt.datetime(2026, 9, 16, 9, 15, tzinfo=IST), p) == 1.0
    assert time_fraction(dt.datetime(2026, 9, 16, 15, 20, tzinfo=IST), p) == 0.0
    assert time_fraction(dt.datetime(2026, 9, 16, 15, 29, tzinfo=IST), p) == 0.0
    mid = time_fraction(dt.datetime(2026, 9, 16, 12, 17, 30, tzinfo=IST), p)
    assert abs(mid - 0.5) < 0.01


def test_estimate_shrinks_with_time_range_and_regime():
    morning = estimate(_ctx(9, 30), _cand())
    afternoon = estimate(_ctx(14, 30), _cand())
    assert morning.remaining_atr > afternoon.remaining_atr > 0
    assert abs(morning.boundary - (SPOT + morning.remaining_atr * ATR)) < 0.05
    chop = estimate(_ctx(9, 30, regime={"primary": "CHOPPY"}), _cand())
    assert chop.remaining_atr < 0.5 * morning.remaining_atr and chop.regime_mult == 0.4
    used = estimate(_ctx(9, 30, levels={**_ctx().levels, "session_high": SPOT + 120, "session_low": SPOT - 120}), _cand())
    assert used.remaining_atr < morning.remaining_atr and used.day_range_used > 1.0


def test_estimate_is_capped_by_a_strong_level_ahead():
    ctx = _ctx(9, 30, levels={**_ctx().levels, "nearest_above": {"name": "pdh", "price": SPOT + 20, "distance_atr": 1.0}})
    em = estimate(ctx, _cand())
    assert em.remaining_atr == 1.0 and "capped by pdh" in em.notes
    weak = _ctx(9, 30, levels={**_ctx().levels, "nearest_above": {"name": "ema50", "price": SPOT + 20, "distance_atr": 1.0}})
    em2 = estimate(weak, _cand())
    assert 1.0 < em2.remaining_atr  # partial credit beyond a weak level


def test_trend_and_momentum_multipliers():
    strong = estimate(_ctx(trends={"5m": {"label": "STRONG_BULL", "exhaustion": False}}), _cand())
    against = estimate(_ctx(trends={"5m": {"label": "BEAR", "exhaustion": False}}), _cand())
    tired = estimate(_ctx(trends={"5m": {"label": "BULL", "exhaustion": True}}), _cand())
    assert strong.trend_mult == 1.15 and against.trend_mult == 0.8 and tired.trend_mult < 1.0
    assert estimate(_ctx(indicators={"atr": ATR, "macd_hist": -0.3}), _cand()).momentum_mult == 0.85


def test_no_chase_rejects_when_little_remains_or_extended():
    ctx = _ctx()
    em = estimate(ctx, _cand())
    assert no_chase(ctx, _cand(), em).ok
    late = _ctx(15, 10)
    v = no_chase(late, _cand(), estimate(late, _cand()))
    assert not v.ok and v.reason_code == "insufficient_remaining_move"
    stretched = _ctx(levels={**ctx.levels, "nearest_below": {"name": "ema20", "price": SPOT - 60, "distance_atr": -3.0}})
    v2 = no_chase(stretched, _cand(sl=SPOT - 70), estimate(stretched, _cand(sl=SPOT - 70)))
    assert not v2.ok and v2.reason_code == "extended_from_origin"
    far_vwap = _ctx(indicators={"atr": ATR, "macd_hist": 0.3, "vwap": SPOT - 60})
    v3 = no_chase(far_vwap, _cand(), estimate(far_vwap, _cand()))
    assert not v3.ok and v3.reason_code == "stretched_from_vwap"


def test_distance_check_blocks_or_downgrades_target():
    ctx = _ctx()
    em = estimate(ctx, _cand(target=SPOT + 30))
    ok = distance_check(ctx, _cand(target=SPOT + 30), em)
    assert ok.ok and ok.blocked_by is None and ok.target_ref == SPOT + 30
    # a target far beyond the realistic move is pulled back to the expected-move boundary
    far = distance_check(ctx, _cand(target=SPOT + 60), estimate(ctx, _cand(target=SPOT + 60)))
    assert far.ok and far.blocked_by == "expected_move_boundary" and far.target_ref < SPOT + 60
    blocked = _ctx(levels={**ctx.levels, "nearest_above": {"name": "swing_high", "price": SPOT + 5, "distance_atr": 0.25}})
    b = distance_check(blocked, _cand(), estimate(blocked, _cand()))
    assert not b.ok and b.reason_code == "target_blocked_by_level" and b.blocked_by == "swing_high"
    closer = _ctx(levels={**ctx.levels, "nearest_above": {"name": "session_high", "price": SPOT + 30, "distance_atr": 1.5}})
    d = distance_check(closer, _cand(target=SPOT + 60), estimate(closer, _cand(target=SPOT + 60)))
    assert d.ok and d.target_ref == SPOT + 30 and d.blocked_by == "session_high"
    # a target that IS the level ahead is fine
    same = distance_check(closer, _cand(target=SPOT + 30), estimate(closer, _cand(target=SPOT + 30)))
    assert same.ok and same.blocked_by is None


def test_option_response_is_convex_and_bounded():
    small = option_response(10, premium=100, delta=0.4, gamma=0.002)
    big = option_response(20, premium=100, delta=0.4, gamma=0.002)
    assert big > 2 * small  # gamma makes it convex
    assert option_response(0, premium=100, delta=0.4, gamma=0.002, theta_per_day=8, hold_fraction_of_day=0.5) == -4.0
    assert option_response(0, premium=3, delta=0.4, gamma=0.0, theta_per_day=40) == -3.0  # cannot lose more than premium
