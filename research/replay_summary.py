"""Run research.funnel_diagnostic per year with --set TECH_SHADOW_STRATEGIES= (so shadow-only
families trade too), --json into $FD_DIR/<UNDERLYING>_<period>.json, then summarise here.

Summarise the 2016-2024 TECH engine replays: executed paper trades and every candidate signal."""
import collections
import glob
import json
import os
import statistics

FD = os.environ.get("FD_DIR", "research_out/fd")


def load(u):
    trades, outs = [], []
    for f in sorted(glob.glob(os.path.join(FD, f"{u}_*.json"))):
        d = json.load(open(f))
        trades += d.get("trades_rows", [])
        outs += d.get("outcome_rows", [])
    return trades, outs


def tstat(xs):
    return statistics.mean(xs) / (statistics.stdev(xs) / len(xs) ** 0.5) if len(xs) > 2 and statistics.stdev(xs) > 0 else 0.0


out = {}
for u in ("NIFTY", "BANKNIFTY"):
    trades, outs = load(u)
    if not trades and not outs:
        continue
    print(f"\n===== {u}: {len(glob.glob(os.path.join(FD, f'{u}_*.json')))} yearly files")
    nets = [float(t["net_pnl"]) for t in trades]
    print(f"EXECUTED paper trades: {len(trades)}  net Rs.{sum(nets):,.0f}  win {sum(n > 0 for n in nets) / max(1, len(nets)):.0%}  "
          f"avg Rs.{statistics.mean(nets) if nets else 0:,.0f}/trade  t {tstat(nets):.2f}")
    by_s = collections.defaultdict(list)
    by_y = collections.defaultdict(float)
    for t in trades:
        by_s[t["details"].get("strategy")].append(float(t["net_pnl"]))
        by_y[str(t["entry_ts"])[:4]] += float(t["net_pnl"])
    for s, v in sorted(by_s.items(), key=lambda kv: -len(kv[1])):
        print(f"   {s:24} trades {len(v):4d}  net Rs.{sum(v):10,.0f}  win {sum(x > 0 for x in v) / len(v):4.0%}")
    print("   by year: " + ", ".join(f"{y} {v:,.0f}" for y, v in sorted(by_y.items())))
    print(f"ALL CANDIDATE SIGNALS (every possible trade, measured on the index with the plan's stop/target): {len(outs)}")
    groups = collections.defaultdict(list)
    for r in outs:
        groups[r["strategy"]].append(r)
    rows = {}
    for s, v in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        rs = [float(r["r"]) for r in v if r.get("how") != "degenerate"]
        hows = collections.Counter(r.get("how") for r in v)
        appr = [float(r["r"]) for r in v if r.get("approved") and r.get("how") != "degenerate"]
        tf = hows["target"] / max(1, hows["target"] + hows["stop"])
        rows[s] = {"n": len(v), "target_first": tf, "avg_r": statistics.mean(rs) if rs else 0, "t": tstat(rs), "approved": len(appr),
                   "approved_avg_r": statistics.mean(appr) if appr else None, "eod_share": hows["eod"] / len(v)}
        print(f"   {s:24} signals {len(v):5d}  target-first {tf:4.0%}  avg R {rows[s]['avg_r']:+.3f} (t {rows[s]['t']:+.2f})  "
              f"reached the end of day {rows[s]['eod_share']:4.0%}  approved {len(appr):4d}"
              + (f" (avg R {rows[s]['approved_avg_r']:+.3f})" if appr else ""))
    stages = collections.Counter(r["stage"] for r in outs if not r.get("approved"))
    print("   where candidates were stopped: " + ", ".join(f"{k} {v}" for k, v in stages.most_common(6)))
    lr = rows.get("LEVEL_REJECTION")
    if lr:
        print(f"LEVEL_REJECTION vs its promotion bar (10+ signals, >=55% target-first): {lr['n']} signals, {lr['target_first']:.0%} target-first "
              f"-> {'MEETS' if lr['n'] >= 10 and lr['target_first'] >= 0.55 else 'does NOT meet'} the bar")
    out[u] = {"trades": len(trades), "net": sum(nets), "by_strategy_trades": {s: (len(v), sum(v)) for s, v in by_s.items()},
              "by_year": dict(by_y), "signals": rows}
json.dump(out, open(os.path.join(FD, "summary.json"), "w"), indent=1, default=str)
