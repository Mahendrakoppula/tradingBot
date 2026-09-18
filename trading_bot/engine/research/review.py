"""Daily review (spec §71), rejected-signal analysis with SEPARATED
counterfactuals (§72), and the strategy promotion record (§83).

"Never change the strategy because of one trade or one day" (§71) - this
module produces evidence, it never edits config. Counterfactual outcomes
are computed on the UNDERLYING only (would price have reached the target
before the stop?), from stored candles AFTER the signal, and are returned
in their own structure so they can never be mixed into actual P&L
(§92 #50).
"""
import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from trading_bot.engine.research.metrics import Metrics, compute, segmented
from trading_bot.engine.research.validation import execution_parity

# --- §71 daily review --------------------------------------------------------------------------


@dataclass
class Review:
    period: str
    overall: Metrics
    best: dict = field(default_factory=dict)  # key -> (value, expectancy, trades)
    worst: dict = field(default_factory=dict)
    sl_quality: dict = field(default_factory=dict)
    target_quality: dict = field(default_factory=dict)
    execution: dict = field(default_factory=dict)
    costs: dict = field(default_factory=dict)
    rejected: dict = field(default_factory=dict)
    caution: str = "Never change the strategy because of one trade or one day (spec section 71)."

    def render(self) -> str:
        from trading_bot.engine.research.metrics import render
        lines = [f"REVIEW {self.period}", render(self.overall)]
        for key in self.best:
            b, w = self.best[key], self.worst[key]
            lines.append(f"{key}: best {b[0]} ({b[1]:+.0f}/trade, n={b[2]}) | worst {w[0]} ({w[1]:+.0f}/trade, n={w[2]})")
        if self.sl_quality:
            lines.append("SL quality: " + ", ".join(f"{k}={v}" for k, v in self.sl_quality.items()))
        if self.target_quality:
            lines.append("target quality: " + ", ".join(f"{k}={v}" for k, v in self.target_quality.items()))
        if self.execution:
            lines.append("execution: " + ", ".join(f"{k}={v}" for k, v in self.execution.items()))
        if self.costs:
            lines.append("costs: " + ", ".join(f"{k}={v}" for k, v in self.costs.items()))
        if self.rejected:
            lines.append("rejected signals: " + ", ".join(f"{k}={v}" for k, v in self.rejected.get("by_stage", {}).items()))
        lines.append(self.caution)
        return "\n".join(lines)


_REVIEW_KEYS = ("strategy", "trend", "regime", "underlying", "option_type", "time_of_day", "fingerprint", "strike", "expiry")


def daily_review(trade_rows: list[dict], signals: list[dict], executions: list[dict], capital: float, period: str,
                 min_group: int = 1) -> Review:
    r = Review(period, compute(trade_rows, capital))
    seg = segmented(trade_rows, capital, _REVIEW_KEYS)
    for key, groups in seg.items():
        items = [(v, m.expectancy, m.trades) for v, m in groups.items() if m.trades >= min_group]
        if not items:
            continue
        r.best[key] = max(items, key=lambda t: t[1])
        r.worst[key] = min(items, key=lambda t: t[1])
    # SL / target quality from MFE/MAE vs plan (details carry initial_sl/target/entry_price/quantity)
    stopped = [t for t in trade_rows if (t.get("exit_reason") or (t.get("details") or {}).get("exit_reason")) == "STOP_LOSS"]
    if trade_rows:
        near_miss = 0
        for t in stopped:
            d = t.get("details") or {}
            qty = int(t.get("quantity") or d.get("quantity") or 0)
            entry, sl = float(d.get("entry_price") or t.get("entry_price") or 0), float(d.get("initial_sl") or 0)
            if qty and entry > sl and float(t.get("mfe") or 0) > 0.5 * (entry - sl) * qty:
                near_miss += 1  # was well in profit before being stopped: exit/trail quality question
        r.sl_quality = {"stopped": len(stopped), "stopped_after_being_in_profit": near_miss,
                        "avg_mae": round(sum(float(t.get("mae") or 0) for t in trade_rows) / len(trade_rows), 0)}
        reached = sum(1 for t in trade_rows if (t.get("exit_reason") or (t.get("details") or {}).get("exit_reason")) in ("TARGET_1", "TARGET_2", "EXTENDED_TARGET"))
        left_on_table = 0
        for t in trade_rows:
            mfe, net = float(t.get("mfe") or 0), float(t.get("net_pnl") or 0)
            if mfe > 0 and net < 0.5 * mfe:
                left_on_table += 1
        r.target_quality = {"target_hits": reached, "closed_below_half_mfe": left_on_table,
                            "avg_mfe": round(sum(float(t.get("mfe") or 0) for t in trade_rows) / len(trade_rows), 0)}
        r.costs = {"total": r.overall.costs, "per_trade": round(r.overall.costs / len(trade_rows), 0),
                   "share_of_gross_wins": round(r.overall.costs / max(1.0, sum(float(t.get("gross_pnl") or 0) for t in trade_rows if float(t.get("gross_pnl") or 0) > 0)), 3)}
    p = execution_parity(executions, signals)
    if p.orders:
        r.execution = {"orders": p.orders, "fill_probability": p.fill_probability, "slippage_vs_theoretical": p.slippage_vs_theoretical_mean,
                       "latency_p95_ms": p.latency_ms_p95, "partials": p.partial_fills, "rejected": p.rejected, "timeouts": p.timeouts}
    r.rejected = rejected_analysis(signals)
    return r


