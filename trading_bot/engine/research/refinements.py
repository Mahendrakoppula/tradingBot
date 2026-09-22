"""Candidate-refinement tracking (spec §71, §72, §83).

A candidate is a hypothesis about a rule that may be too strict or too
loose, written down in docs/ROADMAP.md when first observed. This module
COUNTS how often the situation recurs in the journal and what the
underlying did afterwards, so the nightly review can say "R1: 12
occurrences, 8 target-first - ready to build as v0.2 shadow". That line
is a trigger for a HUMAN decision (build the new version, shadow it,
promote it with a §83 record). Nothing here changes engine behaviour and
nothing counts toward any automatic switch - §92 #43.

Each candidate is a predicate over a signal row (the journal's `signals`
table) plus a readiness rule. Counterfactuals are underlying-only (§92 #50).
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Callable

from trading_bot.engine.research.review import Counterfactual, counterfactuals


@dataclass(frozen=True)
class Candidate:
    code: str
    title: str
    matches: Callable[[dict], bool]  # signal row -> is this the observed situation?
    min_occurrences: int = 10
    min_target_first_share: float = 0.55  # counterfactual bar to call it "worth building"
    note: str = ""
    include_valid: bool = False  # shadow-strategy signals are `valid` rows that were never executed
    ready_verdict: str = "READY to build as a shadow-only vNext for human review (not applied)"


@dataclass
class CandidateStatus:
    code: str
    title: str
    occurrences: int
    by_underlying: dict = field(default_factory=dict)
    counterfactual: dict = field(default_factory=dict)  # outcome -> count
    target_first_share: float | None = None
    avg_max_favourable: float | None = None
    avg_max_adverse: float | None = None
    ready: bool = False
    verdict: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)

    def line(self) -> str:
        cf = ", ".join(f"{k}={v}" for k, v in sorted(self.counterfactual.items())) or "-"
        share = f"{self.target_first_share:.0%}" if self.target_first_share is not None else "n/a"
        return f"{self.code}: {self.occurrences} occurrence(s) [{cf}] target-first {share} - {self.verdict}"


def _routing(row: dict) -> dict:
    return (row.get("snapshot") or {}).get("routing") or {}


def _swept(row: dict) -> bool:
    """A liquidity sweep was on the trigger bar: journaled in the snapshot since
    2026-09-23, before that only in the explanation's Structure line."""
    snap = row.get("snapshot") or {}
    if snap.get("sweep"):
        return True
    return "sweep of" in str((row.get("explanation") or {}).get("Structure") or "")


def _r1(row: dict) -> bool:
    """Failed sweep + close back through the level, declined for want of a
    further confirmation: PDH_PDL_TRAP wanting a candle pattern / swing BOS
    (2026-09-21 NIFTY 10:10), or LIQUIDITY_SWEEP_BOS wanting the structure
    break that only prints after the move (2026-09-22 BANKNIFTY 14:10: swept
    the session low, closed back inside, +100 pts before the BOS)."""
    routing = _routing(row)
    if routing.get("PDH_PDL_TRAP") == "trap_without_confirmation" or (
            row.get("strategy") == "PDH_PDL_TRAP" and row.get("reason_code") == "trap_without_confirmation"):
        return True
    return routing.get("LIQUIDITY_SWEEP_BOS") == "no_structure_break_after_sweep" and _swept(row)


def _r2(row: dict) -> bool:
    """Counter-trend evidence bar / no-chase floor as the binding constraint."""
    return "counter_trend_evidence_insufficient" in _routing(row).values() or (
        row.get("stage") == "NO_CHASE" and row.get("reason_code") == "insufficient_remaining_move")


def _r3(row: dict) -> bool:
    """Counter-trend veto came ONLY from the daily: the 30m agreed with the
    trade (|score| >= 0.5 in its direction) while the daily opposed it."""
    if "counter_trend_not_allowed" not in _routing(row).values():
        return False
    sc = (row.get("snapshot") or {}).get("trend_scores") or {}
    d = row.get("direction")
    s30, s1d = sc.get("30m"), sc.get("1d")
    if s30 is None or s1d is None:
        return False
    with_30m = abs(s30) >= 0.5 and (s30 > 0) == (d == "up")
    against_1d = abs(s1d) >= 0.5 and (s1d > 0) != (d == "up")
    return with_30m and against_1d


