import datetime as dt
import importlib.util
from pathlib import Path

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.option_chain import CacheParams, ChainCache
from trading_bot.engine.option_select import SelectParams
from trading_bot.engine.pipeline import PipelineParams, decide
from trading_bot.engine.presignal import StageEvent
from trading_bot.engine.risk_engine import AccountState, RiskLimits
from trading_bot.options import OptionChain
from trading_bot.timeutil import IST

# reuse the synthetic chain + fake REST from the options tests
_spec = importlib.util.spec_from_file_location("opt_t", Path(__file__).resolve().parent / "test_engine_options.py")
opt_t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(opt_t)

SPOT, ATR = 25000.0, 20.0
NOW = dt.datetime(2026, 9, 16, 9, 45, tzinfo=IST)  # early: room left in the session for the expected move


def _ctx(**over) -> ContextSnapshot:
    """A clean trend-pullback long: strong regime, aligned, at EMA20 with a bullish pin, room to PDH.
    Weaker versions of this (later in the day, normal regime) are correctly rejected by the risk
    engine on EV - see test_rejections_carry_stage_and_reason."""
    base = dict(
        ts=NOW, underlying="NIFTY", trigger_tf="5m", spot=SPOT, session_phase="09:15-10:00", quality="OK", bar_index=20,
        trends={"1d": {"label": "BULL", "score": 0.5}, "30m": {"label": "STRONG_BULL", "score": 0.7},
                "5m": {"label": "STRONG_BULL", "score": 0.75, "exhaustion": False}, "1m": {"label": "BULL", "score": 0.4}},
        alignment={"label": "STRONG_TREND_ALIGNMENT", "direction_preference": "up", "weighted_score": 0.75},
        regime={"primary": "STRONG_BULL", "recent_primaries": ["STRONG_BULL"] * 6},
        structure={"last_event": {"kind": "bos_up", "index": 19, "price": SPOT - 10}, "swing_high": SPOT + 70, "swing_low": SPOT - 40,
                   "recent_sweeps": [], "mss": None},
        levels={"session_high": SPOT + 8, "session_low": SPOT - 20,
                "nearest_above": {"name": "pdh", "price": SPOT + 45, "distance": 45, "distance_atr": 2.25, "touches": 1},
                "nearest_below": {"name": "ema20", "price": SPOT - 4, "distance": -4, "distance_atr": -0.2, "touches": 2}},
        price_action={"labels": ["bullish_pin"], "anatomy": {"range": 12.0, "close_loc": 0.85, "bullish": True}, "sweep": None},
        indicators={"atr": ATR, "rsi": 58.0, "macd_hist": 0.4, "adx": 26.0, "atr_percentile": 50.0, "ema20": SPOT - 4,
                    "ema50": SPOT - 6, "vwap": SPOT - 12, "vwap_prev": SPOT - 13, "close_prev": SPOT - 6},
        volume={"relative_volume": 1.4, "volume_proxy": "futures", "obv_slope": 0.2},
    )
    base.update(over)
    return ContextSnapshot(**base)


def _event(direction="up"):
    return StageEvent("setup-1", "NIFTY", direction, "CONFIRMING", "TRADE_READY", 0.7, "confirmation_closed", 20,
                      {"trigger_level": SPOT - 4})


def _chain(spread_scale=1.0) -> ChainCache:
    rows = opt_t._rows()
    chain = OptionChain(rows, "NIFTY", "NFO")
    rest = opt_t.FakeRest()
    rest.contracts_by_token = {c.token: c for c in chain.contracts}
    cache = ChainCache("NIFTY", chain, CacheParams(strikes_each_side=6, expiries=2))
    cache.refresh(rest, SPOT, NOW, session_elapsed=0.3)
    return cache


def _params(**limits) -> PipelineParams:
    # Rs.1L at 1%: at Rs.50k one NIFTY lot with a ~5pt structural option stop plus spread/slippage/taxes
    # (~2.8pt per unit) does not fit 0.5%-1% - that is a genuine finding, not a test artefact
    return PipelineParams(limits=RiskLimits(capital=100_000.0, risk_per_trade_pct=0.01, **limits), select=SelectParams(min_premium=5.0))


