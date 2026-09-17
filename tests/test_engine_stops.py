import datetime as dt

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.stops import EXIT_REASONS, Plan, PlanRejected, StopParams, ThesisMonitor, build_plan
from trading_bot.engine.strategies.base import Candidate
from trading_bot.timeutil import IST

SPOT, ATR = 25000.0, 20.0


def _ctx(spot=SPOT, bar=10, **over) -> ContextSnapshot:
    base = dict(
        ts=dt.datetime(2026, 9, 16, 10, 30, tzinfo=IST), underlying="NIFTY", trigger_tf="5m", spot=spot,
        session_phase="10:00-11:30", quality="OK", bar_index=bar,
        trends={"5m": {"label": "BULL"}}, alignment={}, regime={"primary": "BULL"},
        structure={"last_event": None, "swing_low": SPOT - 40, "swing_high": SPOT + 80},
        levels={"nearest_above": {"name": "pdh", "price": SPOT + 120, "distance_atr": 6.0}},
        price_action={}, indicators={"atr": ATR, "vwap": SPOT - 30}, volume={},
    )
    base.update(over)
    return ContextSnapshot(**base)


def _cand(sl=SPOT - 30, target=SPOT + 60, direction="up"):
    return Candidate("T", "0.1", "NIFTY", direction, SPOT, sl, target, "x", False)


def _plan(**over) -> Plan:
    p = build_plan(_ctx(), _cand(), option_entry=150.0, delta=0.5, gamma=0.002)
    assert isinstance(p, Plan)
    return p


def test_fifteen_exit_reasons():
    assert len(EXIT_REASONS) == 15 and len(set(EXIT_REASONS)) == 15


def test_plan_translates_structural_stop_to_option_scale():
    p = _plan()
    assert p.stop_ref == SPOT - 30 and p.stop_atr == 1.5 and p.target1_ref == SPOT + 60
    assert p.target2_ref == SPOT + 96 and p.extended_ref == SPOT + 116  # 1 ATR beyond TP2 (3 ATR would sit inside it)
    assert p.option_entry == 150.0 and p.option_stop < 150.0 < p.option_target1 < p.option_target2
    # adverse move loses less than delta x move (gamma helps a long), favourable gains more
    assert 150.0 - p.option_stop < 0.5 * 30 and p.option_target1 - 150.0 > 0.5 * 60
    assert p.risk_per_unit == round(150.0 - p.option_stop, 2) and p.rr1 > 1.5


def test_plan_widens_tiny_stops_and_rejects_huge_ones():
    tiny = build_plan(_ctx(), _cand(sl=SPOT - 2), option_entry=150.0, delta=0.5, gamma=0.002)
    assert isinstance(tiny, Plan) and tiny.stop_atr == 0.3 and "widened" in tiny.notes[0]
    huge = build_plan(_ctx(), _cand(sl=SPOT - 80), option_entry=150.0, delta=0.5, gamma=0.002)
    assert isinstance(huge, PlanRejected) and huge.reason_code == "stop_too_wide"
    bad_t = build_plan(_ctx(), _cand(target=SPOT - 5), option_entry=150.0, delta=0.5, gamma=0.002)
    assert isinstance(bad_t, PlanRejected) and bad_t.reason_code == "target_not_beyond_entry"
    assert build_plan(_ctx(indicators={"atr": None}), _cand(), option_entry=150.0, delta=0.5, gamma=0.002).reason_code == "no_atr"


def test_plan_target_override_from_distance_check():
    p = build_plan(_ctx(), _cand(), option_entry=150.0, delta=0.5, gamma=0.002, target_ref=SPOT + 30)
    assert isinstance(p, Plan) and p.target1_ref == SPOT + 30 and p.target2_ref == SPOT + 48


def test_put_plan_mirrors():
    c = _cand(sl=SPOT + 30, target=SPOT - 60, direction="down")
    p = build_plan(_ctx(), c, option_entry=150.0, delta=-0.5, gamma=0.002)
    assert isinstance(p, Plan) and p.stop_ref == SPOT + 30 and p.target1_ref == SPOT - 60 and p.option_stop < 150 < p.option_target1


