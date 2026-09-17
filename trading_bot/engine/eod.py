"""End-of-day summary (spec §69/§70, M1 subset): what the engine watched,
what it would have called a signal, and how the data held up. Reads the
DAL only; pure formatting otherwise."""
import datetime as dt
from collections import Counter


def eod_stats(dal, run_id) -> dict:
    sigs = dal.signals_for_run(run_id)
    events = dal.presignal_events_for_run(run_id)
    by_stage = Counter(e["to_stage"] for e in events)
    by_underlying = Counter(s["underlying"] for s in sigs)
    by_direction = Counter(s["direction"] for s in sigs)
    regimes = Counter()
    for s in sigs:
        regimes[(s.get("explanation") or {}).get("Regime", "n/a").split(" ")[0]] += 1
    return {
        "context_snapshots": dal.context_count(run_id),
        "presignal_events": len(events),
        "stage_counts": dict(sorted(by_stage.items())),
        "would_be_signals": len(sigs),
        "signals_by_underlying": dict(sorted(by_underlying.items())),
        "signals_by_direction": dict(sorted(by_direction.items())),
        "signals_by_regime": dict(sorted(regimes.items())),
        "setups_opened": sum(1 for e in events if e["to_stage"] == "EARLY_DEVELOPMENT"),
        "setups_expired": by_stage.get("EXPIRED", 0),
        "setups_rejected": by_stage.get("REJECTED", 0) + by_stage.get("EXTENDED", 0) + by_stage.get("EXHAUSTED", 0),
    }


def eod_summary(dal, run_id, day: dt.date, mode: str, feed: dict | None = None) -> str:
    s = eod_stats(dal, run_id)
    lines = [
        f"EOD {day.isoformat()} | {mode} | zero orders",
        f"snapshots={s['context_snapshots']} presignal_events={s['presignal_events']} would_be_signals={s['would_be_signals']}",
        f"setups opened={s['setups_opened']} expired={s['setups_expired']} rejected/extended/exhausted={s['setups_rejected']}",
    ]
    if s["stage_counts"]:
        lines.append("stages: " + ", ".join(f"{k}={v}" for k, v in s["stage_counts"].items()))
    if s["would_be_signals"]:
        lines.append("by underlying: " + ", ".join(f"{k}={v}" for k, v in s["signals_by_underlying"].items()))
        lines.append("by direction: " + ", ".join(f"{k}={v}" for k, v in s["signals_by_direction"].items()))
        lines.append("by regime: " + ", ".join(f"{k}={v}" for k, v in s["signals_by_regime"].items()))
    if feed:
        lines.append("feed: " + ", ".join(f"{k}={v}" for k, v in feed.items()))
    return "\n".join(lines)
