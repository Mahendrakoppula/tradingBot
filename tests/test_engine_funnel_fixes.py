"""Pins the three fixes that came out of the year-long funnel replay
(research/funnel_diagnostic.py, 2023-04 .. 2024-03, 1,374 TRADE_READY setups,
5 approvals, 0 trades):

1. PaperBroker fills a MARKETABLE limit order, never worse than its limit.
2. The PRESIGNAL_CONFIRMED routing fallback, and that it is opt-in.
3. The pre-signal tracker carries the trigger kind into TRADE_READY.
"""
import dataclasses
import datetime as dt
import importlib.util
from pathlib import Path

from trading_bot.engine.execution.broker import OrderRequest
from trading_bot.engine.execution.gate import GateParams
from trading_bot.engine.execution.paper import PaperBroker, PaperParams
from trading_bot.engine.pipeline import decide
from trading_bot.engine.presignal import PreSignalConfig, PreSignalTracker, SetupState
from trading_bot.engine.risk_engine import AccountState
from trading_bot.engine.strategies.fallback import NAME as FALLBACK, presignal_candidate
from trading_bot.timeutil import IST

_spec = importlib.util.spec_from_file_location("pipe_t", Path(__file__).resolve().parent / "test_engine_pipeline.py")
pipe_t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pipe_t)

NOW = dt.datetime(2026, 9, 16, 10, 0, tzinfo=IST)


# --- 1. paper broker -------------------------------------------------------------------------------

def _buy(limit: float, qty: int = 65) -> OrderRequest:
    return OrderRequest("c1", "s1", None, "TOK", "NFO", "SYM", "BUY", qty, "LIMIT", limit, limit, purpose="ENTRY")


def _sell(limit: float, qty: int = 65) -> OrderRequest:
    return OrderRequest("c2", "s1", None, "TOK", "NFO", "SYM", "SELL", qty, "LIMIT", limit, limit, purpose="EXIT")


def _book(mid: float, spread_pct: float) -> tuple[float, float]:
    half = mid * spread_pct / 100.0 / 2.0
    return round(mid - half, 2), round(mid + half, 2)


def test_marketable_buy_limit_fills_at_or_below_its_limit_on_a_wide_spread():
    broker = PaperBroker(PaperParams.for_profile("realistic"))
    for mid, spread in ((57.3, 1.0), (80.0, 1.5), (200.0, 2.0)):  # all inside the gate's 2% spread cap
        bid, ask = _book(mid, spread)
        limit = round(ask * (1 + GateParams().entry_buffer_pct / 100.0), 2)
        px = broker._fill_price(_buy(limit), (bid, ask, mid))
        assert px is not None, f"spread {spread}% must fill - the gate approved it"
        assert ask <= px <= limit


def test_buy_limit_below_the_ask_still_rests():
    broker = PaperBroker(PaperParams.for_profile("realistic"))
    bid, ask = _book(100.0, 1.0)
    assert broker._fill_price(_buy(ask - 0.5), (bid, ask, 100.0)) is None


def test_marketable_sell_limit_fills_at_or_above_its_limit_and_a_high_one_rests():
    broker = PaperBroker(PaperParams.for_profile("realistic"))
    bid, ask = _book(80.0, 1.8)
    limit = round(bid * (1 - 0.005), 2)  # positions.exit_request's buffer
    px = broker._fill_price(_sell(limit), (bid, ask, 80.0))
    assert px is not None and limit <= px <= bid
    assert broker._fill_price(_sell(bid + 0.5), (bid, ask, 80.0)) is None


def test_wide_spread_entry_fills_end_to_end_through_place_and_poll():
    broker = PaperBroker(dataclasses.replace(PaperParams.for_profile("realistic"), timeout_probability=0.0,
                                             reject_probability=0.0))
    bid, ask = _book(80.0, 1.5)
    broker.set_quote("TOK", bid, ask)
    limit = round(ask * (1 + GateParams().entry_buffer_pct / 100.0), 2)
    bo = broker.place(_buy(limit), NOW)
    bo = broker.poll(bo.broker_order_id, NOW + dt.timedelta(seconds=2))
    assert bo.status == "FILLED" and bo.average_price <= limit


# --- 2. routing fallback --------------------------------------------------------------------------

