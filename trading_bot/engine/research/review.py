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
import re
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
    attribution: dict = field(default_factory=dict)
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
            if self.rejected.get("by_reason"):
                lines.append("rejection reasons: " + ", ".join(f"{k}={v}" for k, v in self.rejected["by_reason"].items()))
            if self.rejected.get("by_family"):
                lines.append("routing verdicts: " + ", ".join(f"{k}={v}" for k, v in self.rejected["by_family"].items()))
            ledger = self.rejected.get("ledger") or []
            if ledger:
                lines.append(f"rejection ledger ({len(ledger)}; 'next' = underlying-only counterfactual over "
                             f"{self.rejected.get('horizon_bars', 60)} bars, never P&L):")
                lines += ["  " + render_rejection(r) for r in ledger[:LEDGER_LINES]]
                if len(ledger) > LEDGER_LINES:
                    lines.append(f"  ... and {len(ledger) - LEDGER_LINES} more (review a shorter range for the full list)")
        if self.attribution:
            lines.append(f"component attribution (trades: expectancy/trade; rejections: underlying-only target-first; "
                         f"prune what shows no edge - needs n>={ATTRIBUTION_MIN_N} per side):")
            lines += ["  " + render_attribution_row(name, row) for name, row in self.attribution.items()]
        lines.append(self.caution)
        return "\n".join(lines)


_REVIEW_KEYS = ("strategy", "trend", "regime", "underlying", "option_type", "time_of_day", "fingerprint", "strike", "expiry")


def daily_review(trade_rows: list[dict], signals: list[dict], executions: list[dict], capital: float, period: str,
                 min_group: int = 1, candles_1m=None) -> Review:
    """`candles_1m(underlying) -> list[dict]` (optional) lets the rejection
    ledger say what the underlying did after each rejected signal (§72)."""
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
    r.rejected = rejected_analysis(signals, candles_1m)
    r.attribution = component_attribution(trade_rows, signals, r.rejected.get("ledger") or [])
    return r


# --- component attribution (indicator freeze 2026-09-22: inputs leave on evidence) ------------------

ATTRIBUTION_MIN_N = 20


def _score_parts(row: dict) -> tuple[dict, dict] | None:
    snap = row.get("snapshot") or {}
    comps = snap.get("score_components") or (snap.get("extra") or {}).get("score_components")
    if not comps:
        return None
    pens = snap.get("penalties") or (snap.get("extra") or {}).get("penalties") or {}
    return comps, pens


def component_attribution(trade_rows: list[dict], signals: list[dict], ledger: list[dict]) -> dict:
    """For every scoring component: outcomes when it scored >= half its cap
    ("strong") vs below ("weak"); for every penalty: applied vs not. Trades
    are joined to their signal by signal_id (net P&L expectancy); rejected
    signals use the ledger's underlying-only counterfactual (target-first
    share). This is how an input earns its place or leaves."""
    from trading_bot.engine.scoring import CAPS, PENALTIES
    by_id = {str(s.get("signal_id")): s for s in signals}
    out: dict = {}

    def bucket(name: str, side: str) -> dict:
        return out.setdefault(name, {}).setdefault(side, {"trades": 0, "net": 0.0, "cf_n": 0, "cf_target_first": 0})

    def observe(sig: dict, *, net: float | None = None, cf: str | None = None) -> None:
        parts = _score_parts(sig)
        if parts is None:
            return
        comps, pens = parts
        for comp, cap in CAPS.items():
            side = "strong" if comps.get(comp, 0) >= cap / 2 else "weak"
            b = bucket(comp, side)
            if net is not None:
                b["trades"] += 1
                b["net"] += net
            if cf in ("target_first", "stop_first"):
                b["cf_n"] += 1
                b["cf_target_first"] += cf == "target_first"
        for pen in PENALTIES:
            b = bucket("-" + pen, "applied" if pen in pens else "absent")
            if net is not None:
                b["trades"] += 1
                b["net"] += net
            if cf in ("target_first", "stop_first"):
                b["cf_n"] += 1
                b["cf_target_first"] += cf == "target_first"

    for t in trade_rows:
        sig = by_id.get(str(t.get("signal_id")))
        if sig is not None:
            observe(sig, net=float(t.get("net_pnl") or 0))
    for row in ledger:
        sig = by_id.get(str(row.get("signal_id")))
        if sig is not None and row.get("next"):
            observe(sig, cf=row["next"])
    return {k: v for k, v in out.items() if any(b["trades"] or b["cf_n"] for b in v.values())}


