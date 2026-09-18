"""Rejected-signal dataset (spec §72): every setup that did NOT become a
trade is recorded with WHERE in the pipeline it stopped and WHY, so the
research side can study what was passed on, not only what was taken.

Stage vocabulary follows the §93 pipeline order. `status` maps onto the
`signals.status` CHECK constraint (rejected / expired / invalidated /
risk_rejected / option_rejected). Counterfactual outcomes (§72 "calculate
separately") are NOT computed here - M3 does that offline from the
journal, and they never mix with actual P&L (§92 #50).
"""
from dataclasses import dataclass, field

STAGES: tuple[str, ...] = (
    "PRE_SIGNAL", "STRATEGY_ROUTING", "SCORING", "RANKING", "EXPECTED_MOVE", "NO_CHASE", "DISTANCE_TO_LEVEL",
    "OPTION_SELECTION", "DECAY_FILTER", "STOP_PLAN", "RISK_ENGINE", "SNAPSHOT", "EXECUTION_GATE", "ORDER",
)

_STATUS = {
    "PRE_SIGNAL": "expired", "STRATEGY_ROUTING": "rejected", "SCORING": "rejected", "RANKING": "rejected",
    "EXPECTED_MOVE": "rejected", "NO_CHASE": "rejected", "DISTANCE_TO_LEVEL": "rejected",
    "OPTION_SELECTION": "option_rejected", "DECAY_FILTER": "option_rejected", "STOP_PLAN": "rejected",
    "RISK_ENGINE": "risk_rejected", "SNAPSHOT": "invalidated", "EXECUTION_GATE": "invalidated", "ORDER": "invalidated",
}


@dataclass
class Rejection:
    signal_id: str
    setup_id: str | None
    underlying: str
    direction: str
    stage: str
    reason_code: str
    detail: str = ""
    strategy: str | None = None
    score: int | None = None
    counter_trend: bool = False
    details: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        return _STATUS.get(self.stage, "rejected")

    def as_dict(self) -> dict:
        return {"signal_id": self.signal_id, "setup_id": self.setup_id, "underlying": self.underlying,
                "direction": self.direction, "stage": self.stage, "reason_code": self.reason_code, "detail": self.detail,
                "strategy": self.strategy, "score": self.score, "counter_trend": self.counter_trend,
                "status": self.status, "details": self.details}


def summarize(rejections: list[Rejection]) -> dict:
    """Counts by stage and by (stage, reason) for the EOD report (§69 'rejected signals')."""
    by_stage: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for r in rejections:
        by_stage[r.stage] = by_stage.get(r.stage, 0) + 1
        key = f"{r.stage}:{r.reason_code}"
        by_reason[key] = by_reason.get(key, 0) + 1
    return {"total": len(rejections), "by_stage": dict(sorted(by_stage.items())),
            "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1]))}