def _range_ctx(**over):
    """A context no family accepts: RANGE regime, no sweep, no pattern on the TRADE_READY bar, and
    nowhere near a range extreme - but the tracker confirmed a long above 24,990."""
    base = dict(regime={"primary": "RANGE", "recent_primaries": ["RANGE"] * 6},
                price_action={"labels": [], "anatomy": {"range": 10.0, "close_loc": 0.7, "bullish": True}, "sweep": None},
                alignment={"label": "WEAK_ALIGNMENT", "direction_preference": "up", "weighted_score": 0.2},
                structure={"last_event": {"kind": "bos_up", "index": 19, "price": pipe_t.SPOT - 10}, "swing_high": pipe_t.SPOT + 70,
                           "swing_low": pipe_t.SPOT - 40, "recent_sweeps": [], "mss": None},
                levels={"session_high": pipe_t.SPOT + 8, "session_low": pipe_t.SPOT - 60,
                        "nearest_above": {"name": "pdh", "price": pipe_t.SPOT + 45, "distance": 45, "distance_atr": 2.25, "touches": 1},
                        "nearest_below": {"name": "pivot", "price": pipe_t.SPOT - 30, "distance": -30, "distance_atr": -1.5, "touches": 1}})
    base.update(over)
    return pipe_t._ctx(**base)


def _ready_event(level=pipe_t.SPOT - 10, kind="structure_break"):
    from trading_bot.engine.presignal import StageEvent
    return StageEvent("setup-fb", "NIFTY", "up", "CONFIRMING", "TRADE_READY", 0.7, "confirmation_closed", 20,
                      {"trigger_level": level, "trigger_kind": kind})


def test_without_the_fallback_a_confirmed_setup_with_no_family_dies_at_routing():
    d = decide(_range_ctx(), _ready_event(), setup_evidence=5, family_hint="unclassified", chain=pipe_t._chain(),
               account=AccountState(equity=100000.0), params=pipe_t._params())
    assert d.stage_reached == "STRATEGY_ROUTING" and d.reason_code == "no_strategy_match"


def test_the_fallback_routes_it_and_every_later_gate_still_runs():
    p = dataclasses.replace(pipe_t._params(), presignal_fallback=True)
    d = decide(_range_ctx(), _ready_event(), setup_evidence=5, family_hint="unclassified", chain=pipe_t._chain(),
               account=AccountState(equity=100000.0), params=p)
    assert d.candidate is not None and d.candidate.strategy == FALLBACK
    assert d.stage_reached != "STRATEGY_ROUTING"
    # the family verdicts are still journaled for the review
    assert d.routing_rejections and all(n.strategy != FALLBACK for n in d.routing_rejections)
    # invalidation sits just beyond the trigger level, on the right side of entry
    assert d.candidate.invalidation < pipe_t.SPOT - 10 < d.candidate.entry_ref
    assert d.candidate.target_ref > d.candidate.entry_ref
    # account limits are untouched: a locked day still rejects at the risk engine (or earlier)
    locked = decide(_range_ctx(), _ready_event(), setup_evidence=5, family_hint="unclassified", chain=pipe_t._chain(),
                    account=AccountState(equity=100000.0, daily_lock=True), params=p)
    assert not locked.approved


def test_a_specific_family_always_wins_over_the_fallback():
    p = dataclasses.replace(pipe_t._params(), presignal_fallback=True)
    d = decide(pipe_t._ctx(), pipe_t._event(), setup_evidence=5, family_hint="trend_pullback", chain=pipe_t._chain(),
               account=AccountState(equity=50000.0), params=p)
    assert d.candidate.strategy != FALLBACK


def test_the_fallback_never_bypasses_the_regime_or_data_gates():
    p = dataclasses.replace(pipe_t._params(), presignal_fallback=True)
    for over in ({"regime": {"primary": "NO_TRADE", "recent_primaries": []}}, {"quality": "GAP"}):
        d = decide(_range_ctx(**over), _ready_event(), setup_evidence=5, family_hint="unclassified", chain=pipe_t._chain(),
                   account=AccountState(equity=100000.0), params=p)
        assert d.stage_reached == "STRATEGY_ROUTING" and d.candidate is None


def test_fallback_candidate_respects_counter_trend_evidence_and_bad_levels():
    ctx = _range_ctx()
    assert presignal_candidate(ctx, "up", trigger_level=pipe_t.SPOT - 10, trigger_kind="x", counter_trend=True,
                               counter_trend_cleared=False) is None
    assert presignal_candidate(ctx, "up", trigger_level=pipe_t.SPOT - 10, trigger_kind="x", counter_trend=True,
                               counter_trend_cleared=True) is not None
    # a trigger level on the wrong side of price falls back to the swing behind price
    c = presignal_candidate(ctx, "up", trigger_level=pipe_t.SPOT + 5, trigger_kind="x", counter_trend=False,
                            counter_trend_cleared=False)
    assert c is not None and c.invalidation < pipe_t.SPOT - 40


