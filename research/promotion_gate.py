"""Objective promotion criteria (spec: promote only if justified against
a real baseline) encoded as code, not just written discipline - so a
future automated research loop can't accidentally promote something
that doesn't clear the bar, and so the bar itself is inspectable and
testable rather than a matter of judgment applied inconsistently
between experiments.
"""
from dataclasses import dataclass


@dataclass
class PromotionCriteria:
    min_fraction_folds_beating_baseline: float = 0.6
    min_n_folds: int = 5


@dataclass
class PromotionDecision:
    promote: bool
    reason: str


def evaluate_promotion(
    n_folds: int,
    n_folds_beating_baseline: int,
    criteria: PromotionCriteria | None = None,
) -> PromotionDecision:
    criteria = criteria or PromotionCriteria()

    if n_folds < criteria.min_n_folds:
        return PromotionDecision(
            promote=False,
            reason=f"Only {n_folds} fold(s) evaluated - need at least {criteria.min_n_folds} for a promotion decision",
        )

    fraction = n_folds_beating_baseline / n_folds
    if fraction < criteria.min_fraction_folds_beating_baseline:
        return PromotionDecision(
            promote=False,
            reason=(
                f"Beat baseline in {n_folds_beating_baseline}/{n_folds} folds ({fraction:.0%}) - "
                f"below the {criteria.min_fraction_folds_beating_baseline:.0%} threshold to promote"
            ),
        )
    return PromotionDecision(
        promote=True,
        reason=f"Beat baseline in {n_folds_beating_baseline}/{n_folds} folds ({fraction:.0%}) - meets promotion threshold",
    )