def render_attribution_row(name: str, row: dict) -> str:
    parts = []
    for side in ("strong", "weak", "applied", "absent"):
        b = row.get(side)
        if not b:
            continue
        bits = []
        if b["trades"]:
            bits.append(f"{b['trades']} trades {b['net'] / b['trades']:+.0f}/trade")
        if b["cf_n"]:
            bits.append(f"cf {b['cf_target_first'] / b['cf_n']:.0%} target-first (n={b['cf_n']})")
        parts.append(f"{side}: " + ", ".join(bits))
    return f"{name}: " + " | ".join(parts)


# --- §72 rejected-signal analysis --------------------------------------------------------------


LEDGER_LINES = 30  # per-signal lines in a rendered review (a day is ~10; longer ranges get counts + the first 30)
_TREND_TFS = ("1d", "30m", "5m")


def rejected_analysis(signals: list[dict], candles_1m=None, horizon_bars: int = 60) -> dict:
    """Counts by status, by pipeline stage, by reason, by routing family
    verdict, by (underlying, direction) and counter-trend share - plus a
    per-signal LEDGER: every rejected signal with the stage that stopped it,
    the reason, each family's verdict, the trend scores it was judged
    against and (with candles) what the underlying did next. The ledger is
    what the nightly review reads to see whether the same reason keeps
    recurring (§71/§72); the refinements tracker counts the named ones."""
    rej = [s for s in signals if s.get("status") != "valid"]
    by_status = Counter(s.get("status") for s in rej)
    by_stage = Counter(str(s.get("stage") or "?") for s in rej)
    by_reason = Counter(f"{s.get('stage')}:{s.get('reason_code')}" for s in rej)
    by_side = Counter(f"{s.get('underlying')}:{s.get('direction')}" for s in rej)
    by_family = Counter(f"{fam}:{why}" for s in rej for fam, why in ((s.get("snapshot") or {}).get("routing") or {}).items())
    counter_trend = sum(1 for s in rej if "counter-trend" in str((s.get("explanation") or {}).get("Strategy", "")))
    ledger = [_ledger_row(s) for s in rej]
    if candles_1m is not None and rej:
        by_id = {c.signal_id: c for c in counterfactuals(rej, candles_1m, horizon_bars=horizon_bars)}
        for row in ledger:
            c = by_id.get(row["signal_id"])
            if c is not None:
                row.update(next=c.outcome, max_favourable=c.max_favourable, max_adverse=c.max_adverse)
    return {"total": len(rej), "valid": len(signals) - len(rej), "by_status": dict(sorted(by_status.items())),
            "by_stage": dict(sorted(by_stage.items())), "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])[:15]),
            "by_family": dict(sorted(by_family.items(), key=lambda kv: -kv[1])[:20]),
            "by_side": dict(sorted(by_side.items())), "counter_trend": counter_trend,
            "ledger": ledger, "horizon_bars": horizon_bars}


_TREND_RE = re.compile(r"(1d|30m|5m|1m)=[A-Z_]+\((-?\d+(?:\.\d+)?)\)")