def test_fallback_is_off_unless_configured(monkeypatch):
    from trading_bot.engine.config import EngineConfig
    from trading_bot.engine.pipeline import PipelineParams
    monkeypatch.delenv("TECH_PRESIGNAL_FALLBACK", raising=False)
    monkeypatch.setenv("TECH_MODE", "BACKTEST")
    assert PipelineParams.from_config(EngineConfig.from_env()).presignal_fallback is False
    monkeypatch.setenv("TECH_PRESIGNAL_FALLBACK", "true")
    assert PipelineParams.from_config(EngineConfig.from_env()).presignal_fallback is True


# --- 3. trigger kind travels with TRADE_READY --------------------------------------------------------

def test_trade_ready_event_carries_the_trigger_kind():
    tr = PreSignalTracker(PreSignalConfig())
    s = SetupState("sid", "NIFTY", "up", "CONFIRMING", 0.8, evidence={"level_approach", "momentum_change", "compression",
                                                                     "htf_alignment"},
                   opened_bar=10, last_progress_bar=19, trigger_level=pipe_t.SPOT - 10, trigger_bar=19,
                   trigger_kind="displacement_break")
    ctx = _range_ctx(alignment={"label": "TREND_ALIGNMENT", "direction_preference": "up", "weighted_score": 0.5})
    evs = tr._advance(s, {"level_approach", "momentum_change"}, ctx)
    ready = [e for e in evs if e.to_stage == "TRADE_READY"]
    assert ready and ready[0].details["trigger_kind"] == "displacement_break"


# --- 4. exit retries (the crash the first replayed trades exposed) ---------------------------------

def test_an_unfilled_exit_can_be_retried_without_a_duplicate_order_crash():
    import importlib.util as ilu
    from trading_bot.engine.execution import OrderStateMachine
    from trading_bot.engine.stops import ExitSignal
    spec = ilu.spec_from_file_location("pos_t", Path(__file__).resolve().parent / "test_engine_positions.py")
    pos_t = ilu.module_from_spec(spec)
    spec.loader.exec_module(pos_t)
    import uuid

    book, pos = pos_t._book_with_position(qty=75)
    broker = PaperBroker(dataclasses.replace(PaperParams.for_profile("realistic"), reject_probability=0.0, timeout_probability=0.0))
    osm = OrderStateMachine(broker)
    ids = []
    for attempt in range(2):
        req = book.exit_request(pos, ExitSignal("STOP_LOSS", pos.quantity), bid=140.0)
        eid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"exec/{pos.signal_id}/{req.client_order_id}"))
        ids.append(eid)
        rec = osm.new(eid, pos.signal_id, pos.setup_id, confirmation_at=pos_t.T0)  # raised on the 2nd pass before the fix
        osm.risk_approved(rec, pos_t.T0)
        osm.send(rec, req, pos_t.T0)  # no quote at the broker -> rejected, i.e. the exit did not fill
        assert rec.terminal and rec.filled_quantity == 0
        book.exit_abandoned(pos)
    assert ids[0] != ids[1] and pos.exit_attempts == 2


def test_a_filled_exit_does_not_block_selling_the_remainder():
    from trading_bot.engine.execution import OrderStateMachine
    broker = PaperBroker(dataclasses.replace(PaperParams.for_profile("ideal")))
    broker.set_quote("TOK", 99.0, 101.0)
    osm = OrderStateMachine(broker)
    first = osm.new("e1", "sig", "set", confirmation_at=NOW)
    osm.risk_approved(first, NOW)
    osm.send(first, OrderRequest("sig:EXIT:1", "sig", "set", "TOK", "NFO", "SYM", "SELL", 50, "LIMIT", 98.0, 98.0, purpose="EXIT"), NOW)
    osm.poll(first, NOW + dt.timedelta(seconds=1))
    assert first.state == "POSITION_ACTIVE"
    assert osm.duplicate_check("sig", "set", "TOK", "EXIT") is None


def test_a_filled_entry_is_still_never_repeated():
    from trading_bot.engine.execution import OrderStateMachine
    broker = PaperBroker(dataclasses.replace(PaperParams.for_profile("ideal")))
    broker.set_quote("TOK", 99.0, 101.0)
    osm = OrderStateMachine(broker)
    entry = osm.new("e0", "sig", "set", confirmation_at=NOW)
    osm.risk_approved(entry, NOW)
    osm.send(entry, _buy(102.0), NOW)
    osm.poll(entry, NOW + dt.timedelta(seconds=1))
    assert entry.state == "POSITION_ACTIVE"
    assert osm.duplicate_check("sig", "set", "OTHER", "ENTRY") == "duplicate_signal_or_setup"


