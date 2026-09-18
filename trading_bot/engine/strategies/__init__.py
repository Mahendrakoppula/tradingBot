"""Strategy routing (spec §15): regime + trend first, then the compatible
families, evaluated in tier order. Returns every Candidate the families
produced (scoring/ranking picks between them, §16) plus every NoTrade so
the rejected-signal dataset (§72) sees why each family passed.

Hard gates applied here before any family runs:
- NO_TRADE / UNSTABLE regimes: nothing is evaluated (§4 priority labels).
- A family only runs in a regime it declares compatible.
- A family only runs against the alignment preference if it declares
  counter_trend_ok, and then only when the pre-signal tracker already met
  its counter-trend evidence bar (§7 "substantially stronger evidence").
"""
from dataclasses import dataclass, field

from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.strategies.base import (
    NEVER_TRADE_REGIMES,
    Candidate,
    NoTrade,
    Strategy,
    StrategyParams,
    StrategySpec,
    is_counter_trend,
)
from trading_bot.engine.strategies.families import FAMILIES
from trading_bot.engine.strategies.fingerprint import fingerprint

__all__ = ["Candidate", "NoTrade", "Strategy", "StrategyParams", "StrategySpec", "RoutingResult", "route",
           "FAMILIES", "fingerprint"]


@dataclass
class RoutingResult:
    candidates: list[Candidate] = field(default_factory=list)
    rejections: list[NoTrade] = field(default_factory=list)
    gate: str | None = None  # a routing-level reason when NO family was evaluated

    @property
    def best_tier(self) -> int | None:
        tiers = [f.spec.tier for f in FAMILIES if any(c.strategy == f.spec.name for c in self.candidates)]
        return min(tiers) if tiers else None


def compatible(spec: StrategySpec, ctx: ContextSnapshot, direction: str, counter_trend_cleared: bool,
               family_hint: str | None) -> str | None:
    """None when the family may run; otherwise the reason it may not."""
    regime = ctx.regime.get("primary")
    if regime not in spec.compatible_regimes:
        return "regime_incompatible"
    label = ctx.alignment.get("label")
    if is_counter_trend(ctx, direction):
        if not spec.counter_trend_ok:
            return "counter_trend_not_allowed"
        if not counter_trend_cleared:
            return "counter_trend_evidence_insufficient"
    elif label not in spec.compatible_alignments and label != "COUNTER_TREND":
        return "alignment_incompatible"
    if family_hint and spec.family_hints and family_hint not in spec.family_hints and "unclassified" not in spec.family_hints:
        return "family_hint_mismatch"
    return None


def route(ctx: ContextSnapshot, direction: str, *, params: StrategyParams | None = None,
          counter_trend_cleared: bool = False, family_hint: str | None = None,
          families: tuple = FAMILIES) -> RoutingResult:
    params = params or StrategyParams()
    out = RoutingResult()
    regime = ctx.regime.get("primary")
    if regime in NEVER_TRADE_REGIMES or regime is None:
        out.gate = f"regime_{(regime or 'unknown').lower()}"
        return out
    if ctx.quality != "OK":
        out.gate = f"data_quality_{ctx.quality.lower()}"
        return out
    for fam in sorted(families, key=lambda f: f.spec.tier):
        why = compatible(fam.spec, ctx, direction, counter_trend_cleared, family_hint)
        if why is not None:
            out.rejections.append(NoTrade(fam.spec.name, why))
            continue
        result = fam.evaluate(ctx, direction, params)
        if isinstance(result, Candidate):
            if result.risk_distance <= 0 or result.reward_distance <= 0:
                out.rejections.append(NoTrade(fam.spec.name, "degenerate_levels"))
                continue
            result.evidence.setdefault("fingerprint", fingerprint(ctx, result))
            out.candidates.append(result)
        else:
            out.rejections.append(result)
    return out