_LATE_REASONS = {"extended_from_origin", "move_mostly_done", "extended_from_breakout_level", "extended_from_range"}


def _r4(row: dict) -> bool:
    """Confirmation cadence: the setup was sound but by the time the 5m close
    confirmed it the move had already run - rejected as extended / mostly
    done. A 1m-driven confirmation leg would have seen it sooner."""
    if row.get("stage") == "NO_CHASE" and row.get("reason_code") in _LATE_REASONS:
        return True
    return any(v in _LATE_REASONS for v in _routing(row).values())


def _r5(row: dict) -> bool:
    """LEVEL_REJECTION v0.1 running as a shadow strategy: valid signals that
    were never executed (reason shadow_only_strategy). Its counterfactuals
    and the risk verdicts it collected are the promotion evidence."""
    return row.get("strategy") == "LEVEL_REJECTION" and row.get("status") == "valid"


CANDIDATES: tuple[Candidate, ...] = (
    Candidate("R1", "failed sweep + close back inside as confirmation (trap / sweep-BOS families)", _r1, note="docs/ROADMAP.md R1"),
    Candidate("R2", "counter-trend 6-key bar / 0.75 ATR no-chase floor binding", _r2, min_occurrences=20, note="docs/ROADMAP.md R2"),
    Candidate("R3", "daily-only veto blocked a 30m-aligned trade (measures the pre-2026-09-21 rule)", _r3, note="docs/ROADMAP.md R3"),
    Candidate("R4", "late to a confirmed move: 5m-close confirmation cadence (1m confirmation leg)", _r4, note="docs/ROADMAP.md R4"),
    Candidate("R5", "LEVEL_REJECTION v0.1 shadow: failed break of a key level, no pattern needed", _r5, include_valid=True,
              note="docs/ROADMAP.md R5", ready_verdict="READY for promotion out of shadow (human decision + section 83 record)"),
)


def track(signals: list[dict], candles_1m, candidates: tuple[Candidate, ...] = CANDIDATES,
          horizon_bars: int = 60) -> list[CandidateStatus]:
    out: list[CandidateStatus] = []
    for c in candidates:
        hits = [s for s in signals if (c.include_valid or s.get("status") != "valid") and c.matches(s)]
        st = CandidateStatus(c.code, c.title, len(hits))
        for s in hits:
            st.by_underlying[s.get("underlying", "?")] = st.by_underlying.get(s.get("underlying", "?"), 0) + 1
        if hits:
            cfs: list[Counterfactual] = counterfactuals(hits, candles_1m, horizon_bars=horizon_bars, include_valid=c.include_valid)
            for cf in cfs:
                st.counterfactual[cf.outcome] = st.counterfactual.get(cf.outcome, 0) + 1
            decided = [cf for cf in cfs if cf.outcome in ("target_first", "stop_first")]
            if decided:
                st.target_first_share = round(sum(1 for cf in decided if cf.outcome == "target_first") / len(decided), 3)
                st.avg_max_favourable = round(sum(cf.max_favourable for cf in decided) / len(decided), 2)
                st.avg_max_adverse = round(sum(cf.max_adverse for cf in decided) / len(decided), 2)
        st.ready = st.occurrences >= c.min_occurrences and (st.target_first_share or 0.0) >= c.min_target_first_share
        if st.ready:
            st.verdict = c.ready_verdict
        elif st.occurrences >= c.min_occurrences:
            st.verdict = f"recurring but counterfactuals unfavourable (need >= {c.min_target_first_share:.0%} target-first)"
        else:
            st.verdict = f"watching (need {c.min_occurrences} occurrences)"
        out.append(st)
    return out


def render(statuses: list[CandidateStatus]) -> str:
    lines = ["CANDIDATE REFINEMENTS (tracked, never auto-applied - spec sections 71/83/92#43)"]
    lines += [s.line() for s in statuses]
    return "\n".join(lines)