# --- 5. premium cap: stop-based sizing must not put most of the account into one option -------------

def test_premium_cap_limits_lots_and_never_raises_them():
    from trading_bot.engine.risk_engine import RiskLimits, evaluate
    from trading_bot.costs import CostRates
    d = decide(pipe_t._ctx(), pipe_t._event(), setup_evidence=5, family_hint="trend_pullback", chain=pipe_t._chain(),
               account=AccountState(equity=200000.0),
               params=dataclasses.replace(pipe_t._params(), limits=RiskLimits(capital=200000.0, risk_per_trade_pct=0.03)))
    assert d.approved, d.reason_code
    uncapped = d.risk.lots
    one_lot = d.plan.option_entry * d.option.contract.lotsize
    capped = evaluate(d.plan, d.option, AccountState(equity=200000.0),
                      RiskLimits(capital=200000.0, risk_per_trade_pct=0.03, max_premium_pct=(1.5 * one_lot) / 200000.0),
                      CostRates(), score=d.score.total)
    assert capped.approved and capped.lots == 1 <= uncapped
    assert capped.quantity * d.plan.option_entry <= 1.5 * one_lot
    too_small = evaluate(d.plan, d.option, AccountState(equity=200000.0),
                         RiskLimits(capital=200000.0, risk_per_trade_pct=0.03, max_premium_pct=(0.5 * one_lot) / 200000.0),
                         CostRates(), score=d.score.total)
    assert not too_small.approved and too_small.reason_code == "one_lot_exceeds_premium_cap"
    off = evaluate(d.plan, d.option, AccountState(equity=200000.0), RiskLimits(capital=200000.0, risk_per_trade_pct=0.03),
                   CostRates(), score=d.score.total)
    assert off.lots == uncapped


# --- 6. a monthly-only underlying must always have a contract in the cache ---------------------------

def _chain_with(expiries: list, underlying: str = "BANKNIFTY"):
    from trading_bot.options import OptionChain, OptionContract
    ch = OptionChain([], underlying, "NFO")
    ch.contracts = [OptionContract(f"{e:%m%d}{k}{ot}", f"{underlying}{e:%d%b%y}{k}{ot}".upper(), underlying, e, float(k), ot, 35, 900, "NFO")
                    for e in expiries for k in range(54000, 56100, 100) for ot in ("CE", "PE")]
    return ch


def test_production_dte_window_keeps_banknifty_tradeable_all_month():
    from trading_bot.engine.option_chain import CacheParams, ChainCache
    vals = dict(line.split("=", 1) for line in (Path(__file__).resolve().parent.parent / "deploy" / "config.env")
                .read_text().splitlines() if line and not line.startswith("#") and "=" in line)
    dte_max = int(vals["TECH_OPTION_DTE_MAX"])
    expiry_a, expiry_b = dt.date(2026, 10, 27), dt.date(2026, 11, 24)  # consecutive monthlies, 28 days apart
    chain = _chain_with([expiry_a, expiry_b])
    for today in (dt.date(2026, 10, 28), dt.date(2026, 11, 2), dt.date(2026, 11, 9)):  # 27, 22, 15 DTE
        cache = ChainCache("BANKNIFTY", chain, CacheParams(dte_max=dte_max))
        assert cache.candidates(55000.0, today), f"no BANKNIFTY contract on {today} with dte_max={dte_max}"
        assert not ChainCache("BANKNIFTY", chain, CacheParams(dte_max=14)).candidates(55000.0, today)  # the old blind spot


def test_wider_window_does_not_change_which_weekly_expiries_are_kept():
    from trading_bot.engine.option_chain import CacheParams, ChainCache
    weeklies = [dt.date(2026, 11, 24) + dt.timedelta(days=7 * k) for k in range(5)]
    chain = _chain_with(weeklies, "NIFTY")
    today = dt.date(2026, 11, 23)
    narrow = {c.expiry for c in ChainCache("NIFTY", chain, CacheParams(dte_max=14)).candidates(55000.0, today)}
    wide = {c.expiry for c in ChainCache("NIFTY", chain, CacheParams(dte_max=35)).candidates(55000.0, today)}
    assert narrow == wide == set(weeklies[:2])