# --- thesis monitor -------------------------------------------------------------------------


def _open():
    m = ThesisMonitor()
    st = m.open(_plan(), quantity=150, bar_index=10, entry_vwap=SPOT - 30)
    return m, st


def test_stop_loss_and_priority_of_hard_exits():
    m, st = _open()
    assert m.check(_ctx(spot=SPOT + 5, bar=11), st) is None
    hit = m.check(_ctx(spot=SPOT - 31, bar=12), st)
    assert hit.reason == "STOP_LOSS" and hit.quantity == 150
    assert m.check(_ctx(bar=12), st, kill=True).reason == "MANUAL_KILL_SWITCH"
    assert m.check(_ctx(bar=12), st, emergency=True).reason == "EMERGENCY_EXIT"
    assert m.check(_ctx(bar=12), st, risk_limit_hit=True).reason == "RISK_LIMIT"
    assert m.check(_ctx(bar=12), st, eod=True).reason == "EOD_EXIT"
    assert m.check(_ctx(bar=12), st, option_spread_pct=12.0).reason == "OPTION_LIQUIDITY_FAILURE"


def test_target1_partial_then_breakeven_then_target2():
    m, st = _open()
    t1 = m.check(_ctx(spot=SPOT + 61, bar=12), st)
    assert t1.reason == "TARGET_1" and t1.quantity == 75 and st.target1_hit
    assert st.current_stop_ref >= SPOT  # breakeven after TP1
    assert m.check(_ctx(spot=SPOT + 70, bar=13), st) is None  # between targets, still riding
    t2 = m.check(_ctx(spot=SPOT + 97, bar=14), st)
    assert t2.reason in ("TARGET_2", "EXTENDED_TARGET") and t2.quantity == 150


def test_trailing_only_tightens_and_uses_structure():
    m, st = _open()
    assert m.check(_ctx(spot=SPOT + 16, bar=11), st) is None  # 0.8 ATR: breakeven
    assert st.current_stop_ref == SPOT and st.stop_moves[-1][2] == "breakeven"
    assert m.check(_ctx(spot=SPOT + 25, bar=12, structure={"last_event": None, "swing_low": SPOT + 12, "swing_high": SPOT + 80}), st) is None
    # ATR trail: best 25025 - 20 = 25005; structure trail: 25012 - 5 = 25007 -> the tighter one wins
    assert st.current_stop_ref == SPOT + 7
    before = st.current_stop_ref
    assert m.check(_ctx(spot=SPOT + 20, bar=13), st) is None  # pullback: stop must not loosen
    assert st.current_stop_ref == before
    hit = m.check(_ctx(spot=SPOT + 6, bar=14), st)
    assert hit.reason == "TRAILING_STOP"


def test_thesis_invalidations():
    m, st = _open()
    rev = m.check(_ctx(spot=SPOT + 3, bar=11, structure={"last_event": {"kind": "choch_down", "index": 11, "price": SPOT}}), st)
    assert rev.reason == "STRUCTURE_REVERSAL"
    m, st = _open()
    assert m.check(_ctx(spot=SPOT + 3, bar=11, regime={"primary": "BEAR"}), st).reason == "REGIME_CHANGE"
    m, st = _open()
    assert m.check(_ctx(spot=SPOT + 3, bar=11, regime={"primary": "NO_TRADE"}), st).reason == "REGIME_CHANGE"
    m, st = _open()
    assert m.check(_ctx(spot=SPOT + 3, bar=11, trends={"5m": {"label": "STRONG_BEAR"}}), st).reason == "THESIS_INVALIDATION"
    m, st = _open()
    # entered above VWAP (vwap SPOT-30), now decisively below it after having been in profit
    m.check(_ctx(spot=SPOT + 5, bar=11), st)
    lost = m.check(_ctx(spot=SPOT - 12, bar=12, indicators={"atr": ATR, "vwap": SPOT}), st)
    assert lost.reason == "VWAP_FAILURE"


def test_momentum_failure_after_stall():
    m, st = _open()
    for b in range(11, 20):
        r = m.check(_ctx(spot=SPOT + 2, bar=b), st)
    assert r is not None and r.reason == "MOMENTUM_FAILURE"
