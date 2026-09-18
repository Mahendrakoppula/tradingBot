"""The decision pipeline after TRADE_READY (spec §93, from STRATEGY
ROUTING to SIGNAL SNAPSHOT): one pure function that turns a pre-signal
event plus the current context, option chain and account state into a
Decision - either a locked SignalSnapshot ready for the execution gate,
or a Rejection that says exactly where it stopped and why (§72).

    route -> score/rank -> expected move -> no-chase -> distance-to-level
    -> option selection -> decay filter -> stop plan -> risk engine -> snapshot

No I/O, no clock, no broker: the loop feeds it and journals what comes
back. SHADOW and PAPER run the same function; only what the loop does with
an approved Decision differs (§51 parity).
"""
import datetime as dt
import uuid
from dataclasses import dataclass, field

from trading_bot.costs import CostRates
from trading_bot.engine import expected_move as em
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.execution.snapshot import SignalSnapshot, lock_snapshot
from trading_bot.engine.explain import NOT_EVALUATED, build_explanation
from trading_bot.engine.option_chain import ChainCache, OptionQuote
from trading_bot.engine.option_select import SelectParams, decay_filter, select
from trading_bot.engine.presignal import StageEvent
from trading_bot.engine.rejections import Rejection
from trading_bot.engine.risk_engine import AccountState, RiskDecision, RiskLimits, evaluate
from trading_bot.engine.scoring import ExternalInputs, Score, rank, score
from trading_bot.engine.stops import Plan, PlanRejected, StopParams, build_plan
from trading_bot.engine.strategies import Candidate, StrategyParams, route


@dataclass(frozen=True)
class PipelineParams:
    strategy: StrategyParams = StrategyParams()
    move: em.MoveParams = em.MoveParams()
    select: SelectParams = SelectParams()
    stops: StopParams = StopParams()
    limits: RiskLimits = RiskLimits()
    rates: CostRates = CostRates()
    min_score: int = 40
    counter_trend_min_evidence: int = 6

    @classmethod
    def from_config(cls, cfg) -> "PipelineParams":
        return cls(
            strategy=StrategyParams(level_proximity_atr=cfg.level_proximity_atr),
            move=em.MoveParams(eod_cutoff=cfg.eod_cutoff),
            select=SelectParams(delta_min=cfg.option_delta_min, delta_max=cfg.option_delta_max,
                                max_spread_pct=cfg.option_max_spread_pct, min_oi=cfg.option_min_oi,
                                dte_max=cfg.option_dte_max, expiry_day_allowed=cfg.option_expiry_day_allowed),
            limits=RiskLimits(capital=cfg.capital, risk_per_trade_pct=cfg.risk_per_trade_pct,
                              daily_loss_cap_pct=cfg.daily_loss_cap_pct, weekly_loss_cap_pct=cfg.weekly_loss_cap_pct,
                              max_consecutive_losses=cfg.max_consecutive_losses, max_trades_per_day=cfg.max_trades_per_day,
                              max_open_positions=cfg.max_open_positions, max_spread_pct=cfg.option_max_spread_pct,
                              preferred_net_reward=cfg.preferred_net_reward, tier_b_enabled=cfg.tier_b_enabled,
                              tier_b_min_score=cfg.tier_b_min_score),
            min_score=cfg.min_score,
        )


