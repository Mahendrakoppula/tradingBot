"""PaperLoop: the M2 loop end to end with a paper broker, and the full
replayed session through the M2 loop in SHADOW mode (journal only)."""
import dataclasses
import datetime as dt
import importlib.util
import json
import uuid
from pathlib import Path

from trading_bot.engine.candles import CandleStore
from trading_bot.engine.db.memory import MemoryDAL
from trading_bot.engine.execution import PaperBroker, PaperParams
from trading_bot.engine.option_chain import CacheParams
from trading_bot.engine.option_select import SelectParams
from trading_bot.engine.paper_loop import ChainService, PaperLoop, session_elapsed
from trading_bot.engine.pipeline import PipelineParams
from trading_bot.engine.presignal import SetupState, StageEvent
from trading_bot.engine.replay import SimClock, TickReplaySource
from trading_bot.engine.risk_engine import RiskLimits
from trading_bot.engine.warmup import Instrument
from trading_bot.timeutil import IST


def _load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


opt_t = _load("test_engine_options")
pipe_t = _load("test_engine_pipeline")
replay_t = _load("test_engine_replay")

SPOT = 25000.0
DAY = dt.date(2026, 9, 16)
SPOT_INST = Instrument("NIFTY", "NSE", "99926000", "spot")


class _Notes:
    def __init__(self):
        self.msgs = []

    def notify(self, message, *, html=False):
        self.msgs.append(message)


def _chain_service() -> ChainService:
    rows = opt_t._rows()
    rest = opt_t.FakeRest()
    svc = ChainService(rest, rows, {"NIFTY": "NSE"}, CacheParams(strikes_each_side=6, expiries=2, refresh_seconds=60))
    rest.contracts_by_token = {c.token: c for c in svc.caches["NIFTY"].chain.contracts}
    return svc


def _loop(execute=True, profile="ideal", mode="PAPER", notes=None):
    cfg = replay_t._cfg(mode=mode, capital=100000.0, risk_per_trade_pct=0.01)
    clock = SimClock(dt.datetime.combine(DAY, dt.time(9, 45), tzinfo=IST))
    src = TickReplaySource({SPOT_INST.token: []}, clock, DAY)
    stores = {(SPOT_INST.token, "1m"): CandleStore("1m")}
    broker = PaperBroker(PaperParams.for_profile(profile))
    pp = PipelineParams(limits=RiskLimits(capital=100000.0, risk_per_trade_pct=0.01), select=SelectParams(min_premium=5.0))
    loop = PaperLoop(cfg, MemoryDAL(), [SPOT_INST], src, clock.now, notes or _Notes(), stores, run_id=uuid.UUID(int=5),
                     chains=_chain_service(), broker=broker, pipeline=pp, execute=execute)
    loop.day = DAY
    loop.config_version_id = loop.dal.get_or_create_config_version({})
    loop.dal.insert_run(loop.run_id, mode, clock.now())
    return loop, clock


def _trade_ready(loop, ctx):
    ev = StageEvent("setup-1", "NIFTY", "up", "CONFIRMING", "TRADE_READY", 0.7, "confirmation_closed", 20, {"trigger_level": SPOT - 4})
    loop.presignal.setups[("NIFTY", "up")] = SetupState("setup-1", "NIFTY", "up", "TRADE_READY", 0.7,
                                                        evidence={"a", "b", "c", "d", "e"}, family_hint="trend_pullback")
    loop.last_spot["NIFTY"] = ctx.spot
    loop._on_context(ctx, 1)
    loop._on_stage_event(ctx, ev, 1)
    return ev