# --- §72 rejected-signal analysis --------------------------------------------------------------


def rejected_analysis(signals: list[dict]) -> dict:
    """Counts by status, by pipeline stage, by reason, by (underlying,
    direction) and counter-trend share - from the signals journal alone."""
    rej = [s for s in signals if s.get("status") != "valid"]
    by_status = Counter(s.get("status") for s in rej)
    by_stage = Counter(str(s.get("stage") or "?") for s in rej)
    by_reason = Counter(f"{s.get('stage')}:{s.get('reason_code')}" for s in rej)
    by_side = Counter(f"{s.get('underlying')}:{s.get('direction')}" for s in rej)
    counter_trend = sum(1 for s in rej if "counter-trend" in str((s.get("explanation") or {}).get("Strategy", "")))
    return {"total": len(rej), "valid": len(signals) - len(rej), "by_status": dict(sorted(by_status.items())),
            "by_stage": dict(sorted(by_stage.items())), "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])[:15]),
            "by_side": dict(sorted(by_side.items())), "counter_trend": counter_trend}


@dataclass
class Counterfactual:
    signal_id: str
    underlying: str
    direction: str
    ts: dt.datetime
    spot: float
    target_ref: float | None
    stop_ref: float | None
    outcome: str  # "target_first" | "stop_first" | "neither" | "unknown"
    max_favourable: float
    max_adverse: float
    bars: int

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["ts"] = self.ts.isoformat()
        return d


def counterfactuals(signals: list[dict], candles_1m, *, horizon_bars: int = 60, atr_target: float = 1.5,
                    atr_stop: float = 1.0) -> list[Counterfactual]:
    """UNDERLYING-only counterfactuals for rejected signals: from the
    signal bar forward, did price reach the target reference before the stop
    reference? Uses the snapshot's target/stop when the pipeline got that
    far, else +/- ATR multiples. `candles_1m(underlying) -> list[dict]`
    sorted by ts. Never joined to actual P&L (§92 #50)."""
    out: list[Counterfactual] = []
    for s in signals:
        if s.get("status") == "valid":
            continue
        snap = s.get("snapshot") or {}
        ts = s["ts"] if isinstance(s["ts"], dt.datetime) else dt.datetime.fromisoformat(s["ts"])
        spot = float(snap.get("spot") or snap.get("underlying_price") or 0)
        if spot <= 0:
            continue
        d = s.get("direction")
        sgn = 1 if d == "up" else -1
        atr = float(snap.get("atr") or 0) or None
        target = snap.get("target1_ref") or (spot + sgn * atr_target * atr if atr else None)
        stop = snap.get("stop_ref") or (spot - sgn * atr_stop * atr if atr else None)
        bars = [c for c in candles_1m(s["underlying"]) if c["ts"] > ts][:horizon_bars]
        if not bars or target is None or stop is None:
            out.append(Counterfactual(s["signal_id"], s["underlying"], d, ts, spot, target, stop, "unknown", 0.0, 0.0, len(bars)))
            continue
        outcome, mf, ma = "neither", 0.0, 0.0
        for c in bars:
            fav = (c["high"] - spot) * sgn if d == "up" else (spot - c["low"])
            adv = (spot - c["low"]) if d == "up" else (c["high"] - spot)
            mf, ma = max(mf, fav), max(ma, adv)
            hit_t = (c["high"] >= target) if d == "up" else (c["low"] <= target)
            hit_s = (c["low"] <= stop) if d == "up" else (c["high"] >= stop)
            if hit_t and hit_s:
                outcome = "stop_first"  # same bar: assume the worse ordering
                break
            if hit_s:
                outcome = "stop_first"
                break
            if hit_t:
                outcome = "target_first"
                break
        out.append(Counterfactual(s["signal_id"], s["underlying"], d, ts, spot, float(target), float(stop), outcome,
                                  round(mf, 2), round(ma, 2), len(bars)))
    return out


def counterfactual_summary(cfs: list[Counterfactual]) -> dict:
    by_stage_outcome: dict = defaultdict(Counter)
    total = Counter(c.outcome for c in cfs)
    return {"total": len(cfs), "outcomes": dict(sorted(total.items())),
            "target_first_share": round(total.get("target_first", 0) / len(cfs), 3) if cfs else 0.0,
            "note": "underlying-only, no option pricing, no costs - never comparable to actual P&L"}


# --- §83 promotion record ------------------------------------------------------------------------


def promotion_record(strategy: str, version: str, *, backtest: Metrics | None, walk_forward: dict | None,
                     monte_carlo: dict | None, paper: Metrics | None, gates: list | None, limitations: list[str],
                     rollback_version: str | None, decided_by: str, decision: str) -> dict:
    """The validation record a production strategy must carry (§83)."""
    return {
        "strategy": strategy, "version": version, "decision": decision, "decided_by": decided_by,
        "decided_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "backtest": backtest.as_dict() if backtest else None, "walk_forward": walk_forward, "monte_carlo": monte_carlo,
        "paper": paper.as_dict() if paper else None,
        "gates": [{"name": g.name, "passed": g.passed, "checks": g.checks} for g in gates] if gates else None,
        "limitations": limitations, "rollback_version": rollback_version,
    }


def promote(dal, record: dict) -> int:
    return dal.insert_strategy_version(record["strategy"], record["version"], record)
