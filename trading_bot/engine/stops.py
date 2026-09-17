"""Stop loss, targets, trailing and the thesis monitor (spec §26, §28,
§31, §32).

The stop lives on the UNDERLYING (the strategy's structural invalidation)
and is translated to an option price through the option's delta/gamma
response - the option stop is a consequence of the market stop, never the
other way round ("Never determine SL from desired R:R", §26; "SL is
market-driven", §92 #18). Targets are levels/expected-move driven (§28),
never a universal R multiple (§92 #21).

`ThesisMonitor.check()` is the §32 loop: each bar it asks whether the
original thesis still holds and returns one of the fifteen exit reasons
or None. Trailing (§31) only ever tightens - "never loosen risk merely to
avoid a loss".
"""
from dataclasses import dataclass, field

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.expected_move import option_response
from trading_bot.engine.strategies.base import Candidate, sign

EXIT_REASONS: tuple[str, ...] = (
    "TARGET_1", "TARGET_2", "EXTENDED_TARGET", "STOP_LOSS", "THESIS_INVALIDATION", "TRAILING_STOP",
    "STRUCTURE_REVERSAL", "MOMENTUM_FAILURE", "VWAP_FAILURE", "REGIME_CHANGE", "OPTION_LIQUIDITY_FAILURE",
    "RISK_LIMIT", "EOD_EXIT", "EMERGENCY_EXIT", "MANUAL_KILL_SWITCH",
)


@dataclass(frozen=True)
class StopParams:
    min_stop_atr: float = 0.3  # a stop tighter than this is noise, widen to it (still structural: it is the buffer)
    max_stop_atr: float = 2.5  # wider than this and the setup is not worth pricing - reject, never shrink
    target2_extension: float = 1.6  # TP2 = entry + 1.6 x (TP1 - entry) unless a level caps it
    extended_target_atr: float = 3.0
    partial_at_target1_pct: float = 0.5  # share of quantity taken at TP1 (rest trails)
    trail_atr: float = 1.0  # ATR trail behind the best price once TP1 is hit
    trail_activation_atr: float = 1.0  # trailing starts after this much favourable move
    breakeven_after_atr: float = 0.75  # move the stop to entry after this much favourable move
    max_bars_without_progress: int = 8  # thesis stall (§32 MOMENTUM_FAILURE)
    option_stop_floor_pct: float = 0.35  # never let the option stop exceed 35% of premium (§25 caps it via sizing anyway)


@dataclass
class Plan:
    """The trade plan on both price scales."""
    direction: str
    entry_ref: float  # underlying
    stop_ref: float  # underlying, structural
    target1_ref: float
    target2_ref: float
    extended_ref: float
    stop_atr: float
    option_entry: float
    option_stop: float
    option_target1: float
    option_target2: float
    risk_per_unit: float  # option points at risk per unit (entry - stop)
    reward1_per_unit: float
    notes: list[str] = field(default_factory=list)

    @property
    def rr1(self) -> float:
        return self.reward1_per_unit / self.risk_per_unit if self.risk_per_unit > 0 else 0.0


@dataclass(frozen=True)
class PlanRejected:
    reason_code: str
    detail: str = ""


def _option_at(entry_premium: float, delta: float, gamma: float | None, underlying_move: float, direction: str, plan_dir: str) -> float:
    """Premium after an underlying move of `underlying_move` points in
    `direction` ("up"/"down") for a long option on side `plan_dir`."""
    favourable = direction == plan_dir
    change = option_response(underlying_move, entry_premium, delta, gamma, theta_per_day=0.0)
    if favourable:
        return entry_premium + change
    # adverse: delta shrinks as we move away (gamma works against the magnitude), floor at a sliver of premium
    d = abs(delta)
    g = abs(gamma or 0.0)
    loss = d * underlying_move - 0.5 * g * underlying_move * underlying_move
    return max(0.05, entry_premium - max(0.0, loss))


