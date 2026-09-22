"""M2 loop: ShadowLoop + the §93 decision pipeline + paper execution.

    TRADE_READY -> pipeline.decide() -> journal (signal, risk decision,
    rejection) -> [PAPER only] execution gate -> PaperBroker -> position
    book -> thesis monitor at every bar -> exits -> trade result

The same class runs SHADOW and PAPER (§51 parity): SHADOW evaluates the
whole pipeline and journals every decision but `execute=False` means no
order object is ever created; PAPER hands approved snapshots to the gate
and the PaperBroker. There is no live broker anywhere in this module and
nothing here can import one (AST guard).
"""
import dataclasses
import datetime as dt
import logging
import uuid
from typing import Callable

from trading_bot.costs import CostRates
from trading_bot.engine import jsonlog
from trading_bot.engine.candles import Candle
from trading_bot.engine.clock import in_session, session_close, session_open
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.execution import GateParams, OrderRequest, OrderStateMachine, PaperBroker, execution_gate
from trading_bot.engine.explain import render
from trading_bot.engine.health import HealthMonitor, HealthThresholds, format_alert
from trading_bot.engine.instruments import OPTIONS_EXCHANGE
from trading_bot.engine.option_chain import CacheParams, ChainCache
from trading_bot.engine.pipeline import Decision, PipelineParams, _reject, decide
from trading_bot.engine.positions import KillSwitches, Position, PositionBook, circuit_breaker_reason, reconcile, worst_quality
from trading_bot.engine.presignal import StageEvent
from trading_bot.engine.recovery import recover
from trading_bot.engine.rejections import Rejection, summarize
from trading_bot.engine.risk_engine import AccountState, record_result
from trading_bot.engine.shadow import TRIGGER_TF, ShadowLoop
from trading_bot.engine.stops import ExitSignal, ThesisMonitor
from trading_bot.options import OptionChain
from trading_bot.timeutil import IST

log = logging.getLogger(__name__)


def session_elapsed(now: dt.datetime) -> float:
    start = now.replace(hour=session_open().hour, minute=session_open().minute, second=0, microsecond=0)
    end = now.replace(hour=session_close().hour, minute=session_close().minute, second=0, microsecond=0)
    total = (end - start).total_seconds()
    return max(0.0, min(1.0, (now - start).total_seconds() / total)) if total > 0 else 1.0


class ChainService:
    """Owns one ChainCache per underlying and refreshes it on a cadence via
    the broker REST (one batched quote call + Greeks). Tests pass a fake
    `rest`; replay passes none and gets no chains."""

    def __init__(self, rest, scrip_rows: list[dict], spot_exchange: dict[str, str], params: CacheParams,
                 limiter=None):
        self.rest = rest
        self.params = params
        self.limiter = limiter
        self.caches: dict[str, ChainCache] = {}
        for u, exch in spot_exchange.items():
            chain = OptionChain(scrip_rows, u, OPTIONS_EXCHANGE.get(exch, "NFO"))
            self.caches[u] = ChainCache(u, chain, params)
        self.errors = 0

    def due(self, u: str, now: dt.datetime) -> bool:
        c = self.caches.get(u)
        return c is not None and (c.last_refresh is None or (now - c.last_refresh).total_seconds() >= self.params.refresh_seconds)

    def refresh(self, u: str, spot: float, now: dt.datetime) -> ChainCache | None:
        c = self.caches.get(u)
        if c is None:
            return None
        try:
            if self.limiter is not None:
                self.limiter.wait()
            c.refresh(self.rest, spot, now, session_elapsed=session_elapsed(now))
        except Exception as exc:  # noqa: BLE001 - a failed refresh leaves the cache stale, which the pipeline rejects on
            self.errors += 1
            jsonlog.event("options", "refresh_failed", severity="WARN", underlying=u, error=repr(exc))
        return c


