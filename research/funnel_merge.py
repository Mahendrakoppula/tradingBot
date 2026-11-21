"""Merge funnel_diagnostic JSON reports (e.g. two halves run in parallel) and
print the funnel as a compact table.

    python research/funnel_merge.py a.json b.json [--label baseline]
"""
import argparse
import collections
import json
import statistics


def merge(paths: list[str]) -> dict:
    out: dict = {"sessions": 0, "trade_ready": 0, "decisions": 0, "approved": 0, "trades": 0, "net_pnl": 0.0,
                 "trades_rows": []}
    counters = collections.defaultdict(collections.Counter)
    for p in paths:
        d = json.load(open(p))
        for k in ("sessions", "trade_ready", "decisions", "approved", "trades"):
            out[k] += d[k]
        out["net_pnl"] += d["net_pnl"]
        out["trades_rows"].extend(d.get("trades_rows", []))
        for k in ("presignal_transitions", "pipeline_stage_reached", "rejections_incl_gate", "family_verdicts",
                  "regime_at_decision", "exit_reasons", "presignal_terminal_reasons"):
            counters[k].update(d.get(k, {}))
    for k, c in counters.items():
        out[k] = dict(c.most_common())
    net = [float(t["net_pnl"]) for t in out["trades_rows"]]
    out["win_rate"] = round(sum(1 for x in net if x > 0) / len(net), 3) if net else None
    out["avg_trade"] = round(statistics.mean(net), 1) if net else None
    out["trades_per_session"] = round(out["trades"] / max(1, out["sessions"]), 2)
    return out


def render(d: dict, label: str) -> str:
    lines = [f"== {label}: {d['sessions']} sessions | TRADE_READY {d['trade_ready']} | approved {d['approved']} | "
             f"trades {d['trades']} ({d['trades_per_session']}/day) | net Rs.{d['net_pnl']:,.0f} | "
             f"win {d['win_rate']} | avg {d['avg_trade']}"]
    total = d["decisions"] or 1
    lines.append("  where decisions stopped:")
    for k, v in d["pipeline_stage_reached"].items():
        lines.append(f"    {k:<20} {v:>6}  {100 * v / total:5.1f}%")
    lines.append("  top rejection reasons:")
    for k, v in list(d["rejections_incl_gate"].items())[:14]:
        lines.append(f"    {k:<60} {v:>6}")
    if d.get("exit_reasons"):
        lines.append("  exits: " + ", ".join(f"{k}={v}" for k, v in d["exit_reasons"].items()))
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--label", default="run")
    ap.add_argument("--families", action="store_true", help="also print per-family routing verdicts")
    a = ap.parse_args()
    m = merge(a.paths)
    print(render(m, a.label))
    if a.families:
        print("  regimes at decision: " + ", ".join(f"{k}={v}" for k, v in m["regime_at_decision"].items()))
        for k, v in list(m["family_verdicts"].items())[:40]:
            print(f"    {k:<60} {v:>6}")
