"""Pre-registered WEEKLY-HOLD NIFTY option ideas. Written 2026-11-22, BEFORE any run.

These are NEW ideas, labelled as such, prompted by research/overnight_options.py: the
overnight iron fly earned about Rs.356 a night before costs on real prices, but paying
four legs in and out every night (about Rs.500) more than ate it. Holding one position
for the whole weekly cycle pays the entry once and lets most legs expire. Two buy-side
mirrors are included because Mahi prefers buying.

Rules (one trade per weekly cycle)
  * On each weekly expiry day E0, at the close, open a position in the NEXT weekly expiry
    E1 and hold it to E1. Settle every leg at intrinsic value using the official NIFTY
    close on E1 (NSE's final settlement price). No stops, no adjustments.
  * Strikes: ATM = strike nearest the underlying at entry (50-point grid); "+1%" = strike
    nearest underlying x 1.01, and so on. One lot of 65; P&L in rupees per lot.

  W1 WEEKLY_IRON_FLY      sell ATM CE + ATM PE, buy CE +2% and PE -2%
  W2 WEEKLY_IRON_CONDOR   sell CE +1% and PE -1%, buy CE +2% and PE -2%
  W3 WEEKLY_LONG_STRADDLE buy ATM CE + ATM PE
  W4 WEEKLY_LONG_ITM_CALL buy CE at -1%

Costs
  * Entry, every leg: slippage max(Rs.0.50, 0.5% of premium), brokerage Rs.20, STT 0.15%
    on premium sold, exchange 0.035%, SEBI 0.0001%, stamp 0.003% on premium bought, GST
    18% on brokerage + exchange + SEBI.
  * Expiry, every leg that finishes in the money: Rs.20 exercise/assignment charge, and STT
    0.15% of intrinsic value on bought legs. Legs that expire worthless cost nothing.

Test 1 - real prices: NSE F&O bhavcopy, Aug - Nov 2026. Entry = ClsPric of the E1
  contract on E0; underlying at entry by put-call parity (median K + C - P, as in
  overnight_options.py); settlement from the official NIFTY close (index closes file).
Test 2 - 14 Feb 2019 - 27 Mar 2024 (NIFTY weekly era up to the end of our minute data):
  entry spot = official NIFTY close on E0; entry premium = Black-Scholes, r = 0, IV =
  India VIX close on E0 x the moneyness/DTE multipliers that overnight_options.py
  calibrates from real close prices; settlement from the official close on E1, so only
  the entry price is modelled. Expiries: weekly Thursday, holiday -> previous trading day.

Pass bar (stricter than before: we have now tried nine option ideas)
  1. Test 2: mean net > 0 with t >= 2.8, positive in 2019-21 AND 2022-24, and positive in
     at least 4 of the 6 calendar years.
  2. Test 1: total net > 0.

    python research/weekly_options.py --fo <fo_bhavcopy/2026> --ic <index_closes> [--json out.json]
"""
import argparse
import datetime as dt
import glob
import json
import math
import os
import statistics
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import overnight_options as O  # noqa: E402  (same rate card, BS pricer, calibration)

LOT = O.LOT
STRATS = {
    "W1_WEEKLY_IRON_FLY": [(-1, 0.00, "CE"), (-1, 0.00, "PE"), (+1, +0.02, "CE"), (+1, -0.02, "PE")],
    "W2_WEEKLY_IRON_CONDOR": [(-1, +0.01, "CE"), (-1, -0.01, "PE"), (+1, +0.02, "CE"), (+1, -0.02, "PE")],
    "W3_WEEKLY_LONG_STRADDLE": [(+1, 0.00, "CE"), (+1, 0.00, "PE")],
    "W4_WEEKLY_LONG_ITM_CALL": [(+1, -0.01, "CE")],
}
T_PASS = 2.8
EXERCISE_FEE = 20.0


def entry_cost(side: int, px: float) -> float:
    q = LOT
    turnover = px * q
    exch, sebi = turnover * O.EXCH / 100, turnover * O.SEBI / 100
    stt = turnover * O.STT_SELL / 100 if side < 0 else 0.0
    stamp = turnover * O.STAMP_BUY / 100 if side > 0 else 0.0
    gst = (O.BROKERAGE + exch + sebi) * O.GST / 100
    return O.slip(px) * q + O.BROKERAGE + exch + sebi + stt + stamp + gst


def settle(side: int, k: float, ot: str, close: float) -> tuple[float, float]:
    """(intrinsic per unit, settlement cost for the lot)."""
    iv = max(0.0, close - k) if ot == "CE" else max(0.0, k - close)
    if iv <= 0:
        return 0.0, 0.0
    cost = EXERCISE_FEE * (1 + O.GST / 100)
    if side > 0:
        cost += iv * LOT * O.STT_SELL / 100
    return iv, cost


def trade(legs, spot: float, entry_px, close_e1: float) -> dict | None:
    gross = cost = 0.0
    for side, off, ot in legs:
        k = O.strike(spot, off)
        px = entry_px(k, ot, off)
        if px is None:
            return None
        intrinsic, scost = settle(side, k, ot, close_e1)
        gross += side * (intrinsic - px) * LOT
        cost += entry_cost(side, px) + scost
    return {"gross": gross, "net": gross - cost}


def load_closes(ic_dir: str) -> dict:
    out = {}
    for f in sorted(glob.glob(os.path.join(ic_dir, "*", "*.csv"))):
        d = pd.read_csv(f)
        n = d[d["Index Name"].astype(str).str.strip() == "Nifty 50"]
        if len(n):
            out[dt.date.fromisoformat(os.path.basename(f)[:-4])] = float(n["Closing Index Value"].iloc[0])
    return out