def _trend_scores(snap: dict, explanation: dict) -> dict:
    """Snapshot `trend_scores` (journaled since 2026-09-22); older rows only
    have the explanation's Trend line "1d=STRONG_BEAR(-0.71), 30m=..."."""
    scores = snap.get("trend_scores") or {}
    if not any(scores.get(tf) is not None for tf in _TREND_TFS):
        scores = {tf: float(v) for tf, v in _TREND_RE.findall(str(explanation.get("Trend") or ""))}
    return {tf: scores[tf] for tf in _TREND_TFS if scores.get(tf) is not None}


def _ledger_row(s: dict) -> dict:
    snap = s.get("snapshot") or {}
    expl = s.get("explanation") or {}
    ts = s.get("ts")
    if isinstance(ts, str):
        ts = dt.datetime.fromisoformat(ts)
    return {"signal_id": s.get("signal_id"), "ts": ts.isoformat() if ts else None, "time": ts.strftime("%H:%M") if ts else "?",
            "underlying": s.get("underlying"), "direction": s.get("direction"), "stage": s.get("stage"),
            "reason_code": s.get("reason_code"), "strategy": s.get("strategy"), "score": s.get("score"),
            "detail": snap.get("detail") or expl.get("Strategy"), "routing": dict(snap.get("routing") or {}),
            "trend_scores": _trend_scores(snap, expl),
            "vwap": snap.get("vwap") or {},
            "next": None, "max_favourable": None, "max_adverse": None}


def render_rejection(row: dict) -> str:
    """One ledger line: time side stage:reason [trend scores] family verdicts -> what happened next."""
    head = f"{row['time']} {row['underlying']} {row['direction']} {row['stage']}:{row['reason_code']}"
    if row.get("strategy"):
        head += f" {row['strategy']}"
    if row.get("score") is not None:
        head += f" s={float(row['score']):.0f}"
    parts = [head]
    if row.get("trend_scores"):
        parts.append("[" + " ".join(f"{tf} {float(v):+.1f}" for tf, v in row["trend_scores"].items()) + "]")
    vw = row.get("vwap") or {}
    if vw.get("distance_atr") is not None:
        parts.append(f"vwap {float(vw['distance_atr']):+.1f}atr" + (f"/{float(vw['slope_atr']):+.2f}" if vw.get("slope_atr") is not None else ""))
    if row.get("routing"):
        parts.append(" ".join(f"{fam}={why}" for fam, why in row["routing"].items()))
    elif row.get("detail"):
        parts.append(str(row["detail"])[:80])
    if row.get("next"):
        parts.append(f"-> {row['next']} mf={row['max_favourable']:+.0f} ma={-abs(row['max_adverse']):+.0f}")
    return " ".join(parts)


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
        all_bars = candles_1m(s["underlying"])
        atr = float(snap.get("atr") or 0) or _atr_5m_from_1m([c for c in all_bars if c["ts"] <= ts])
        target = snap.get("target1_ref") or (spot + sgn * atr_target * atr if atr else None)
        stop = snap.get("stop_ref") or (spot - sgn * atr_stop * atr if atr else None)
        bars = [c for c in all_bars if c["ts"] > ts][:horizon_bars]
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


def _atr_5m_from_1m(bars_1m: list[dict], periods: int = 14) -> float | None:
    """5m ATR(periods) rebuilt from 1m candles (for rows journaled without
    an ATR): bucket the 1m bars into 5-minute bars, mean true range."""
    buckets: dict = {}
    for c in bars_1m:
        key = c["ts"].replace(minute=c["ts"].minute - c["ts"].minute % 5, second=0, microsecond=0)
        b = buckets.setdefault(key, {"high": c["high"], "low": c["low"], "close": c["close"]})
        b["high"], b["low"], b["close"] = max(b["high"], c["high"]), min(b["low"], c["low"]), c["close"]
    five = [buckets[k] for k in sorted(buckets)][-(periods + 1):]
    if len(five) < 2:
        return None
    trs = [max(b["high"] - b["low"], abs(b["high"] - prev["close"]), abs(b["low"] - prev["close"])) for prev, b in zip(five, five[1:])]
    return round(sum(trs) / len(trs), 2) or None


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