def test_paper_entry_fill_manage_exit_and_journal():
    notes = _Notes()
    loop, clock = _loop(execute=True, profile="ideal", notes=notes)
    ctx = pipe_t._ctx()
    _trade_ready(loop, ctx)
    dal = loop.dal
    assert loop.stats.decisions == 1 and loop.stats.approved == 1 and loop.stats.orders_sent == 1
    sig = dal.signals[0]
    assert sig["status"] == "valid" and sig["strategy"] and sig["score"] >= 40 and len(sig["explanation"]) == 20
    assert dal.risk_decisions[0]["decision"] in ("APPROVED", "REDUCE_SIZE") and dal.risk_decisions[0]["details"]["costs"]["brokerage"] == 40.0
    # ideal profile: filled on the first poll -> position open, execution journaled
    assert loop.stats.fills == 1 and len(loop.book.open) == 1
    pos = next(iter(loop.book.open.values()))
    ex = dal.executions[0]
    assert ex["side"] == "BUY" and ex["state"] == "POSITION_ACTIVE" and ex["filled_quantity"] == pos.quantity and ex["details"]["profile"] == "ideal"
    assert notes.msgs[-1].startswith("PAPER ORDER")
    # manage: price runs to TP1 -> partial exit; then to TP2 -> final exit -> trade result
    tok = pos.token
    q = loop.chains.caches["NIFTY"].quotes[tok]
    up1 = dataclasses.replace(ctx, spot=pos.plan.target1_ref + 1, bar_index=22)
    loop.chains.caches["NIFTY"].quotes[tok] = dataclasses.replace(q, bid=pos.plan.option_target1 + 0.5, ask=pos.plan.option_target1 + 1.5)
    loop.broker.set_quote(tok, pos.plan.option_target1 + 0.5, pos.plan.option_target1 + 1.5)
    loop._manage("NIFTY", up1, clock.now())
    assert loop.stats.trades_closed == 0 and pos.quantity < pos.snapshot.quantity or pos.snapshot.quantity == 75
    up2 = dataclasses.replace(ctx, spot=pos.plan.target2_ref + 1, bar_index=24)
    loop.chains.caches["NIFTY"].quotes[tok] = dataclasses.replace(q, bid=pos.plan.option_target2 + 0.5, ask=pos.plan.option_target2 + 1.5)
    loop.broker.set_quote(tok, pos.plan.option_target2 + 0.5, pos.plan.option_target2 + 1.5)
    loop._manage("NIFTY", up2, clock.now())
    assert loop.stats.trades_closed == 1 and loop.book.open == {} and len(dal.trade_results) == 1
    tr = dal.trade_results[0]
    assert tr["net_pnl"] > 0 and tr["gross_pnl"] > tr["net_pnl"] and tr["exit_reason"] in ("TARGET_2", "EXTENDED_TARGET", "TARGET_1")
    assert tr["details"]["fingerprint"].startswith("NIFTY|") and tr["r_multiple"] > 0
    assert loop.account.trades_today == 1 and loop.account.realized_today == tr["net_pnl"] and loop.account.consecutive_losses == 0
    assert any(m.startswith("PAPER TRADE CLOSED") for m in notes.msgs)
    assert loop.broker.positions() == [] and all(e["state"] == "POSITION_ACTIVE" for e in dal.executions)
    # EOD summary includes the P&L lines
    loop._eod()
    assert "trades=1 wins=1" in notes.msgs[-1] and "net=" in notes.msgs[-1] and "signals by status: valid=1" in notes.msgs[-1]
    assert dal.runs[str(loop.run_id)]["notes"]["stats"]["rejections"]["total"] == 0


def test_stop_loss_exit_and_daily_lock():
    loop, clock = _loop(execute=True, profile="ideal")
    ctx = pipe_t._ctx()
    _trade_ready(loop, ctx)
    pos = next(iter(loop.book.open.values()))
    tok = pos.token
    q = loop.chains.caches["NIFTY"].quotes[tok]
    down = dataclasses.replace(ctx, spot=pos.plan.stop_ref - 1, bar_index=22)
    loop.chains.caches["NIFTY"].quotes[tok] = dataclasses.replace(q, bid=pos.plan.option_stop - 0.5, ask=pos.plan.option_stop + 0.5)
    loop.broker.set_quote(tok, pos.plan.option_stop - 0.5, pos.plan.option_stop + 0.5)
    loop._manage("NIFTY", down, clock.now())
    assert loop.stats.trades_closed == 1
    tr = loop.dal.trade_results[0]
    assert tr["exit_reason"] == "STOP_LOSS" and tr["net_pnl"] < 0 and loop.account.consecutive_losses == 1
    # a second identical signal is blocked as a duplicate setup at the gate
    ev2 = StageEvent("setup-1", "NIFTY", "up", "CONFIRMING", "TRADE_READY", 0.7, "confirmation_closed", 23, {})
    clock.advance_to(ctx.ts + dt.timedelta(minutes=15))
    loop._on_stage_event(dataclasses.replace(ctx, bar_index=23, ts=ctx.ts + dt.timedelta(minutes=15)), ev2, 2)
    assert loop.rejections and loop.rejections[-1].stage in ("EXECUTION_GATE", "RISK_ENGINE", "RANKING")
    # daily loss lock trips the trading kill switch
    loop.account.realized_today = -loop.cfg.capital * loop.cfg.daily_loss_cap_pct
    loop._on_trade_closed(dataclasses.replace(loop.book.closed[0], net_pnl=-1.0, trade_id="x"), clock.now())
    assert loop.kills.trading and loop.dal.kill_switch_events[-1]["reason"] == "daily_loss_cap"


