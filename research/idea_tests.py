"""Pre-registered tests of six classic intraday NIFTY ideas on 1m data, 2015-2024.

Written down BEFORE any of these were run (2026-11-22). Nothing below may be tuned after
seeing a result; a variant is a new idea and is labelled as one.

Common mechanics
  * Session 09:15-15:30 IST. Signals are judged on CLOSED bars only.
  * Entry at the OPEN of the 1m bar after the signal bar (no fill at the signal close).
  * Stop: touched by a later 1m high/low -> filled at the stop, or at that bar's open if it
    opened beyond the stop (gap through).
  * Time exit at the open of the 15:15 bar unless the idea says otherwise.
  * At most one trade per idea per day.
  * Returns in basis points of the index (comparable across 8,000 and 22,000 NIFTY).
  * Cost hurdle: 2.5 bp per round trip (~5.5 points at 22,000 - spread, slippage and charges on an
    ATM option at delta ~0.5, ex-theta). Sensitivity at 5 bp is reported, not used to pass.

Ideas
  H1 ORB15        Opening range = 09:15-09:29. First 5m bar closing above the OR high -> long,
                  below the OR low -> short, signal bars closing by 11:00. Stop: other side of OR.
  H2 ORB15_NARROW H1, only on days whose OR width is below the median OR width of the
                  previous 20 sessions (narrow ranges are said to break out more).
  H3 GAP_FADE     |gap| = |open / prev close - 1| >= 0.40%. At the close of the 09:15 5m bar,
                  trade against the gap. Target: prev close. Stop: entry +- gap size.
  H4 GAP_GO       |gap| >= 0.40% and the 09:15-09:29 range closes beyond the open in the gap's
                  direction -> trade with the gap at 09:30. Stop: the 09:15-09:29 low (long) /
                  high (short).
  H5 PDHL_BREAK   First 5m bar closing above the previous day's high -> long, below its low ->
                  short, signal bars closing 09:30-14:00. Stop: back 0.20% inside that level.
  H6 INTRADAY_MOM Gao-Han-Li-Zhou "market intraday momentum": r = close at 09:44 / prev close - 1.
                  Long at 15:00 if r > 0, short if r < 0, exit at the 15:29 close. No stop.
  RANDOM          Control: a coin-flip direction at 09:30, exit 15:15 - must net about -cost.

Pass bar (all three, net of the 2.5 bp hurdle)
  1. Full period mean > 0 with t >= 2.6 (about Bonferroni 5% for six ideas).
  2. Mean > 0 in the 2020-2024 confirmation window as well as 2015-2019.
  3. Net positive in at least 6 of the 10 calendar years (2024 is Jan-Mar only).

    python research/idea_tests.py --csv nifty50_candlestick_data.csv [--json out.json]
"""
import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

import pandas as pd

COST_BP = 2.5
T_PASS = 2.6


def load(csv_path: str) -> dict:
    df = pd.read_csv(csv_path)
    ts = pd.to_datetime(df["Date"] + " " + df["Time"], format="%d-%m-%Y %H:%M:%S")
    df = pd.DataFrame({"ts": ts, "o": df["Open"], "h": df["High"], "l": df["Low"], "c": df["Close"]})
    df = df[(df.ts.dt.time >= pd.Timestamp("09:15").time()) & (df.ts.dt.time < pd.Timestamp("15:30").time())]
    df = df.sort_values("ts").drop_duplicates("ts")
    days = {}
    for d, g in df.groupby(df.ts.dt.date):
        if len(g) < 300:  # broken / half sessions (muhurat, data holes) are skipped, not guessed
            continue
        g = g.reset_index(drop=True)
        g["m"] = (g.ts.dt.hour * 60 + g.ts.dt.minute) - (9 * 60 + 15)  # minute of session
        days[d] = g
    return days


def bars5(g: pd.DataFrame) -> pd.DataFrame:
    k = g.m // 5
    b = g.groupby(k).agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), last_m=("m", "max"))
    b["close_m"] = (b.index + 1) * 5  # session minute at which the 5m bar closes
    return b.reset_index(drop=True)


def run_trade(g: pd.DataFrame, entry_m: int, side: int, stop: float | None, target: float | None = None,
              exit_m: int = 360, exit_at_close: bool = False) -> dict | None:
    """Walk 1m bars from session minute `entry_m` (entry at that bar's open)."""
    rows = g[g.m >= entry_m]
    if rows.empty or rows.iloc[0].m > entry_m + 2:
        return None
    entry = float(rows.iloc[0].o)
    exit_px, how = None, "time"
    for r in rows.itertuples():
        if r.m >= exit_m and not exit_at_close:
            exit_px = float(r.o)
            break
        if stop is not None:
            if side > 0 and r.l <= stop:
                exit_px, how = (min(r.o, stop) if r.m > entry_m else stop), "stop"
                break
            if side < 0 and r.h >= stop:
                exit_px, how = (max(r.o, stop) if r.m > entry_m else stop), "stop"
                break
        if target is not None:
            if side > 0 and r.h >= target:
                exit_px, how = (max(r.o, target) if r.m > entry_m else target), "target"
                break
            if side < 0 and r.l <= target:
                exit_px, how = (min(r.o, target) if r.m > entry_m else target), "target"
                break
        if exit_at_close and r.m >= exit_m:
            exit_px = float(r.c)
            break
    if exit_px is None:
        exit_px = float(rows.iloc[-1].c)
    gross_bp = side * (exit_px - entry) / entry * 1e4
    return {"entry": entry, "exit": exit_px, "side": side, "gross_bp": gross_bp, "how": how}