def build_plan(ctx: ContextSnapshot, cand: Candidate, *, option_entry: float, delta: float, gamma: float | None,
               target_ref: float | None = None, params: StopParams | None = None) -> Plan | PlanRejected:
    p = params or StopParams()
    atr = ctx.atr or 0.0
    if atr <= 0:
        return PlanRejected("no_atr")
    d = cand.direction
    stop = cand.invalidation
    stop_dist = abs(cand.entry_ref - stop)
    notes: list[str] = []
    if stop_dist / atr < p.min_stop_atr:
        stop = cand.entry_ref - sign(d) * p.min_stop_atr * atr
        stop_dist = p.min_stop_atr * atr
        notes.append(f"stop widened to {p.min_stop_atr} ATR minimum")
    if stop_dist / atr > p.max_stop_atr:
        return PlanRejected("stop_too_wide", f"{stop_dist / atr:.2f} ATR > {p.max_stop_atr}")
    t1 = target_ref if target_ref is not None else cand.target_ref
    if (t1 - cand.entry_ref) * sign(d) <= 0:
        return PlanRejected("target_not_beyond_entry")
    reward_dist = abs(t1 - cand.entry_ref)
    t2 = cand.entry_ref + sign(d) * reward_dist * p.target2_extension
    # the extended target is beyond TP2 by construction (at least 1 ATR past it)
    ext = max((cand.entry_ref + sign(d) * p.extended_target_atr * atr) * sign(d), (t2 + sign(d) * atr) * sign(d)) * sign(d)
    o_stop = _option_at(option_entry, delta, gamma, stop_dist, "down" if d == "up" else "up", d)
    o_t1 = _option_at(option_entry, delta, gamma, reward_dist, d, d)
    o_t2 = _option_at(option_entry, delta, gamma, abs(t2 - cand.entry_ref), d, d)
    risk_unit = option_entry - o_stop
    if risk_unit <= 0:
        return PlanRejected("option_stop_not_below_entry")
    if risk_unit / option_entry > p.option_stop_floor_pct:
        # the structural stop implies losing more than the floor share of premium: keep the STRUCTURAL stop
        # (never tighten it to fit) but record it - sizing (§27) will shrink quantity or reject
        notes.append(f"option risk {risk_unit / option_entry:.0%} of premium")
    return Plan(direction=d, entry_ref=cand.entry_ref, stop_ref=round(stop, 2), target1_ref=round(t1, 2),
                target2_ref=round(t2, 2), extended_ref=round(ext, 2), stop_atr=round(stop_dist / atr, 3),
                option_entry=round(option_entry, 2), option_stop=round(o_stop, 2), option_target1=round(o_t1, 2),
                option_target2=round(o_t2, 2), risk_per_unit=round(risk_unit, 2), reward1_per_unit=round(o_t1 - option_entry, 2),
                notes=notes)


# --- §31 trailing + §32 thesis monitor ------------------------------------------------------------


@dataclass
class TradeState:
    plan: Plan
    quantity: int
    entry_bar: int
    best_ref: float  # most favourable underlying price seen
    current_stop_ref: float
    target1_hit: bool = False
    partial_done: bool = False
    last_progress_bar: int = 0
    entry_vwap: float | None = None  # which side of VWAP the thesis was entered on
    stop_moves: list[tuple[int, float, str]] = field(default_factory=list)  # (bar, new_stop, why)


@dataclass(frozen=True)
class ExitSignal:
    reason: str
    quantity: int  # how much to exit (partial at TP1)
    detail: str = ""