def test_shadow_mode_runs_pipeline_without_orders():
    loop, clock = _loop(execute=False, mode="SHADOW")
    ctx = pipe_t._ctx()
    _trade_ready(loop, ctx)
    assert loop.stats.approved == 1 and loop.stats.orders_sent == 0 and loop.osm.records == {} and loop.book.open == {}
    assert loop.dal.signals[0]["status"] == "valid" and loop.dal.executions == []


def test_eod_force_closes_open_paper_positions():
    loop, clock = _loop(execute=True, profile="realistic")
    ctx = pipe_t._ctx()
    _trade_ready(loop, ctx)
    clock.advance_to(clock.now() + dt.timedelta(seconds=2))
    loop._poll_orders(clock.now())
    assert len(loop.book.open) == 1
    loop._eod()
    assert loop.book.open == {} and loop.stats.trades_closed == 1
    assert loop.dal.trade_results[0]["exit_reason"] in ("EOD_EXIT", "EMERGENCY_EXIT")
    assert loop.dal.runs[str(loop.run_id)]["status"] == "completed"


def test_circuit_breaker_blocks_and_recovers():
    loop, clock = _loop(execute=True)
    ctx = pipe_t._ctx(quality="STALE")
    loop.last_spot["NIFTY"] = SPOT
    loop._on_context(ctx, 1)
    assert loop.kills.circuit_breaker == "data_stale" and loop.dal.kill_switch_events[-1]["action"] == "on"
    ev = StageEvent("setup-2", "NIFTY", "up", "CONFIRMING", "TRADE_READY", 0.7, "x", 20, {})
    loop._on_stage_event(ctx, ev, 1)
    assert loop.rejections[-1].reason_code.startswith("circuit_breaker") and loop.stats.orders_sent == 0
    loop._on_context(pipe_t._ctx(), 2)
    assert loop.kills.circuit_breaker is None and loop.dal.kill_switch_events[-1]["action"] == "off"


def test_circuit_breaker_does_not_flap_while_another_underlying_is_still_gapped():
    """2026-09-21 15:30: NIFTY's clean bar reset the breaker one second before SENSEX's
    still-gapped bar tripped it again. The breaker is global: it recovers only when
    every underlying's latest snapshot is clean."""
    loop, clock = _loop(execute=True)
    loop._on_context(pipe_t._ctx(), 1)
    loop._on_context(pipe_t._ctx(underlying="SENSEX", quality="GAP"), 2)
    assert loop.kills.circuit_breaker == "data_gap"
    loop._on_context(pipe_t._ctx(), 3)  # NIFTY clean again, SENSEX still gapped
    assert loop.kills.circuit_breaker == "data_gap"
    assert [e["action"] for e in loop.dal.kill_switch_events] == ["on"]
    loop._on_context(pipe_t._ctx(underlying="SENSEX"), 4)  # SENSEX recovers
    assert loop.kills.circuit_breaker is None
    assert [e["action"] for e in loop.dal.kill_switch_events] == ["on", "off"]