def first_break(b: pd.DataFrame, hi: float, lo: float, from_m: int, to_m: int):
    for r in b.itertuples():
        if r.close_m < from_m or r.close_m > to_m:
            continue
        if r.c > hi:
            return r.close_m, 1
        if r.c < lo:
            return r.close_m, -1
    return None, 0


def simulate(days: dict, seed: int = 7) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    out: dict[str, list[dict]] = defaultdict(list)
    dates = sorted(days)
    or_widths: list[float] = []
    for i, d in enumerate(dates):
        g = days[d]
        b = bars5(g)
        prev = days[dates[i - 1]] if i > 0 else None
        op = float(g.iloc[0].o)
        first15 = g[g.m < 15]
        or_hi, or_lo = float(first15.h.max()), float(first15.l.min())
        or_w = or_hi - or_lo

        def add(name, t):
            if t is not None:
                t["date"] = str(d)
                out[name].append(t)

        # H1 / H2 opening-range breakout
        m, side = first_break(b, or_hi, or_lo, 20, 105)  # 5m closes 09:35 .. 11:00
        if side:
            stop = or_lo if side > 0 else or_hi
            t = run_trade(g, m, side, stop)
            add("H1_ORB15", t)
            if len(or_widths) >= 20 and or_w < statistics.median(or_widths[-20:]):
                add("H2_ORB15_NARROW", dict(t) if t else None)
        or_widths.append(or_w)

        if prev is not None:
            pc = float(prev.iloc[-1].c)
            pdh, pdl = float(prev.h.max()), float(prev.l.min())
            gap = op / pc - 1
            # H3 gap fade / H4 gap and go
            if abs(gap) >= 0.004:
                side = -1 if gap > 0 else 1
                entry_m = 5
                e_px = float(g[g.m >= entry_m].iloc[0].o) if (g.m >= entry_m).any() else None
                if e_px is not None:
                    gap_pts = abs(op - pc)
                    add("H3_GAP_FADE", run_trade(g, entry_m, side, e_px - side * gap_pts, target=pc))
                c15 = float(first15.iloc[-1].c)
                if (gap > 0 and c15 > op) or (gap < 0 and c15 < op):
                    side = 1 if gap > 0 else -1
                    add("H4_GAP_GO", run_trade(g, 15, side, or_lo if side > 0 else or_hi))
            # H5 previous-day high/low break
            m, side = first_break(b, pdh, pdl, 15, 285)  # 5m closes 09:30 .. 14:00
            if side:
                lvl = pdh if side > 0 else pdl
                add("H5_PDHL_BREAK", run_trade(g, m, side, lvl * (1 - side * 0.002)))
            # H6 intraday momentum (first 30 min incl. overnight -> last 30 min)
            at = g[g.m == 29]
            if not at.empty:
                r = float(at.iloc[0].c) / pc - 1
                if r != 0:
                    add("H6_INTRADAY_MOM", run_trade(g, 345, 1 if r > 0 else -1, None, exit_m=374, exit_at_close=True))
        # control
        add("RANDOM", run_trade(g, 15, rng.choice((1, -1)), None))
    return out


def summarise(trades: list[dict], cost_bp: float = COST_BP) -> dict:
    net = [t["gross_bp"] - cost_bp for t in trades]
    if len(net) < 2:
        return {"n": len(net)}
    mean, sd = statistics.mean(net), statistics.pstdev(net)
    t = mean / (sd / math.sqrt(len(net))) if sd else 0.0
    by_year = defaultdict(list)
    for tr, x in zip(trades, net):
        by_year[tr["date"][:4]].append(x)
    early = [x for tr, x in zip(trades, net) if tr["date"] < "2020"]
    late = [x for tr, x in zip(trades, net) if tr["date"] >= "2020"]
    years_pos = sum(1 for v in by_year.values() if sum(v) > 0)
    res = {
        "n": len(net), "per_year": round(len(net) / max(1, len(by_year)), 1),
        "gross_bp": round(statistics.mean(t_["gross_bp"] for t_ in trades), 2),
        "net_bp": round(mean, 2), "t": round(t, 2),
        "win": round(sum(1 for x in net if x > 0) / len(net), 3),
        "net_2015_19": round(statistics.mean(early), 2) if early else None,
        "net_2020_24": round(statistics.mean(late), 2) if late else None,
        "years_positive": f"{years_pos}/{len(by_year)}",
        "by_year": {y: round(sum(v), 1) for y, v in sorted(by_year.items())},
        "net_bp_at_5bp": round(statistics.mean(t_["gross_bp"] - 5.0 for t_ in trades), 2),
    }
    res["PASS"] = bool(mean > 0 and t >= T_PASS and (res["net_2015_19"] or 0) > 0 and (res["net_2020_24"] or 0) > 0
                       and years_pos >= 6)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    days = load(a.csv)
    print(f"{len(days)} sessions {min(days)} .. {max(days)}")
    res = simulate(days)
    report = {k: summarise(v) for k, v in sorted(res.items())}
    hdr = f"{'idea':<17}{'n':>5}{'/yr':>6}{'gross':>8}{'net':>8}{'t':>7}{'win':>6}{'15-19':>8}{'20-24':>8}{'yrs+':>7}{'@5bp':>7}  verdict"
    print(hdr)
    for k, r in report.items():
        print(f"{k:<17}{r['n']:>5}{r['per_year']:>6}{r['gross_bp']:>8}{r['net_bp']:>8}{r['t']:>7}{r['win']:>6}"
              f"{str(r['net_2015_19']):>8}{str(r['net_2020_24']):>8}{r['years_positive']:>7}{r['net_bp_at_5bp']:>7}  "
              f"{'PASS' if r['PASS'] else 'fail'}")
    if a.json:
        Path(a.json).write_text(json.dumps({"report": report, "trades": res}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
