"""Expected move (spec §21), no-chase (§22), distance-to-level (§23) and
time-of-day scaling (§37) - the "is there still a trade here?" checks
that sit between a confirmed candidate and option selection.

Underlying movement is estimated in ATR units and shrunk by how much of
the session is left, how much the day's range has already been used, and
regime/trend/momentum context. The option response is NOT linear (§21):
an OTM/ATM long option gains delta as spot moves toward it, so the
projected option move integrates delta + gamma over the underlying move
and then loses spread, slippage and costs on the way to a NET reward.

Everything here is first-cut and configurable; M3 replaces the
multipliers with measured behaviour.
"""
import datetime as dt
from dataclasses import dataclass

from trading_bot.engine.clock import session_open
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.strategies.base import Candidate, sign

@dataclass(frozen=True)
class MoveParams:
    base_move_atr: float = 1.5  # expected remaining range over a full session, in ATR(5m)
    daily_range_atr_cap: float = 6.0  # typical full-day range in 5m ATRs; consumed share shrinks the estimate
    min_time_fraction: float = 0.15  # never expect less than this share of the base move
    eod_cutoff: dt.time = dt.time(15, 20)  # no move counted after the flatten time
    regime_mult: dict | None = None  # overrides for REGIME_MULT
    min_remaining_atr: float = 0.75  # §22 reject below this
    max_consumed_atr: float = 2.0  # §22 "extension": how far past the origin level the move has run
    max_vwap_distance_atr: float = 2.5
    blocked_level_atr: float = 0.5  # §23 target blocked if an opposing level sits within this


REGIME_MULT = {
    "STRONG_BULL": 1.2, "STRONG_BEAR": 1.2, "BULL": 1.0, "BEAR": 1.0, "WEAK_BULL": 0.8, "WEAK_BEAR": 0.8,
    "BREAKOUT": 1.2, "BREAKDOWN": 1.2, "EXPANSION": 1.1, "HIGH_VOLATILITY": 1.1, "TRANSITION": 0.8,
    "RANGE": 0.6, "COMPRESSION": 0.5, "LOW_VOLATILITY": 0.5, "CHOPPY": 0.4, "NORMAL": 0.9,
    "UNSTABLE": 0.0, "NO_TRADE": 0.0,
}


@dataclass(frozen=True)
class ExpectedMove:
    remaining_atr: float  # realistic remaining move in the trade direction, ATR units
    remaining_points: float
    time_fraction: float  # share of the tradeable session left
    consumed_atr: float  # move already completed from the setup's origin level
    day_range_used: float  # today's range / (cap * ATR), 0-1+
    regime_mult: float
    trend_mult: float
    momentum_mult: float
    boundary: float  # spot + remaining move in the direction
    notes: tuple[str, ...] = ()


def time_fraction(now: dt.datetime, params: MoveParams) -> float:
    end = now.replace(hour=params.eod_cutoff.hour, minute=params.eod_cutoff.minute, second=0, microsecond=0)
    start = now.replace(hour=session_open().hour, minute=session_open().minute, second=0, microsecond=0)
    total = (end - start).total_seconds()
    left = (end - now).total_seconds()
    return max(0.0, min(1.0, left / total)) if total > 0 else 0.0


def estimate(ctx: ContextSnapshot, cand: Candidate, params: MoveParams | None = None) -> ExpectedMove:
    p = params or MoveParams()
    atr = ctx.atr or 0.0
    notes: list[str] = []
    tf = time_fraction(ctx.ts, p)
    # sqrt-of-time: range grows with the square root of remaining minutes
    time_scale = max(p.min_time_fraction, tf ** 0.5)

    # today's range consumed so far (session high/low from the levels map)
    hi, lo = ctx.levels.get("session_high"), ctx.levels.get("session_low")
    used = ((hi - lo) / (p.daily_range_atr_cap * atr)) if (hi is not None and lo is not None and atr) else 0.0
    range_scale = max(0.3, 1.0 - 0.5 * used)  # a day that has used its range has less left, never zero

    regime = ctx.regime.get("primary", "NORMAL")
    r_mult = (p.regime_mult or REGIME_MULT).get(regime, 0.8)
    t5 = ctx.trend("5m")
    with_trend = t5.get("label", "") in (("STRONG_BULL", "BULL", "WEAK_BULL") if cand.direction == "up" else ("STRONG_BEAR", "BEAR", "WEAK_BEAR"))
    t_mult = 1.15 if with_trend and t5.get("label", "").startswith("STRONG") else 1.0 if with_trend else 0.8
    if t5.get("exhaustion"):
        t_mult *= 0.7
        notes.append("trend exhaustion")
    h = ctx.indicators.get("macd_hist")
    m_mult = 1.0 if h is None else (1.05 if (h > 0) == (cand.direction == "up") else 0.85)

    remaining_atr = p.base_move_atr * time_scale * range_scale * r_mult * t_mult * m_mult
    # a level in the way caps the realistic move at that level (§23)
    ahead = ctx.nearest("above" if cand.direction == "up" else "below")
    if ahead and ahead.get("distance_atr") is not None and abs(ahead["distance_atr"]) < remaining_atr:
        # the first level caps it unless the move is a breakout THROUGH it: keep 60% credit beyond a weak level
        strong = ahead["name"] in ("pdh", "pdl", "pwh", "pwl", "session_high", "session_low")
        remaining_atr = abs(ahead["distance_atr"]) + (0.0 if strong else 0.4 * (remaining_atr - abs(ahead["distance_atr"])))
        notes.append(f"capped by {ahead['name']}")

    consumed = abs(ctx.spot - cand.invalidation) / atr if atr else 0.0  # from the setup's origin (SL side)
    behind = ctx.nearest("below" if cand.direction == "up" else "above")
    if behind and behind.get("distance_atr") is not None:
        consumed = min(consumed, abs(behind["distance_atr"]))
    return ExpectedMove(
        remaining_atr=round(remaining_atr, 3), remaining_points=round(remaining_atr * atr, 2), time_fraction=round(tf, 3),
        consumed_atr=round(consumed, 3), day_range_used=round(used, 3), regime_mult=r_mult, trend_mult=t_mult,
        momentum_mult=m_mult, boundary=round(ctx.spot + sign(cand.direction) * remaining_atr * atr, 2), notes=tuple(notes),
    )