def test_full_replay_through_the_m2_loop_is_deterministic():
    def run():
        spot_hist, px = replay_t._history(12, 100, 0)
        stores = replay_t._stores(spot_hist, SPOT_INST.token)
        today = replay_t._session(DAY, px, 999, 0)
        clock = SimClock(dt.datetime.combine(DAY, dt.time(9, 0), tzinfo=IST))
        src = TickReplaySource({SPOT_INST.token: today}, clock, DAY)
        dal = MemoryDAL()
        loop = PaperLoop(replay_t._cfg(mode="SHADOW"), dal, [SPOT_INST], src, clock.now, _Notes(), stores,
                         run_id=uuid.UUID(int=9), chains=None, broker=None, execute=False)
        stats = loop.run()
        return loop, dal, stats

    a, dal_a, stats_a = run()
    b, dal_b, stats_b = run()
    assert stats_a.snapshots == 75 and dal_a.runs[str(a.run_id)]["status"] == "completed"
    assert stats_a.decisions == sum(1 for e in dal_a.presignal_events if e["to_stage"] == "TRADE_READY")
    for s in dal_a.signals:
        assert s["status"] in ("valid", "rejected", "expired", "invalidated", "risk_rejected", "option_rejected")
        assert len(s["explanation"]) == 20 and "not_evaluated_in_M1" not in s["explanation"].values()
    ja = json.dumps([dal_a.signals, dal_a.risk_decisions, [r.as_dict() for r in a.rejections]], sort_keys=True, default=str)
    jb = json.dumps([dal_b.signals, dal_b.risk_decisions, [r.as_dict() for r in b.rejections]], sort_keys=True, default=str)
    assert ja == jb


def test_session_elapsed():
    assert session_elapsed(dt.datetime(2026, 9, 16, 9, 15, tzinfo=IST)) == 0.0
    assert session_elapsed(dt.datetime(2026, 9, 16, 15, 30, tzinfo=IST)) == 1.0
    assert abs(session_elapsed(dt.datetime(2026, 9, 16, 12, 22, 30, tzinfo=IST)) - 0.5) < 0.01


def test_shadow_only_strategy_is_journaled_but_not_executed():
    import dataclasses as _dc
    loop, clock = _loop(execute=True, profile="ideal")
    loop.cfg = _dc.replace(loop.cfg, shadow_strategies=("TREND_PULLBACK", "EMA_PULLBACK", "MTF_CONFLUENCE"))
    ctx = pipe_t._ctx()
    _trade_ready(loop, ctx)
    assert loop.stats.approved == 1 and loop.stats.orders_sent == 0 and loop.book.open == {}
    assert loop.stats.__dict__["shadow_only"] == 1
    sig = loop.dal.signals[0]
    assert sig["status"] == "valid" and sig["reason_code"] == "shadow_only_strategy"


def test_chain_api_errors_are_counted_in_a_window_not_since_start():
    """2026-09-24: the broker's batch-quote endpoint 404'd for ~2.5 minutes at
    the open. 26 refreshes failed, the API recovered by 09:18 and chains
    refreshed all morning - but the cumulative counter kept the circuit
    breaker on `repeated_api_errors` and blocked entries for the whole day."""
    from trading_bot.engine.paper_loop import API_ERROR_WINDOW, ChainService
    from trading_bot.engine.option_chain import CacheParams
    from trading_bot.engine.positions import circuit_breaker_reason

    t0 = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
    clockbox = {"now": t0}
    state = {"ok": False}
    svc = ChainService(None, [], {"NIFTY": "NSE"}, CacheParams(), now=lambda: clockbox["now"])

    def cache_refresh(*a, **k):  # stands in for the batched quote call
        if not state["ok"]:
            raise RuntimeError("HTTP_404: non-JSON response (404): not found")
        return 0

    svc.caches["NIFTY"].refresh = cache_refresh
    args = dict(quality="OK", feed_connected=True, reconciled=True, clock_drift_seconds=0.0)
    for i in range(26):  # the open burst
        svc.refresh("NIFTY", SPOT, t0 + dt.timedelta(seconds=6 * i))
    assert svc.errors == 26 and svc.total_errors == 26
    assert circuit_breaker_reason(api_errors_recent=svc.errors, **args) == "repeated_api_errors"
    # the API recovers; once the window has passed with no new failures the breaker reason clears
    state["ok"] = True
    clockbox["now"] = t0 + API_ERROR_WINDOW + dt.timedelta(minutes=5)  # past the window of the LAST failure
    assert svc.errors == 0 and svc.total_errors == 26  # the cumulative count is kept for the journal
    assert circuit_breaker_reason(api_errors_recent=svc.errors, **args) is None
    # a fresh burst inside the window trips it again
    state["ok"] = False
    for i in range(5):
        svc.refresh("NIFTY", SPOT, clockbox["now"] + dt.timedelta(seconds=i))
    assert svc.errors == 5
    assert circuit_breaker_reason(api_errors_recent=svc.errors, **args) == "repeated_api_errors"