class PaperLoop(ShadowLoop):
    def __init__(self, *args, chains: ChainService | None = None, broker: PaperBroker | None = None,
                 pipeline: PipelineParams | None = None, execute: bool = False, gate_params: GateParams | None = None,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.chains = chains
        self.broker = broker
        self.execute = execute and broker is not None
        self.pp = pipeline or PipelineParams()
        self.gate_params = gate_params or GateParams()
        self.account = AccountState(equity=self.cfg.capital)
        self.kills = KillSwitches()
        self.book = PositionBook(ThesisMonitor(self.pp.stops), self.pp.rates)
        self.osm = OrderStateMachine(broker) if broker is not None else None
        self.rejections: list[Rejection] = []
        self.decisions: list[Decision] = []
        self.last_ctx: dict[str, ContextSnapshot] = {}
        self.last_spot: dict[str, float] = {}
        self._pending_entries: dict[str, tuple] = {}  # execution_id -> (record, decision)
        self._pending_exits: dict[str, tuple] = {}  # execution_id -> (record, position)
        self._reconciled = True
        self._eod_swept = False
        self.health = HealthMonitor(HealthThresholds(stale_tick_seconds=float(self.cfg.stale_tick_seconds) + 5.0,
                                                     max_clock_drift_seconds=float(self.cfg.clock_drift_seconds)),
                                    in_session=lambda now: in_session(now.astimezone(IST).time()))
        self.health.state.daily_cap = self.cfg.capital * self.cfg.daily_loss_cap_pct
        self.health.state.heat_cap = self.cfg.capital * self.pp.limits.max_portfolio_heat_pct
        self._last_reconnects = 0
        self.stats.__dict__.update({"decisions": 0, "approved": 0, "orders_sent": 0, "fills": 0, "trades_closed": 0,
                                    "net_pnl": 0.0, "gross_pnl": 0.0, "costs": 0.0})

    # --- startup: §54 restart recovery ---------------------------------------------------------

    def start(self) -> None:
        super().start()
        now = self.clock()
        try:
            rep = recover(self.dal, day=self.day, now=now, book=self.book, broker=self.broker, account=self.account,
                          kills=self.kills, mode=self.mode)
        except Exception as exc:  # noqa: BLE001 - a broken journal must not stop the session, but it must stop entries
            jsonlog.event("recovery", "failed", severity="ERROR", error=repr(exc))
            self.kills.kill_trading(now, f"recovery failed: {exc}")
            self._alert(f"RECOVERY FAILED ({self.mode}): {exc} - new entries stopped", now)
            return
        if rep.prior_runs == 0 and rep.positions_recovered == 0 and not rep.kills_reapplied:
            return  # a normal first start of the day
        jsonlog.event("recovery", "done", severity="INFO" if rep.safe_to_resume else "ERROR", **{k: v for k, v in vars(rep).items() if k != "day"})
        if not rep.safe_to_resume:
            self.kills.kill_trading(now, "recovery unsafe: " + (", ".join(r for _, r in rep.positions_skipped) or "reconciliation mismatch"))
            self._journal_kill("trading", "on", "recovery_unsafe")
        self._alert(rep.summary(), now)

    # --- per-iteration -------------------------------------------------------------------

    def _housekeeping(self, now: dt.datetime) -> None:
        if self.osm is not None:
            self._poll_orders(now)
        if self.chains is not None:
            for u, spot in list(self.last_spot.items()):
                if self.chains.due(u, now):
                    self._refresh_chain(u, spot, now)
        self._health_tick(now)

    # --- §87 health -----------------------------------------------------------------------------

    def _health_tick(self, now: dt.datetime) -> None:
        h = self.source.health()
        st = self.health.state
        st.feed_connected = h.connected
        st.last_tick_at = h.last_tick_at
        st.clock_drift_seconds = h.clock_drift_seconds
        st.dropped_ticks = getattr(getattr(self.source, "stream", None), "dropped", 0)
        while self._last_reconnects < h.reconnects:
            self._last_reconnects += 1
            self.health.note_reconnect(now)
        st.snapshots = self.stats.snapshots
        st.decisions = self.stats.decisions
        st.approved = self.stats.approved
        st.rejections_by_stage = summarize(self.rejections)["by_stage"] if self.rejections else {}
        st.realized_today = self.account.realized_today
        st.open_risk = self.book.open_risk()
        st.open_positions = len(self.book.open)
        st.reconciled = self._reconciled
        st.api_errors = self.chains.errors if self.chains else 0
        st.circuit_breaker = self.kills.circuit_breaker
        for a in self.health.check(now):
            jsonlog.event("health", "recovered" if a.cleared else "alert", severity="INFO" if a.cleared else a.severity,
                          code=a.code, message=a.message)
            if a.severity == "CRITICAL" or a.cleared and a.code in ("feed_disconnected", "feed_stale", "engine_stalled"):
                self._alert(format_alert(a, self.mode), now)
            elif a.severity == "WARN" and not a.cleared:
                self._alert(format_alert(a, self.mode), now)
        if self.health.heartbeat_due(now):
            jsonlog.event("health", "heartbeat", **self.health.snapshot(now))

    def _refresh_chain(self, u: str, spot: float, now: dt.datetime) -> ChainCache | None:
        c = self.chains.refresh(u, spot, now)
        if c is not None and self.broker is not None:
            for q in c.quotes.values():
                self.broker.set_quote(q.contract.token, q.bid, q.ask, q.ltp)
        return c

    def _on_bar(self, token: str, tf: str, bar: Candle, now: dt.datetime) -> None:
        inst = self.by_token[token]
        if inst.role == "spot":
            self.last_spot[inst.underlying] = bar.close
            if tf == "1m" and inst.underlying in self.last_ctx:
                # hard-stop / target check between 5m closes using the 1m close
                ctx = dataclasses.replace(self.last_ctx[inst.underlying], spot=bar.close)
                self._manage(inst.underlying, ctx, now)
        super()._on_bar(token, tf, bar, now)

    # --- 5m close: context, presignal, pipeline, positions ------------------------------------

    def _on_context(self, ctx: ContextSnapshot, ctx_id: int) -> None:
        """Runs BEFORE the pre-signal tracker sees the bar: refresh the
        reconciliation and circuit-breaker state the pipeline will consult."""
        u = ctx.underlying
        self.last_ctx[u] = ctx
        now = self.clock()
        self.health.state.quality[u] = ctx.quality
        self.health.state.last_snapshot_at[u] = now
        if self.broker is not None:
            r = reconcile(self.book, self.broker, now)
            if not r.ok:
                jsonlog.event("reconcile", "mismatch", severity="ERROR", mismatches=list(r.mismatches))
            self._reconciled = r.ok
        h = self.source.health()
        # The breaker is global (§57) but this runs once per underlying's 5m close: judge it on the WORST
        # quality across all underlyings' latest snapshots, or a clean NIFTY bar would reset it a second
        # before a still-gapped SENSEX bar trips it again (flap seen 2026-09-21 15:30).
        reason = circuit_breaker_reason(quality=worst_quality(self.health.state.quality.values()), feed_connected=h.connected,
                                        reconciled=self._reconciled, api_errors_recent=self.chains.errors if self.chains else 0,
                                        clock_drift_seconds=h.clock_drift_seconds)
        if reason and self.kills.circuit_breaker is None:
            self.kills.trip(now, reason)
            self._journal_kill("circuit_breaker", "on", reason)
        elif reason is None and self.kills.circuit_breaker is not None:
            self.kills.reset_breaker(now)
            self._journal_kill("circuit_breaker", "off", "recovered")
        if self.chains is not None:
            c = self.chains.caches.get(u)
            if c is not None and c.quotes:
                self.dal.insert_option_chain_snapshots(self.run_id, ctx.ts, u, [q.as_dict() for q in c.quotes.values()])

    def _on_trigger(self, inst, bar: Candle) -> None:
        super()._on_trigger(inst, bar)
        ctx = self.last_ctx.get(inst.underlying)
        if ctx is None:
            return
        now = self.clock()
        self._manage(inst.underlying, ctx, now)
        if not self._eod_swept and ctx.ts.time() >= self.cfg.eod_cutoff:
            self._eod_sweep(now)

    def _on_stage_event(self, ctx: ContextSnapshot, ev: StageEvent, ctx_id: int) -> None:
        self.stats.stage_events += 1
        self.dal.insert_presignal_event(self.run_id, ctx.ts, ev, context_snapshot_id=ctx_id)
        jsonlog.event("presignal", "stage_change", underlying=ev.underlying, setup_id=ev.setup_id, ts=ctx.ts,
                      decision=ev.to_stage.lower(), reason_code=ev.reason_code, from_stage=ev.from_stage,
                      to_stage=ev.to_stage, direction=ev.direction, confidence=ev.confidence, details=ev.details)
        if ev.to_stage != "TRADE_READY":
            return
        self._decide(ctx, ev, ctx_id)

    def _decide(self, ctx: ContextSnapshot, ev: StageEvent, ctx_id: int) -> None:
        u = ctx.underlying
        now = self.clock()
        chain = None
        if self.chains is not None:
            chain = self.chains.caches.get(u)
            if chain is not None and (chain.last_refresh is None or not chain.fresh(ctx.ts)):
                chain = self._refresh_chain(u, ctx.spot, now)
        self.account.open_positions = len(self.book.open)
        self.account.open_risk = self.book.open_risk()
        self.account.open_directions = self.book.open_directions()
        setup = self.presignal.setups.get((u, ev.direction))
        evidence = len(setup.evidence) if setup else 0
        hint = setup.family_hint if setup else None
        d = decide(ctx, ev, setup_evidence=evidence, family_hint=hint, chain=chain, account=self.account, params=self.pp,
                   kill_reason=self.kills.blocks_entry("*", u), session_elapsed=session_elapsed(now))
        if d.approved and d.candidate.strategy in self.kills.strategies:
            _reject(d, "RISK_ENGINE", "strategy_kill", d.candidate.strategy)
            d.snapshot = None
        self.decisions.append(d)
        self.stats.decisions += 1
        explanation = d.explanation(ctx, ev)
        snap_dict = d.snapshot.as_dict() if d.snapshot else {
            "ts": ctx.ts, "spot": ctx.spot, "atr": ctx.atr, "direction": ev.direction, "stage_reached": d.stage_reached,
            "option": d.option.contract.tradingsymbol if d.option else None, "strategy": d.candidate.strategy if d.candidate else None,
            "detail": d.rejection.detail if d.rejection else None,
            # per-family routing verdicts (§72): what each strategy said, not just the winner
            "routing": {n.strategy: n.reason_code for n in d.routing_rejections},
            "trend_scores": {tf: ctx.trend(tf).get("score") for tf in ("1d", "30m", "5m", "1m")},
            "vwap": {"distance_atr": ctx.indicators.get("vwap_distance_atr"), "slope_atr": ctx.indicators.get("vwap_slope_atr")},
            "sweep": ctx.price_action.get("sweep"),  # R1 tracker: was a liquidity sweep on the trigger bar
            "regime": ctx.regime.get("primary"),  # R6 tracker: which regime label gated the trend families
            "score_components": dict(d.score.components) if d.score else None,
            "penalties": dict(d.score.penalties) if d.score else None,
            "ranked": d.ranked, "target_ref": d.target_ref,
            "stop_ref": d.plan.stop_ref if d.plan else None, "target1_ref": d.plan.target1_ref if d.plan else None}
        self._journal(self.dal.insert_signal, self.run_id, signal_id=d.signal_id, setup_id=ev.setup_id, ts=ctx.ts, mode=self.mode, underlying=u,
                               direction=ev.direction, stage=d.stage_reached, status=d.status, explanation=explanation,
                               snapshot=snap_dict, reason_code=d.reason_code, context_snapshot_id=ctx_id,
                               option_type="CE" if ev.direction == "up" else "PE",
                               strategy=d.candidate.strategy if d.candidate else None,
                               strategy_version=d.candidate.version if d.candidate else None,
                               score=d.score.total if d.score else None)
        if d.risk is not None:
            self.dal.insert_risk_decision(self.run_id, d.signal_id, ctx.ts, decision=d.risk.decision, reason_code=d.risk.reason_code,
                                          risk_amount=d.risk.planned_loss, quantity=d.risk.quantity,
                                          all_in_cost=d.risk.costs.total if d.risk.costs else None,
                                          expected_value=d.risk.expected_value,
                                          details={"checks": d.risk.checks, "tier": d.risk.tier, "costs": d.risk.costs.as_dict() if d.risk.costs else None,
                                                   "net_profit": d.risk.net_expected_profit, "rr": d.risk.risk_reward})
        if d.rejection is not None:
            self.rejections.append(d.rejection)
            jsonlog.event("pipeline", "rejected", underlying=u, setup_id=ev.setup_id, signal_id=d.signal_id, ts=ctx.ts,
                          stage=d.stage_reached, decision="no_trade", reason_code=d.reason_code, detail=d.rejection.detail,
                          strategy=d.rejection.strategy, score=d.rejection.score)
            return
        self.stats.approved += 1
        jsonlog.event("pipeline", "approved", underlying=u, setup_id=ev.setup_id, signal_id=d.signal_id, ts=ctx.ts,
                      decision="approved" if self.execute else "would_trade", strategy=d.candidate.strategy,
                      score=d.score.total, option=d.option.contract.tradingsymbol, quantity=d.risk.quantity,
                      tier=d.risk.tier, ev=d.risk.expected_value, explanation=explanation)
        shadow_only = d.candidate.strategy in set(getattr(self.cfg, "shadow_strategies", ()) or ())
        head = "PAPER ORDER" if (self.execute and not shadow_only) else f"WOULD TRADE ({'shadow-only strategy' if shadow_only else self.mode}, no order)"
        self._alert(f"{head}\n{u} {ev.direction.upper()} {d.option.contract.tradingsymbol} x{d.risk.quantity} @ ~{d.option.ask:.2f}\n\n{render(explanation)}", ctx.ts)
        if shadow_only:
            # phase 24: a valid signal deliberately not executed - kept as `valid` with the reason recorded
            self.stats.__dict__["shadow_only"] = self.stats.__dict__.get("shadow_only", 0) + 1
            self._journal(self.dal.update_signal_status, d.signal_id, "valid", "shadow_only_strategy")
            jsonlog.event("pipeline", "shadow_only", underlying=u, signal_id=d.signal_id, strategy=d.candidate.strategy)
            return
        if self.execute:
            self._enter(d, ctx, now)

    def _journal(self, fn, *a, **kw):
        """A journal write must never kill the session; count failures for §87."""
        try:
            return fn(*a, **kw)
        except Exception as exc:  # noqa: BLE001
            self.health.state.db_write_failures += 1
            jsonlog.event("journal", "write_failed", severity="ERROR", fn=getattr(fn, "__name__", "?"), error=repr(exc))
            log.warning("journal write failed (%s): %s", getattr(fn, "__name__", "?"), exc)
            return None

    # --- execution ---------------------------------------------------------------------------------

    def _enter(self, d: Decision, ctx: ContextSnapshot, now: dt.datetime) -> None:
        snap = d.snapshot
        q = self.broker.quote(snap.option_token)
        bid, ask = (q[0], q[1]) if q else (snap.bid, snap.ask)
        h = self.source.health()
        res = execution_gate(snap, d.risk, now=now, spot_now=self.last_spot.get(snap.underlying, ctx.spot), atr=ctx.atr or 0.0,
                             bid=bid, ask=ask, bid_qty=d.option.bid_qty, ask_qty=d.option.ask_qty, quality=ctx.quality,
                             feed_connected=h.connected, reconciled=self._reconciled, broker=self.broker, osm=self.osm,
                             kill_new_entries=self.kills.trading or self.kills.emergency,
                             eod_cutoff_passed=ctx.ts.time() >= self.cfg.eod_cutoff, params=self.gate_params)
        if not res.passed:
            rej = Rejection(d.signal_id, snap.setup_id, snap.underlying, snap.direction, "EXECUTION_GATE", res.reason_code or "gate",
                            strategy=snap.strategy, score=snap.score, details={"checks": res.checks})
            self.rejections.append(rej)
            self.dal.update_signal_status(d.signal_id, "invalidated", res.reason_code)
            jsonlog.event("gate", "blocked", underlying=snap.underlying, signal_id=d.signal_id, reason_code=res.reason_code,
                          checks=res.checks, drift=vars(res.drift) if res.drift else None)
            return
        execution_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"exec/{d.signal_id}/entry"))
        rec = self.osm.new(execution_id, d.signal_id, snap.setup_id, confirmation_at=ctx.ts)
        self.osm.risk_approved(rec, now)
        req = OrderRequest(f"{d.signal_id}:ENTRY", d.signal_id, snap.setup_id, snap.option_token, snap.option_exchange,
                           snap.option_symbol, "BUY", res.quantity, "LIMIT", res.limit_price, res.max_price, purpose="ENTRY")
        self.osm.send(rec, req, now)
        self.stats.orders_sent += 1
        self._pending_entries[execution_id] = (rec, d)
        jsonlog.event("order", "sent", side="BUY", signal_id=d.signal_id, execution_id=execution_id, symbol=snap.option_symbol,
                      quantity=res.quantity, limit=res.limit_price, state=rec.state, broker_order_id=rec.broker_order_id)
        self._poll_orders(now)

    def _poll_orders(self, now: dt.datetime) -> None:
        for eid, (rec, d) in list(self._pending_entries.items()):
            self.osm.poll(rec, now)
            if not rec.terminal:
                continue
            del self._pending_entries[eid]
            self._journal_execution(rec, d.signal_id, now)
            self.health.note_order(filled=rec.state == "POSITION_ACTIVE", rejected=rec.state in ("REJECTED", "FAILED"),
                                   latency_ms=rec.latency.ms("request_at", "fill_at"),
                                   slippage=(rec.average_price - rec.request.limit_price) if (rec.average_price and rec.request and rec.request.limit_price) else None)
            if rec.state == "POSITION_ACTIVE":
                snap = d.snapshot
                ctx = self.last_ctx.get(snap.underlying)
                pos = self.book.open_position(snap, d.plan, eid, rec.filled_quantity, rec.average_price, now,
                                              ctx.bar_index if ctx else 0, entry_vwap=ctx.indicators.get("vwap") if ctx else None)
                self.stats.fills += 1
                self.account.open_positions = len(self.book.open)
                jsonlog.event("position", "opened", position_id=pos.position_id, signal_id=d.signal_id, symbol=snap.option_symbol,
                              quantity=rec.filled_quantity, price=rec.average_price, latency=rec.latency.as_dict())
            else:
                rej = Rejection(d.signal_id, d.setup_id, d.underlying, d.direction, "ORDER", rec.reason or rec.state.lower(),
                                strategy=d.snapshot.strategy, score=d.snapshot.score)
                self.rejections.append(rej)
                self.dal.update_signal_status(d.signal_id, "invalidated", rec.reason or rec.state.lower())
        for eid, (rec, pos) in list(self._pending_exits.items()):
            self.osm.poll(rec, now)
            if not rec.terminal:
                continue
            del self._pending_exits[eid]
            self._journal_execution(rec, pos.signal_id, now)
            if rec.filled_quantity > 0:
                result = self.book.on_exit_filled(pos, rec.filled_quantity, rec.average_price, now,
                                                  spot_now=self.last_spot.get(pos.underlying, pos.snapshot.underlying_price))
                if result is not None:
                    self._on_trade_closed(result, now)
            else:
                # exit did not fill (timeout/reject): position stays open, the monitor will try again next bar
                self.book.exit_abandoned(pos)
                jsonlog.event("order", "exit_unfilled", severity="WARN", position_id=pos.position_id, reason=rec.reason)

    def _journal_execution(self, rec, signal_id: str, now: dt.datetime) -> None:
        req = rec.request
        execution_id = rec.execution_id
        slip = None
        if rec.average_price is not None and req is not None and req.limit_price is not None:
            slip = round(rec.average_price - req.limit_price, 2) if req.side == "BUY" else round(req.limit_price - rec.average_price, 2)
        self.dal.insert_execution(self.run_id, signal_id, now, mode=self.mode, side=req.side if req else "?", state=rec.state,
                                  broker_order_id=rec.broker_order_id, ordertag=req.client_order_id if req else None,
                                  requested_price=req.limit_price if req else None, fill_price=rec.average_price,
                                  quantity=req.quantity if req else 0, filled_quantity=rec.filled_quantity,
                                  latency_ms=rec.latency.ms("request_at", "fill_at"), slippage=slip,
                                  details={"execution_id": execution_id, "history": rec.history, "latency": rec.latency.as_dict(), "reason": rec.reason,
                                           "purpose": req.purpose if req else None, "profile": getattr(self.broker.p, "profile", None) if self.broker else None})

    # --- positions -----------------------------------------------------------------------------------

    def _manage(self, u: str, ctx: ContextSnapshot, now: dt.datetime) -> None:
        for pos in self.book.by_underlying(u):
            bid, spread_pct = self._option_bid(pos)
            if bid is None:
                continue
            eod = ctx.ts.time() >= self.cfg.eod_cutoff
            risk_hit = -self.account.realized_today >= self.cfg.capital * self.cfg.daily_loss_cap_pct
            sig = self.book.manage(pos, ctx, option_bid=bid, option_spread_pct=spread_pct, eod=eod, risk_limit_hit=risk_hit, kills=self.kills)
            if sig is not None:
                self._exit(pos, sig, bid, now)

    def _option_bid(self, pos: Position) -> tuple[float | None, float | None]:
        if self.chains is not None:
            c = self.chains.caches.get(pos.underlying)
            q = c.quotes.get(pos.token) if c else None
            if q is not None and q.bid > 0:
                return q.bid, q.spread_pct
        if self.broker is not None:
            q = self.broker.quote(pos.token)
            if q is not None and q[0] > 0:
                return q[0], (q[1] - q[0]) / (0.5 * (q[0] + q[1])) * 100.0
        return (pos.last_option_price, None) if pos.last_option_price else (None, None)

    def _exit(self, pos: Position, sig: ExitSignal, bid: float, now: dt.datetime) -> None:
        req = self.book.exit_request(pos, sig, bid=bid)
        execution_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"exec/{pos.signal_id}/{req.client_order_id}"))
        rec = self.osm.new(execution_id, pos.signal_id, pos.setup_id, confirmation_at=now)
        self.osm.risk_approved(rec, now)
        self.osm.send(rec, req, now)
        self._pending_exits[execution_id] = (rec, pos)
        jsonlog.event("order", "sent", side="SELL", position_id=pos.position_id, signal_id=pos.signal_id, reason=sig.reason,
                      quantity=req.quantity, limit=req.limit_price, state=rec.state)
        self._poll_orders(now)

    def _on_trade_closed(self, result, now: dt.datetime) -> None:
        record_result(self.account, result.net_pnl, now)
        self.account.open_positions = len(self.book.open)
        self.stats.trades_closed += 1
        self.stats.net_pnl = round(self.stats.net_pnl + result.net_pnl, 2)
        self.stats.gross_pnl = round(self.stats.gross_pnl + result.gross_pnl, 2)
        self.stats.costs = round(self.stats.costs + result.total_cost, 2)
        r_mult = round(result.net_pnl / (result.quantity * (result.entry_price - result.initial_sl)), 3) if result.entry_price > result.initial_sl else None
        self.dal.insert_trade_result(self.run_id, result.signal_id, entry_ts=result.entry_ts, exit_ts=result.exit_ts,
                                     entry_price=result.entry_price, exit_price=result.exit_price, quantity=result.quantity,
                                     gross_pnl=result.gross_pnl, costs=result.total_cost, net_pnl=result.net_pnl, r_multiple=r_mult,
                                     exit_reason=result.exit_reason, mae=result.mae, mfe=result.mfe, details=result.as_dict())
        jsonlog.event("trade", "closed", trade_id=result.trade_id, signal_id=result.signal_id, symbol=result.option_symbol,
                      exit_reason=result.exit_reason, gross=result.gross_pnl, costs=result.total_cost, net=result.net_pnl,
                      mfe=result.mfe, mae=result.mae, holding_minutes=result.holding_minutes, fingerprint=result.fingerprint)
        self._alert(f"PAPER TRADE CLOSED {result.exit_reason}\n{result.option_symbol} x{result.quantity} {result.entry_price:.2f} -> {result.exit_price:.2f}\n"
                    f"gross {result.gross_pnl:+.0f} costs {result.total_cost:.0f} net {result.net_pnl:+.0f} | day net {self.account.realized_today:+.0f}", now)
        if -self.account.realized_today >= self.cfg.capital * self.cfg.daily_loss_cap_pct and not self.account.daily_lock:
            self.account.daily_lock = True
            self.kills.kill_trading(now, "daily_loss_cap")
            self._journal_kill("trading", "on", "daily_loss_cap")

    def _journal_kill(self, switch: str, action: str, reason: str, target: str | None = None) -> None:
        self.dal.insert_kill_switch_event(self.run_id, self.clock(), switch=switch, action=action, reason=reason,
                                          details={"target": target} if target else {})
        jsonlog.event("kill_switch", action, severity="WARN", switch=switch, reason=reason, target=target)

    # --- end of day ----------------------------------------------------------------------------------------

    def _eod_sweep(self, now: dt.datetime) -> None:
        self._eod_swept = True
        if not self.execute:
            return
        bids = {}
        for pos in self.book.open.values():
            b, _ = self._option_bid(pos)
            if b is not None:
                bids[pos.token] = b
        for pos, req in self.book.eod_exit_requests(bids):
            execution_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"exec/{pos.signal_id}/{req.client_order_id}"))
            rec = self.osm.new(execution_id, pos.signal_id, pos.setup_id, confirmation_at=now)
            self.osm.risk_approved(rec, now)
            self.osm.send(rec, req, now)
            self._pending_exits[execution_id] = (rec, pos)
            jsonlog.event("order", "sent", side="SELL", position_id=pos.position_id, reason="EOD_EXIT", quantity=req.quantity)
        self._poll_orders(now)

    def _eod(self, stopped: bool = False) -> None:
        now = self.clock()
        if self.execute:
            if not self._eod_swept:
                self._eod_sweep(now)
            # give resting paper exits their latency, then force-close anything left at the last bid (§69 emergency protection)
            for _ in range(3):
                self._poll_orders(now + dt.timedelta(seconds=5))
            for pos in list(self.book.open.values()):
                bid, _ = self._option_bid(pos)
                if bid is None:
                    bid = pos.entry_price
                if pos.pending_exit is not None:
                    self.book.exit_abandoned(pos)
                pos.pending_reason = "EMERGENCY_EXIT"
                jsonlog.event("position", "forced_close", severity="WARN", position_id=pos.position_id, price=bid, note="paper EOD force-close")
                result = self.book.on_exit_filled(pos, pos.quantity, bid, now, spot_now=self.last_spot.get(pos.underlying, pos.snapshot.underlying_price))
                if result is not None:
                    self._on_trade_closed(result, now)
        self.stats.__dict__["rejections"] = summarize(self.rejections)
        self.stats.__dict__["kill_events"] = len(self.kills.log)
        self.stats.__dict__["health"] = {"alerts": len(self.health.history), "final": self.health.worst(),
                                         "codes": sorted({a.code for a in self.health.history if not a.cleared})}
        super()._eod(stopped=stopped)