@dataclass
class Decision:
    signal_id: str
    setup_id: str | None
    ts: dt.datetime
    underlying: str
    direction: str
    stage_reached: str
    rejection: Rejection | None = None
    candidate: Candidate | None = None
    score: Score | None = None
    move: em.ExpectedMove | None = None
    target_ref: float | None = None
    option: OptionQuote | None = None
    option_rejections: list = field(default_factory=list)
    decay: object = None
    plan: Plan | None = None
    risk: RiskDecision | None = None
    snapshot: SignalSnapshot | None = None
    routing_rejections: list = field(default_factory=list)
    ranked: list = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.snapshot is not None and self.risk is not None and self.risk.approved

    @property
    def status(self) -> str:
        if self.approved:
            return "valid"
        return self.rejection.status if self.rejection else "rejected"

    @property
    def reason_code(self) -> str | None:
        return self.rejection.reason_code if self.rejection else None

    def explanation(self, ctx: ContextSnapshot, event: StageEvent) -> dict:
        """The §68 template with M2 fields filled in from the pipeline."""
        ex = build_explanation(ctx, event)
        c, s, m, o, p, r = self.candidate, self.score, self.move, self.option, self.plan, self.risk
        if c:
            ex["Strategy"] = f"{c.strategy} v{c.version} ({'counter-trend' if c.counter_trend else 'with trend'}): " + "; ".join(c.reasons)
            ex["Confirmation"] = c.confirmation + (f" | score {s.total}" if s else "")
        if m:
            ex["Expected Move"] = f"{m.remaining_points:.1f} pts ({m.remaining_atr:.2f} ATR), boundary {m.boundary:.2f}, time left {m.time_fraction:.0%}"
            ex["Remaining Move"] = f"consumed {m.consumed_atr:.2f} ATR; target {self.target_ref:.2f}" if self.target_ref else f"consumed {m.consumed_atr:.2f} ATR"
        if o:
            ex["Option"] = f"{o.contract.tradingsymbol} mid {o.mid:.2f} spread {o.spread_pct:.2f}% delta {o.delta:.2f} theta {o.theta_per_day:.2f}/day ({o.greeks_source})" if o.delta is not None else o.contract.tradingsymbol
            ex["Strike"] = f"{o.contract.strike:.0f} {o.contract.option_type}"
            ex["Expiry"] = o.contract.expiry.isoformat()
        elif self.stage_reached in ("OPTION_SELECTION", "DECAY_FILTER"):
            ex["Option"] = f"no acceptable option ({self.reason_code})"
        if p and r:
            ex["Risk"] = f"SL {p.option_stop:.2f} (underlying {p.stop_ref:.2f}, {p.stop_atr:.2f} ATR); {r.lots} lot(s) = {r.quantity}; planned loss Rs.{r.planned_loss:.0f} of Rs.{r.max_permitted_loss:.0f}"
            ex["Expected Net Reward"] = f"Rs.{r.net_expected_profit:.0f} net (gross {r.gross_expected_profit:.0f}, costs {r.costs.total:.0f}) tier {r.tier}" if r.costs else f"Rs.{r.net_expected_profit:.0f}"
            ex["Expected Value"] = f"Rs.{r.expected_value:.0f} at p={r.win_probability:.2f}, R:R {r.risk_reward:.2f}"
        elif p:
            ex["Risk"] = f"SL {p.option_stop:.2f} (underlying {p.stop_ref:.2f})"
        if self.approved:
            ex["Decision"] = f"{r.decision} - {r.tier} - {r.quantity} x {o.contract.tradingsymbol}"
        else:
            ex["Decision"] = f"NO TRADE - {self.stage_reached}: {self.reason_code}"
        for k in ("Expected Move", "Remaining Move", "Option", "Strike", "Expiry", "Risk", "Expected Net Reward", "Expected Value"):
            if ex[k] == NOT_EVALUATED:
                ex[k] = "not reached"
        return ex


def _reject(d: Decision, stage: str, code: str, detail: str = "", **details) -> Decision:
    d.stage_reached = stage
    d.rejection = Rejection(d.signal_id, d.setup_id, d.underlying, d.direction, stage, code, detail,
                            strategy=d.candidate.strategy if d.candidate else None,
                            score=d.score.total if d.score else None,
                            counter_trend=bool(d.candidate and d.candidate.counter_trend), details=details)
    return d


