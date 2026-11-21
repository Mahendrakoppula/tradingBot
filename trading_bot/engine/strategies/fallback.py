"""PRESIGNAL_CONFIRMED v0.1 - the routing fallback.

Why it exists (measured, not assumed): a year of real NIFTY 1m bars
(2023-04 .. 2024-03, research/funnel_diagnostic.py) replayed through the
production pipeline produced 1,374 TRADE_READY setups and FIVE approvals -
none of them executable trades. 57% of every TRADE_READY died at
STRATEGY_ROUTING with `no_strategy_match`, for two structural reasons:

1. Regime coverage. HIGH_VOLATILITY, COMPRESSION, RANGE, TRANSITION and
   CHOPPY are ~70% of the regime labels at decision time, and most of the
   ten families exclude every one of them (RANGE_EXTREME_REVERSAL alone
   collected 1,045 regime_incompatible verdicts).
2. Confirmation counted twice, one bar apart. The pre-signal tracker
   already demands a location (level_approach), >= 4 pieces of evidence, a
   trigger (structure break / sweep-reclaim / displacement / rejection
   candle) AND a following close that holds beyond the trigger level. The
   families then re-check their OWN trigger on that following bar - a
   displacement, a pin bar, a close in direction - so a setup that the
   tracker confirmed on bar N is asked to confirm again on bar N+1
   (MOMENTUM_EXPANSION:no_displacement 540, LIQUIDITY_SWEEP_BOS:
   no_structure_break_after_sweep 1,010, LEVEL_REJECTION:
   no_close_in_direction 358).

So when no family matches, the tracker's own confirmation is taken as the
setup: entry at the confirming close, invalidation just beyond the trigger
level (the same line the tracker uses to call the thesis invalidated), and
the ordinary market-driven target. Everything after routing still applies
unchanged - score/rank, no-chase, distance-to-level, option selection,
decay, the stop plan, sizing, expected value and every account limit.

Lowest tier: a specific family that matches always wins the slot. NEVER
trades in NO_TRADE/UNSTABLE or on bad data (route() gates those before any
family runs) and respects the configured counter-trend veto exactly like
the families do. TECH_PRESIGNAL_FALLBACK=false turns it off.
"""
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.strategies.base import Candidate, StrategyParams, default_target, sign
from trading_bot.engine.strategies.fingerprint import fingerprint

NAME = "PRESIGNAL_CONFIRMED"
VERSION = "0.1"
TIER = 4


def presignal_candidate(ctx: ContextSnapshot, direction: str, *, trigger_level: float | None, trigger_kind: str | None,
                        counter_trend: bool, counter_trend_cleared: bool,
                        params: StrategyParams | None = None) -> Candidate | None:
    p = params or StrategyParams()
    atr = ctx.atr or 0.0
    if atr <= 0:
        return None
    if counter_trend and not counter_trend_cleared:
        return None
    s = sign(direction)
    level = trigger_level
    if level is None or (ctx.spot - level) * s < 0:
        # no usable trigger level: anchor on the last swing behind price instead
        swing = ctx.structure.get("swing_low" if direction == "up" else "swing_high")
        if swing is None or (ctx.spot - swing) * s <= 0:
            return None
        level = float(swing)
    invalidation = level - s * p.sl_buffer_atr * atr
    target = default_target(ctx, direction, p)
    if (target - ctx.spot) * s <= 0:
        return None
    kind = trigger_kind or "trigger"
    cand = Candidate(
        strategy=NAME, version=VERSION, underlying=ctx.underlying, direction=direction, entry_ref=ctx.spot,
        invalidation=round(invalidation, 2), target_ref=round(target, 2), confirmation=f"presignal_{kind}_held",
        counter_trend=counter_trend,
        reasons=[f"pre-signal {kind} at {level:.2f} confirmed by the next close", "no specific family matched"],
        evidence={"trigger_level": level, "trigger_kind": kind},
    )
    cand.evidence["fingerprint"] = fingerprint(ctx, cand)
    return cand