# --- §22 no-chase, §23 distance-to-level ---------------------------------------------------------


@dataclass(frozen=True)
class ChaseVerdict:
    ok: bool
    reason_code: str | None = None
    detail: str = ""


def no_chase(ctx: ContextSnapshot, cand: Candidate, em: ExpectedMove, params: MoveParams | None = None) -> ChaseVerdict:
    p = params or MoveParams()
    atr = ctx.atr or 0.0
    if em.remaining_atr < p.min_remaining_atr:
        return ChaseVerdict(False, "insufficient_remaining_move", f"{em.remaining_atr:.2f} ATR < {p.min_remaining_atr}")
    if em.consumed_atr > p.max_consumed_atr:
        return ChaseVerdict(False, "extended_from_origin", f"{em.consumed_atr:.2f} ATR past origin")
    if em.consumed_atr > 0 and em.remaining_atr < 0.5 * em.consumed_atr:
        return ChaseVerdict(False, "move_mostly_done", f"remaining {em.remaining_atr:.2f} vs consumed {em.consumed_atr:.2f} ATR")
    vwap = ctx.indicators.get("vwap")
    if vwap is not None and atr and abs(ctx.spot - vwap) / atr > p.max_vwap_distance_atr:
        # only a chase when we are stretched AWAY from VWAP in the trade direction
        if (ctx.spot - vwap) * sign(cand.direction) > 0:
            return ChaseVerdict(False, "stretched_from_vwap", f"{abs(ctx.spot - vwap) / atr:.2f} ATR")
    if ctx.trend("5m").get("exhaustion") and em.remaining_atr < 1.0:
        return ChaseVerdict(False, "momentum_exhausted")
    if em.time_fraction <= 0.0:
        return ChaseVerdict(False, "no_time_remaining")
    return ChaseVerdict(True)


@dataclass(frozen=True)
class LevelCheck:
    ok: bool
    reason_code: str | None
    target_ref: float  # possibly downgraded to the blocking level
    distance_to_target_atr: float
    blocked_by: str | None = None


def distance_check(ctx: ContextSnapshot, cand: Candidate, em: ExpectedMove, params: MoveParams | None = None) -> LevelCheck:
    """§23: reject when the realistic target is immediately blocked; else
    downgrade the target to the nearest opposing level when that level
    is closer than the candidate's own target."""
    p = params or MoveParams()
    atr = ctx.atr or 0.0
    d_target = abs(cand.target_ref - ctx.spot) / atr if atr else 0.0
    ahead = ctx.nearest("above" if cand.direction == "up" else "below")
    if ahead and ahead.get("distance_atr") is not None:
        d_level = abs(ahead["distance_atr"])
        if d_level < p.blocked_level_atr and ahead["price"] != cand.target_ref:
            return LevelCheck(False, "target_blocked_by_level", cand.target_ref, d_target, ahead["name"])
        if d_level < d_target and ahead["price"] != cand.target_ref:
            return LevelCheck(True, None, float(ahead["price"]), d_level, ahead["name"])
    if em.remaining_atr < d_target * 0.5:
        return LevelCheck(True, None, em.boundary, em.remaining_atr, "expected_move_boundary")
    return LevelCheck(True, None, cand.target_ref, d_target, None)


# --- §21 option response ----------------------------------------------------------------------


def option_response(underlying_move: float, premium: float, delta: float, gamma: float | None,
                    theta_per_day: float | None = None, hold_fraction_of_day: float = 0.25) -> float:
    """Projected premium change for a long option over `underlying_move`
    (signed toward the trade: positive = favourable). Second-order in
    gamma, minus the theta bleed over the expected hold. Never linear."""
    d = abs(delta)
    g = abs(gamma or 0.0)
    move = abs(underlying_move)
    gain = d * move + 0.5 * g * move * move
    bleed = abs(theta_per_day or 0.0) * hold_fraction_of_day
    change = gain - bleed
    return max(-premium, change)  # a long option cannot lose more than its premium