def test_real(fo: pd.DataFrame, closes: dict) -> dict:
    days = set(fo.TradDt.unique())
    last = max(days)
    expiries = sorted(e for e in fo.XpryDt.unique() if e in days or e <= last)
    by_day = {d: g for d, g in fo.groupby("TradDt")}
    trades = defaultdict(list)
    for e0 in [e for e in expiries if e in days]:
        later = [e for e in expiries if e > e0]
        if not later or later[0] > last or later[0] not in closes:
            continue
        e1 = later[0]
        rows = by_day[e0]
        spot = O.parity_underlying(rows, e1)

        def px(k, ot, off, rows=rows, e1=e1):
            r = rows[(rows.XpryDt == e1) & (rows.StrkPric == k) & (rows.OptnTp == ot)]
            return float(r.ClsPric.iloc[0]) if len(r) and float(r.ClsPric.iloc[0]) > 0 else None

        for name, legs in STRATS.items():
            t = trade(legs, spot, px, closes[e1])
            if t:
                trades[name].append({"day": e0.isoformat(), "expiry": e1.isoformat(), **t})
    return trades


def test_model(closes: dict, vix: dict, mult: dict, start: dt.date, end: dt.date) -> dict:
    trading = set(closes)
    trades = defaultdict(list)
    d = start
    e0 = O.expiry_for(d - dt.timedelta(days=1), trading)
    while True:
        e1 = O.expiry_for(e0, trading)
        if e1 > end:
            break
        if e0 in vix and e0 in closes and e1 in closes:
            spot, days_left = closes[e0], (e1 - e0).days
            t0 = O.years(dt.datetime.combine(e0, dt.time(15, 30)), e1)

            def px(k, ot, off, spot=spot, t0=t0, days_left=days_left, v=vix[e0]):
                iv = O.iv_for(mult, k / spot - 1, days_left, v)
                return max(0.05, O.bs(spot, k, t0, iv, ot))

            for name, legs in STRATS.items():
                trades[name].append({"day": e0.isoformat(), "expiry": e1.isoformat(), **trade(legs, spot, px, closes[e1])})
        e0 = e1
    return trades


def summarize(rows: list) -> dict:
    nets = [r["net"] for r in rows]
    n = len(nets)
    mean = statistics.mean(nets) if n else 0.0
    sd = statistics.stdev(nets) if n > 1 else 0.0
    yrs = defaultdict(float)
    for r in rows:
        yrs[r["day"][:4]] += r["net"]
    return {"trades": n, "win": sum(x > 0 for x in nets) / n if n else 0.0,
            "gross_mean": statistics.mean(r["gross"] for r in rows) if n else 0.0, "net_mean": mean,
            "net_total": sum(nets), "worst": min(nets) if n else 0.0, "t": mean / (sd / math.sqrt(n)) if sd else 0.0,
            "years": dict(sorted(yrs.items())), "years_pos": sum(v > 0 for v in yrs.values()),
            "h1": sum(r["net"] for r in rows if r["day"] < "2022"), "h2": sum(r["net"] for r in rows if r["day"] >= "2022")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fo", required=True)
    ap.add_argument("--ic", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    fo, vix, closes = O.load_fo(a.fo), O.load_vix(a.ic), load_closes(a.ic)
    _, mult, _ = O.test_real(fo, vix)  # calibration only; overnight P&L is ignored here
    real = test_real(fo, closes)
    model = test_model(closes, vix, mult, dt.date(2019, 2, 14), dt.date(2024, 3, 27))
    out = {"real": {}, "model": {}}
    print(f"Test 1 - real NSE prices, weekly cycles  (Rs. per lot of {LOT})")
    print(f"{'strategy':24} {'trades':>6} {'win':>5} {'gross/tr':>9} {'net/tr':>8} {'net total':>10} {'worst':>8}")
    for name in STRATS:
        s = summarize(real[name])
        out["real"][name] = s
        print(f"{name:24} {s['trades']:6d} {s['win']:5.0%} {s['gross_mean']:9.0f} {s['net_mean']:8.0f} {s['net_total']:10.0f} {s['worst']:8.0f}")
    print("\nTest 2 - 14 Feb 2019 .. 27 Mar 2024, VIX-priced entry, real settlement")
    print(f"{'strategy':24} {'trades':>6} {'win':>5} {'gross/tr':>9} {'net/tr':>8} {'net total':>10} {'worst':>8} {'t':>6} {'19-21':>8} {'22-24':>8} {'yrs+':>5}  verdict")
    for name in STRATS:
        s = summarize(model[name])
        ok = (s["net_mean"] > 0 and s["t"] >= T_PASS and s["h1"] > 0 and s["h2"] > 0 and s["years_pos"] >= 4
              and out["real"][name]["net_total"] > 0)
        s["verdict"] = "PASS" if ok else "fail"
        out["model"][name] = s
        print(f"{name:24} {s['trades']:6d} {s['win']:5.0%} {s['gross_mean']:9.0f} {s['net_mean']:8.0f} {s['net_total']:10.0f} "
              f"{s['worst']:8.0f} {s['t']:6.2f} {s['h1']:8.0f} {s['h2']:8.0f} {s['years_pos']:3d}/{len(s['years'])}  {s['verdict']}")
    print("\nTest 2 net by year (Rs. per lot):")
    yrs = sorted(out["model"]["W1_WEEKLY_IRON_FLY"]["years"])
    print(f"{'strategy':24} " + " ".join(f"{y:>8}" for y in yrs))
    for name in STRATS:
        print(f"{name:24} " + " ".join(f"{out['model'][name]['years'].get(y, 0):8.0f}" for y in yrs))
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"out": out, "real_trades": real}, f, indent=1, default=str)


if __name__ == "__main__":
    main()