class ThesisMonitor:
    def __init__(self, params: StopParams | None = None):
        self.p = params or StopParams()

    def open(self, plan: Plan, quantity: int, bar_index: int, entry_vwap: float | None = None) -> TradeState:
        return TradeState(plan=plan, quantity=quantity, entry_bar=bar_index, best_ref=plan.entry_ref,
                          current_stop_ref=plan.stop_ref, last_progress_bar=bar_index, entry_vwap=entry_vwap)

    def _tighten(self, st: TradeState, new_stop: float, bar: int, why: str) -> None:
        d = st.plan.direction
        if (new_stop - st.current_stop_ref) * sign(d) > 0:  # only ever toward price
            st.current_stop_ref = round(new_stop, 2)
            st.stop_moves.append((bar, st.current_stop_ref, why))

    def check(self, ctx: ContextSnapshot, st: TradeState, *, option_bid: float | None = None,
              option_spread_pct: float | None = None, eod: bool = False, risk_limit_hit: bool = False,
              kill: bool = False, emergency: bool = False) -> ExitSignal | None:
        """Evaluate at a bar close (or on a tick for the hard stop). Priority
        follows the spec's severity: kills and emergencies first, then hard
        risk, then market invalidation, then targets/trailing."""
        d = st.plan.direction
        atr = ctx.atr or 0.0
        px = ctx.spot
        bar = ctx.bar_index
        if kill:
            return ExitSignal("MANUAL_KILL_SWITCH", st.quantity)
        if emergency:
            return ExitSignal("EMERGENCY_EXIT", st.quantity)
        if risk_limit_hit:
            return ExitSignal("RISK_LIMIT", st.quantity)
        if eod:
            return ExitSignal("EOD_EXIT", st.quantity)
        if option_spread_pct is not None and option_spread_pct > 8.0:
            return ExitSignal("OPTION_LIQUIDITY_FAILURE", st.quantity, f"spread {option_spread_pct:.1f}%")

        # favourable progress bookkeeping
        if (px - st.best_ref) * sign(d) > 0:
            st.best_ref = px
            st.last_progress_bar = bar
        moved = (px - st.plan.entry_ref) * sign(d)

        # hard stop (structural or trailed)
        if (px - st.current_stop_ref) * sign(d) <= 0:
            reason = "TRAILING_STOP" if st.stop_moves else "STOP_LOSS"
            return ExitSignal(reason, st.quantity, f"underlying {px:.2f} through {st.current_stop_ref:.2f}")

        # thesis invalidation: structure / regime / VWAP / momentum
        ev = (ctx.structure or {}).get("last_event") or {}
        against = ("bos_down", "choch_down") if d == "up" else ("bos_up", "choch_up")
        if ev.get("kind") in against and bar - int(ev.get("index", -10**6)) <= 1:
            return ExitSignal("STRUCTURE_REVERSAL", st.quantity, ev["kind"])
        regime = ctx.regime.get("primary")
        if regime in ("NO_TRADE", "UNSTABLE"):
            return ExitSignal("REGIME_CHANGE", st.quantity, regime)
        if (regime in ("STRONG_BEAR", "BEAR") and d == "up") or (regime in ("STRONG_BULL", "BULL") and d == "down"):
            return ExitSignal("REGIME_CHANGE", st.quantity, regime)
        vwap = ctx.indicators.get("vwap")
        if vwap is not None and st.entry_vwap is not None and (st.plan.entry_ref - st.entry_vwap) * sign(d) > 0                 and (px - vwap) * sign(d) < -0.5 * atr:
            # we entered on the right side of VWAP and have now lost it decisively
            return ExitSignal("VWAP_FAILURE", st.quantity)
        if bar - st.last_progress_bar >= self.p.max_bars_without_progress and moved < 0.25 * atr:
            return ExitSignal("MOMENTUM_FAILURE", st.quantity, f"{bar - st.last_progress_bar} bars without progress")
        t5 = ctx.trend("5m")
        if t5.get("label") == ("STRONG_BEAR" if d == "up" else "STRONG_BULL"):
            return ExitSignal("THESIS_INVALIDATION", st.quantity, f"5m trend {t5['label']}")

        # targets
        if not st.target1_hit and (px - st.plan.target1_ref) * sign(d) >= 0:
            st.target1_hit = True
            qty = int(round(st.quantity * self.p.partial_at_target1_pct))
            self._tighten(st, st.plan.entry_ref, bar, "breakeven_at_target1")
            if qty > 0 and not st.partial_done:
                st.partial_done = True
                return ExitSignal("TARGET_1", qty)
        if st.target1_hit and (px - st.plan.extended_ref) * sign(d) >= 0:
            return ExitSignal("EXTENDED_TARGET", st.quantity)
        if st.target1_hit and (px - st.plan.target2_ref) * sign(d) >= 0:
            return ExitSignal("TARGET_2", st.quantity)

        # trailing (§31): breakeven, then ATR/structure trail once activated
        if moved >= self.p.breakeven_after_atr * atr:
            self._tighten(st, st.plan.entry_ref, bar, "breakeven")
        if moved >= self.p.trail_activation_atr * atr:
            self._tighten(st, st.best_ref - sign(d) * self.p.trail_atr * atr, bar, "atr_trail")
            swing = ctx.structure.get("swing_low" if d == "up" else "swing_high")
            if swing is not None and (swing - st.plan.entry_ref) * sign(d) > 0:
                self._tighten(st, swing - sign(d) * 0.25 * atr, bar, "structure_trail")
        return None
