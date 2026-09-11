"""Structured, queryable experiment ledger (spec: an autonomous daily
research engine - observation -> hypothesis -> test -> validate ->
promote-only-if-justified). Tonight's actual research
(models/EXPERIMENTS.md, backtesting/BACKTESTS.md) was done by hand, in
prose - this gives it a durable, machine-readable form so a FUTURE
automated loop has real history to check against (never blindly repeat
an already-tried hypothesis) and ties to research/promotion_gate.py's
OBJECTIVE promotion criteria, not just written discipline.

This is the structural backbone for Phase 17/18, not a claim that
anything here is actually autonomous yet - hypothesis generation is
still done by a human (or Claude, working with a human) reading the
data, not by this code. The prose logs (EXPERIMENTS.md, BACKTESTS.md)
remain the primary, detailed record for a human reader; this is the
machine-readable index of the same history.
"""
import dataclasses
import json
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "experiment_log.jsonl"

VALID_VERDICTS = ("PROMOTED", "REJECTED", "INCONCLUSIVE")


@dataclasses.dataclass
class Experiment:
    experiment_id: str
    date: str  # ISO date, e.g. "2026-09-11"
    subject: str  # e.g. "models/regime_classifier" or "strategies/portfolio"
    hypothesis: str
    method: str
    metrics: dict
    verdict: str  # one of VALID_VERDICTS
    notes: str = ""
    prose_reference: str = ""  # e.g. "models/EXPERIMENTS.md#experiment-002" - the detailed human-readable record

    def __post_init__(self) -> None:
        if self.verdict not in VALID_VERDICTS:
            raise ValueError(f"verdict must be one of {VALID_VERDICTS}, got {self.verdict!r}")


def append_experiment(experiment: Experiment, log_path: Path = LOG_PATH) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(dataclasses.asdict(experiment)) + "\n")


def load_experiments(log_path: Path = LOG_PATH) -> list[Experiment]:
    if not log_path.exists():
        return []
    experiments = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                experiments.append(Experiment(**json.loads(line)))
    return experiments


def has_been_tried(subject: str, hypothesis: str, log_path: Path = LOG_PATH) -> bool:
    """Exact (subject, hypothesis) match only - a real system would need
    fuzzier matching to catch near-duplicate hypotheses phrased
    differently, out of scope for this first pass."""
    return any(e.subject == subject and e.hypothesis == hypothesis for e in load_experiments(log_path))


def promoted_experiments(subject: str, log_path: Path = LOG_PATH) -> list[Experiment]:
    return [e for e in load_experiments(log_path) if e.subject == subject and e.verdict == "PROMOTED"]