def test_full_pipeline_reaches_a_locked_snapshot():
    d = decide(_ctx(), _event(), setup_evidence=5, family_hint="trend_pullback", chain=_chain(),
               account=AccountState(equity=50000.0), params=_params())
    assert d.approved and d.status == "valid" and d.stage_reached == "SNAPSHOT" and d.rejection is None
    assert d.candidate.strategy in ("TREND_PULLBACK", "EMA_PULLBACK", "MTF_CONFLUENCE")
    assert d.risk.expected_value > 0 and d.risk.risk_reward >= 1.0
    assert d.score.total >= 40 and d.move.remaining_points > 0 and d.option is not None and d.plan is not None
    assert d.risk.approved and d.risk.quantity >= 75
    s = d.snapshot
    assert s.signal_id == d.signal_id and s.option_type == "CE" and s.quantity == d.risk.quantity
    assert s.option_stop < s.option_entry < s.option_target1 and s.fingerprint.startswith("NIFTY|")
    assert s.extra["tier"] in ("A", "B") and s.extra["greeks_source"] == "broker"
    ex = d.explanation(_ctx(), _event())
    assert len(ex) == 20 and ex["Decision"].startswith("APPROVED") and "lot(s)" in ex["Risk"]
    assert ex["Option"].startswith("NIFTY") and ex["Expiry"] == "2026-09-23"
    assert "not_evaluated_in_M1" not in ex.values()
    # the same inputs give the same signal id (replay parity)
    d2 = decide(_ctx(), _event(), setup_evidence=5, family_hint="trend_pullback", chain=_chain(),
                account=AccountState(equity=50000.0), params=_params())
    assert d2.signal_id == d.signal_id and d2.snapshot.as_dict() == s.as_dict()


def test_rejections_carry_stage_and_reason():
    acct = AccountState(equity=50000.0)
    # kill switch
    k = decide(_ctx(), _event(), setup_evidence=5, family_hint=None, chain=_chain(), account=acct, params=_params(), kill_reason="trading_kill")
    assert not k.approved and k.stage_reached == "STRATEGY_ROUTING" and k.reason_code == "trading_kill"
    # regime gate
    r = decide(_ctx(regime={"primary": "NO_TRADE", "recent_primaries": []}), _event(), setup_evidence=5, family_hint=None,
               chain=_chain(), account=acct, params=_params())
    assert r.stage_reached == "STRATEGY_ROUTING" and r.reason_code == "regime_no_trade" and r.status == "rejected"
    # nothing matches: quiet context
    q = decide(_ctx(price_action={"labels": [], "anatomy": {}, "sweep": None}, structure={"last_event": None, "recent_sweeps": []}),
               _event(), setup_evidence=5, family_hint=None, chain=_chain(), account=acct, params=_params())
    assert q.reason_code == "no_strategy_match" and q.routing_rejections
    # no chain
    n = decide(_ctx(), _event(), setup_evidence=5, family_hint=None, chain=None, account=acct, params=_params())
    assert n.stage_reached == "OPTION_SELECTION" and n.reason_code == "no_option_chain" and n.status == "option_rejected"
    assert n.explanation(_ctx(), _event())["Option"].startswith("no acceptable option")
    # stale chain
    ch = _chain()
    ch.last_refresh = NOW - dt.timedelta(minutes=10)
    st = decide(_ctx(), _event(), setup_evidence=5, family_hint=None, chain=ch, account=acct, params=_params())
    assert st.reason_code == "option_cache_stale"
    # correlated exposure at ranking
    c = decide(_ctx(), _event(), setup_evidence=5, family_hint=None, chain=_chain(),
               account=AccountState(equity=50000.0, open_directions={"BANKNIFTY": "up"}), params=_params())
    assert c.stage_reached == "RANKING" and c.reason_code == "correlated_exposure"
    # risk lock
    lock = decide(_ctx(), _event(), setup_evidence=5, family_hint=None, chain=_chain(),
                  account=AccountState(equity=50000.0, daily_lock=True), params=_params())
    assert lock.stage_reached == "RISK_ENGINE" and lock.reason_code == "daily_loss_lock" and lock.status == "risk_rejected"
    assert lock.plan is not None and lock.option is not None  # everything up to risk was evaluated and kept
    # late in the day: no chase
    late = decide(_ctx(ts=dt.datetime(2026, 9, 16, 15, 12, tzinfo=IST), session_phase="15:00-15:30"), _event(),
                  setup_evidence=5, family_hint=None, chain=_chain(), account=acct, params=_params())
    assert late.stage_reached == "NO_CHASE"


def test_counter_trend_needs_the_evidence_bar():
    ctx = _ctx(alignment={"label": "COUNTER_TREND", "direction_preference": "down"},
               trends={"1d": {"label": "BULL", "score": 0.5}, "30m": {"label": "STRONG_BEAR", "score": -0.7},
                       "5m": {"label": "STRONG_BULL", "score": 0.75, "exhaustion": False}, "1m": {"label": "BULL", "score": 0.4}})
    blocked = decide(ctx, _event(), setup_evidence=4, family_hint=None, chain=_chain(), account=AccountState(equity=50000.0), params=_params())
    assert blocked.stage_reached == "STRATEGY_ROUTING" and blocked.reason_code == "no_strategy_match"
    assert "counter_trend_evidence_insufficient" in blocked.rejection.detail


def test_tight_budget_rejects_at_risk_engine_with_full_detail():
    d = decide(_ctx(), _event(), setup_evidence=5, family_hint=None, chain=_chain(), account=AccountState(equity=50000.0),
               params=PipelineParams(limits=RiskLimits(risk_per_trade_pct=0.0025)))
    assert d.stage_reached == "RISK_ENGINE" and d.reason_code == "one_lot_exceeds_max_risk"
    assert "one lot risks" in d.rejection.detail