def decide(ctx: ContextSnapshot, event: StageEvent, *, setup_evidence: int, family_hint: str | None,
           chain: ChainCache | None, account: AccountState, params: PipelineParams | None = None,
           kill_reason: str | None = None, session_elapsed: float = 0.5) -> Decision:
    p = params or PipelineParams()
    signal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"signal/{event.setup_id}/{ctx.ts.isoformat()}"))
    d = Decision(signal_id, event.setup_id, ctx.ts, ctx.underlying, event.direction, "STRATEGY_ROUTING")
    if kill_reason:
        return _reject(d, "STRATEGY_ROUTING", kill_reason)

    # --- routing (§15) -------------------------------------------------------------------
    rr = route(ctx, event.direction, params=p.strategy, counter_trend_cleared=setup_evidence >= p.counter_trend_min_evidence,
               family_hint=family_hint)
    d.routing_rejections = rr.rejections
    if rr.gate:
        return _reject(d, "STRATEGY_ROUTING", rr.gate)
    if not rr.candidates:
        top = sorted({n.reason_code for n in rr.rejections})
        return _reject(d, "STRATEGY_ROUTING", "no_strategy_match", ", ".join(top[:5]))

    # --- scoring + ranking (§16, §35) ----------------------------------------------------------
    scored = [(c, score(ctx, c)) for c in rr.candidates]
    ranked = rank(scored, open_directions=account.open_directions, max_selected=1, min_score=p.min_score)
    d.ranked = [(r.candidate.strategy, r.score.total, r.selected, r.reason) for r in ranked]
    chosen = next((r for r in ranked if r.selected), None)
    if chosen is None:
        why = ranked[0].reason if ranked else "nothing_ranked"
        d.candidate, d.score = (ranked[0].candidate, ranked[0].score) if ranked else (None, None)
        return _reject(d, "RANKING", why)
    d.candidate, d.score = chosen.candidate, chosen.score
    cand = chosen.candidate

    # --- expected move / no-chase / distance (§21-§23) -----------------------------------------
    move = em.estimate(ctx, cand, p.move)
    d.move = move
    chase = em.no_chase(ctx, cand, move, p.move)
    if not chase.ok:
        return _reject(d, "NO_CHASE", chase.reason_code or "no_chase", chase.detail)
    lc = em.distance_check(ctx, cand, move, p.move)
    if not lc.ok:
        return _reject(d, "DISTANCE_TO_LEVEL", lc.reason_code or "blocked", lc.blocked_by or "")
    d.target_ref = lc.target_ref

    # --- option selection + decay (§19, §20, §24) ------------------------------------------------
    if chain is None or not chain.quotes:
        return _reject(d, "OPTION_SELECTION", "no_option_chain")
    if not chain.fresh(ctx.ts):
        return _reject(d, "OPTION_SELECTION", "option_cache_stale")
    sel = select(chain.for_side(cand.option_type), cand.option_type, ctx.ts.date(), p.limits.capital, p.select)
    d.option_rejections = sel.rejections
    if not sel.ok:
        reasons = sorted({r.reason_code for r in sel.rejections})
        return _reject(d, "OPTION_SELECTION", "no_acceptable_option", ", ".join(reasons[:5]))
    q = sel.chosen
    d.option = q
    move_points = min(move.remaining_points, abs(lc.target_ref - ctx.spot))
    dv = decay_filter(q, move_points, p.rates, p.select)
    d.decay = dv
    if not dv.ok:
        return _reject(d, "DECAY_FILTER", dv.reason_code or "decay", f"net {dv.net_expected_gain:.2f} pts")
    # rescore with the option facts now known (§16 liquidity/option components + penalties)
    d.score = score(ctx, cand, ExternalInputs(spread_pct=q.spread_pct, open_interest=q.oi, theta_pct_of_premium=q.theta_pct_of_premium,
                                             remaining_move_atr=move.remaining_atr, move_consumed_atr=move.consumed_atr))
    if d.score.total < p.min_score:
        return _reject(d, "SCORING", "score_below_minimum_after_option_facts", str(d.score.total))

    # --- stop plan (§26, §28) ---------------------------------------------------------------------------
    plan = build_plan(ctx, cand, option_entry=q.ask, delta=q.delta or 0.5, gamma=q.gamma, target_ref=lc.target_ref, params=p.stops)
    if isinstance(plan, PlanRejected):
        return _reject(d, "STOP_PLAN", plan.reason_code, plan.detail)
    d.plan = plan

    # --- risk engine (§25-§30, §33-§35): the final authority -------------------------------------------
    risk = evaluate(plan, q, account, p.limits, p.rates, score=d.score.total, expected_move_ok=True, thesis_ok=True)
    d.risk = risk
    if not risk.approved:
        return _reject(d, "RISK_ENGINE", risk.reason_code or "risk_rejected", risk.detail)

    # --- snapshot lock (§39) ------------------------------------------------------------------------------
    d.stage_reached = "SNAPSHOT"
    d.snapshot = lock_snapshot(
        signal_id=signal_id, setup_id=event.setup_id, ts=ctx.ts, underlying=ctx.underlying, underlying_price=ctx.spot,
        direction=cand.direction, strategy=cand.strategy, strategy_version=cand.version,
        trend=ctx.trend("5m").get("label", "n/a"), regime=ctx.regime.get("primary", "n/a"),
        structure=(ctx.structure.get("last_event") or {}).get("kind", "none"), score=d.score.total,
        expected_move_points=move.remaining_points, remaining_move_points=round(abs(lc.target_ref - ctx.spot), 2),
        option_token=q.contract.token, option_symbol=q.contract.tradingsymbol, option_exchange=q.contract.exchange,
        option_type=q.contract.option_type, strike=q.contract.strike, expiry=q.contract.expiry.isoformat(),
        lot_size=q.contract.lotsize, bid=q.bid, ask=q.ask, spread_pct=round(q.spread_pct, 3), iv=q.iv, delta=q.delta,
        gamma=q.gamma, theta=q.theta_per_day, vega=q.vega, option_entry=plan.option_entry, option_stop=plan.option_stop,
        option_target1=plan.option_target1, option_target2=plan.option_target2, stop_ref=plan.stop_ref,
        target1_ref=plan.target1_ref, quantity=risk.quantity, risk_rupees=risk.planned_loss,
        fingerprint=cand.evidence.get("fingerprint", ""),
        extra={"tier": risk.tier, "ev": risk.expected_value, "net_reward": risk.net_expected_profit, "lots": risk.lots,
               "greeks_source": q.greeks_source, "decay": vars(dv), "ranked": d.ranked},
    )
    return d
