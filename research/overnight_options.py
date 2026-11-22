"""Pre-registered overnight NIFTY option strategies. Written 2026-11-22, BEFORE any run.

Why: Mahi prefers buying options. Our minute data says NIFTY's gains arrive overnight
(close -> next open averaged +12.6 bp a night, 2015 - Mar 2024), and Bhat, Pandey & Rao
(J. Futures Markets 2024) found NIFTY option sellers earn their premium overnight. So the
honest test is: buy or sell options at the close, get out at the next open - which of
these five rules makes money after Indian costs? Nothing below may be tuned after seeing a
result; a variant is a new idea and is labelled as one.

Common mechanics
  * NIFTY index options, nearest expiry strictly after the entry day.
  * Enter at the close of day t, exit at the open of the next trading day. Every trading
    night is traded, one trade per strategy per night, no filters.
  * ATM = strike nearest the underlying at entry (50-point grid); "+1%" = strike nearest
    underlying x 1.01, and so on.
  * Size: one lot of 65 (today's lot) for every strategy; P&L in rupees per lot.

Strategies
  B1 LONG_CALL      buy ATM CE
  B2 LONG_ITM_CALL  buy CE at -1% (one per cent in the money)
  B3 LONG_STRADDLE  buy ATM CE + ATM PE
  S1 IRON_CONDOR    sell CE +1% and PE -1%, buy CE +2% and PE -2%
  S2 IRON_FLY       sell ATM CE + ATM PE, buy CE +2% and PE -2%

Costs, every leg, entry and exit
  * slippage max(Rs.0.50, 0.5% of premium) per unit, always against us
  * brokerage Rs.20 per order; STT 0.15% of premium on the sell side (rate from
    1 Apr 2026); exchange 0.035%; SEBI 0.0001%; stamp duty 0.003% on the buy side;
    GST 18% on brokerage + exchange + SEBI.

Test 1 - real prices: NSE F&O bhavcopy, 3 Aug - 13 Nov 2026 (70 nights)
  entry = ClsPric on day t, exit = OpnPric on day t+1, same contract.

Test 2 - NIFTY 1-minute spot, 12 Jan 2015 - 27 Mar 2024, Black-Scholes prices
  * entry spot = the 15:29 bar close; exit spot = the next session's 09:15 bar open.
  * IV = India VIX close on day t x a multiplier by moneyness and days to expiry, held
    constant overnight. Multipliers = median(implied vol / VIX) in Test 1's data, measured
    from CLOSE prices only (no exit prices, no P&L).
  * Expiries: monthly (last Thursday) before 14 Feb 2019, weekly Thursday from then; an
    expiry that falls on a non-trading day moves to the previous trading day.
  * Calendar-time decay to 15:30 on expiry day, r = 0.

Data note, added after the first run (a measurement fix, not a rule change): NSE's
reported UndrlygPric is the official index close, which on 23 of the 70 entry days sat
50-200 points away from the level the option closing prices themselves imply. So "the
underlying at entry" in Test 1 is measured by put-call parity on the entry expiry (median
of K + C - P within 300 points), for strike choice and IV calibration alike. The first
run, which used UndrlygPric, is kept in the commit message for comparison.

Pass bar - a strategy must clear both
  1. Test 2: mean net per trade > 0 with t >= 2.6, positive in 2015-19 AND 2020-24, and
     positive in at least 6 of the 10 calendar years (2024 is Jan-Mar only).
  2. Test 1: total net P&L > 0.

    python research/overnight_options.py --fo <fo_bhavcopy/2026 dir> \
        --ic <index_closes dir> --minute nifty50_candlestick_data.csv [--json out.json]
"""
import argparse
import datetime as dt
import glob
import json
import math
import os
import statistics
from collections import defaultdict

import pandas as pd

LOT = 65
STEP = 50.0
STRATS = {
    "B1_LONG_CALL": [(+1, 0.00, "CE")],
    "B2_LONG_ITM_CALL": [(+1, -0.01, "CE")],
    "B3_LONG_STRADDLE": [(+1, 0.00, "CE"), (+1, 0.00, "PE")],
    "S1_IRON_CONDOR": [(-1, +0.01, "CE"), (-1, -0.01, "PE"), (+1, +0.02, "CE"), (+1, -0.02, "PE")],
    "S2_IRON_FLY": [(-1, 0.00, "CE"), (-1, 0.00, "PE"), (+1, +0.02, "CE"), (+1, -0.02, "PE")],
}
# rate card - see docstring
BROKERAGE, STT_SELL, EXCH, SEBI, STAMP_BUY, GST = 20.0, 0.15, 0.035, 0.0001, 0.003, 18.0
T_PASS = 2.6
WEEKLY_FROM = dt.date(2019, 2, 14)


def strike(spot: float, offset: float) -> float:
    return round(spot * (1 + offset) / STEP) * STEP


def slip(price: float) -> float:
    return max(0.5, 0.005 * price)


def leg_cost(side: int, entry: float, exit_: float) -> float:
    """Slippage + charges for one leg of one lot, entry and exit. side +1 = bought first."""
    q = LOT
    buy_px, sell_px = (entry, exit_) if side > 0 else (exit_, entry)
    slippage = (slip(entry) + slip(exit_)) * q
    turnover = (buy_px + sell_px) * q
    exch = turnover * EXCH / 100
    sebi = turnover * SEBI / 100
    brokerage = 2 * BROKERAGE
    stt = sell_px * q * STT_SELL / 100
    stamp = buy_px * q * STAMP_BUY / 100
    gst = (brokerage + exch + sebi) * GST / 100
    return slippage + brokerage + exch + sebi + stt + stamp + gst


# ---------- Black-Scholes, r = 0 ----------
def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(spot: float, k: float, t: float, iv: float, ot: str) -> float:
    if t <= 0 or iv <= 0:
        return max(0.0, spot - k) if ot == "CE" else max(0.0, k - spot)
    sd = iv * math.sqrt(t)
    d1 = (math.log(spot / k) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if ot == "CE":
        return spot * _ncdf(d1) - k * _ncdf(d2)
    return k * _ncdf(-d2) - spot * _ncdf(-d1)


def implied_vol(price: float, spot: float, k: float, t: float, ot: str) -> float | None:
    intrinsic = max(0.0, spot - k) if ot == "CE" else max(0.0, k - spot)
    if t <= 0 or price <= intrinsic + 0.05:
        return None
    lo, hi = 0.01, 3.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs(spot, k, t, mid, ot) > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def years(from_: dt.datetime, expiry: dt.date) -> float:
    end = dt.datetime.combine(expiry, dt.time(15, 30))
    return max(0.0, (end - from_).total_seconds()) / (365.0 * 86400)


def dte_bucket(days: int) -> str:
    return "1" if days <= 1 else ("2-4" if days <= 4 else "5+")


def m_bucket(offset: float) -> str:
    return f"{round(offset * 100):+d}%"


# ---------- data ----------
def load_fo(fo_dir: str) -> pd.DataFrame:
    cols = ["TradDt", "FinInstrmTp", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "OpnPric", "ClsPric", "UndrlygPric"]
    parts = []
    for f in sorted(glob.glob(os.path.join(fo_dir, "*.csv"))):
        d = pd.read_csv(f, usecols=cols)
        parts.append(d[(d.TckrSymb == "NIFTY") & (d.FinInstrmTp == "IDO")])
    fo = pd.concat(parts)
    fo["TradDt"] = pd.to_datetime(fo.TradDt).dt.date
    fo["XpryDt"] = pd.to_datetime(fo.XpryDt).dt.date
    return fo


def load_vix(ic_dir: str) -> dict:
    vix = {}
    for f in sorted(glob.glob(os.path.join(ic_dir, "*", "*.csv"))):
        d = pd.read_csv(f)
        v = d[d["Index Name"].astype(str).str.strip().str.lower() == "india vix"]
        if len(v):
            vix[dt.date.fromisoformat(os.path.basename(f)[:-4])] = float(v["Closing Index Value"].iloc[0])
    return vix


def load_minute(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    ts = pd.to_datetime(df["Date"] + " " + df["Time"], format="%d-%m-%Y %H:%M:%S")
    df = pd.DataFrame({"ts": ts, "o": df["Open"], "c": df["Close"]})
    df = df[(df.ts.dt.time >= dt.time(9, 15)) & (df.ts.dt.time < dt.time(15, 30))].sort_values("ts").drop_duplicates("ts")
    df["d"] = df.ts.dt.date
    g = df.groupby("d")
    day = pd.DataFrame({"open": g.o.first(), "first": g.ts.first(), "close_1529": g.apply(
        lambda x: x.loc[x.ts.dt.time == dt.time(15, 29), "c"].iloc[0] if (x.ts.dt.time == dt.time(15, 29)).any() else float("nan"),
        include_groups=False)})
    return day[day["first"].dt.time == dt.time(9, 15)]


# ---------- test 1: real prices ----------
def parity_underlying(day_rows: pd.DataFrame, exp: dt.date) -> float:
    """The level the option closing prices themselves imply: median of K + C - P over
    strikes within 300 points of NSE's reported underlying (see the data note)."""
    e = day_rows[day_rows.XpryDt == exp]
    reported = float(e.UndrlygPric.iloc[0])
    ce = e[e.OptnTp == "CE"].set_index("StrkPric").ClsPric
    pe = e[e.OptnTp == "PE"].set_index("StrkPric").ClsPric
    both = [k for k in ce.index.intersection(pe.index) if abs(k - reported) <= 300 and ce[k] > 0 and pe[k] > 0]
    return statistics.median(k + ce[k] - pe[k] for k in both) if both else reported


def test_real(fo: pd.DataFrame, vix: dict):
    days = sorted(fo.TradDt.unique())
    by_day = {d: g for d, g in fo.groupby("TradDt")}
    trades = defaultdict(list)
    calib = defaultdict(list)  # (m_bucket, dte_bucket) -> iv/vix, from CLOSE prices only
    for t, n in zip(days[:-1], days[1:]):
        dt_, dn = by_day[t], by_day[n]
        exp = min(e for e in dt_.XpryDt.unique() if e > t)
        spot = parity_underlying(dt_, exp)
        tt = years(dt.datetime.combine(t, dt.time(15, 30)), exp)
        db = dte_bucket((exp - t).days)

        def px(frame, k, ot, col):
            r = frame[(frame.XpryDt == exp) & (frame.StrkPric == k) & (frame.OptnTp == ot)]
            return float(r[col].iloc[0]) if len(r) and float(r[col].iloc[0]) > 0 else None

        # calibration: OTM side for +/- offsets, both sides averaged at the money
        if t in vix:
            for off in (-0.02, -0.01, 0.0, 0.01, 0.02):
                k = strike(spot, off)
                sides = ("CE", "PE") if off == 0 else (("CE",) if off > 0 else ("PE",))
                ivs = [implied_vol(px(dt_, k, ot, "ClsPric") or 0, spot, k, tt, ot) for ot in sides]
                ivs = [v for v in ivs if v]
                if ivs:
                    calib[(m_bucket(off), db)].append(statistics.mean(ivs) / (vix[t] / 100))
        for name, legs in STRATS.items():
            gross = cost = 0.0
            ok = True
            for side, off, ot in legs:
                k = strike(spot, off)
                a, b = px(dt_, k, ot, "ClsPric"), px(dn, k, ot, "OpnPric")
                if a is None or b is None:
                    ok = False
                    break
                gross += side * (b - a) * LOT
                cost += leg_cost(side, a, b)
            if ok:
                trades[name].append({"day": t.isoformat(), "gross": gross, "net": gross - cost})
    mult = {k: statistics.median(v) for k, v in calib.items()}
    return trades, mult, {k: len(v) for k, v in calib.items()}


# ---------- test 2: 2015-2024 model ----------
def _last_thursday(any_day: dt.date) -> dt.date:
    nxt = (any_day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    e = nxt - dt.timedelta(days=1)
    return e - dt.timedelta(days=(e.weekday() - 3) % 7)


def _scheduled_expiry(probe: dt.date) -> dt.date:
    """First scheduled (pre-holiday-adjustment) expiry on or after probe."""
    if probe >= WEEKLY_FROM:
        return probe + dt.timedelta(days=(3 - probe.weekday()) % 7)
    e = _last_thursday(probe)
    if e < probe:
        e = _last_thursday((probe.replace(day=28) + dt.timedelta(days=4)).replace(day=1))
    return e


def expiry_for(d: dt.date, trading: set) -> dt.date:
    """Nearest expiry strictly after d under the historical NSE calendar; a scheduled
    expiry on a non-trading day moves to the previous trading day."""
    probe = d + dt.timedelta(days=1)
    for _ in range(60):
        raw = _scheduled_expiry(probe)
        e = raw
        while e not in trading and e > d:
            e -= dt.timedelta(days=1)
        if e > d:
            return e
        probe = raw + dt.timedelta(days=1)
    raise RuntimeError(f"no expiry after {d}")


def iv_for(mult: dict, offset: float, days_left: int, vix_close: float) -> float:
    db = dte_bucket(days_left)
    pts = sorted((float(mb[:-1]) / 100, v) for (mb, b), v in mult.items() if b == db)
    if not pts:
        raise RuntimeError(f"no calibration for DTE bucket {db}")
    if offset <= pts[0][0]:
        m = pts[0][1]
    elif offset >= pts[-1][0]:
        m = pts[-1][1]
    else:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= offset <= x1:
                m = y0 + (y1 - y0) * (offset - x0) / (x1 - x0)
                break
    return m * vix_close / 100


def test_model(day: pd.DataFrame, vix: dict, mult: dict):
    trading = set(day.index)
    ds = sorted(trading)
    trades = defaultdict(list)
    skipped = 0
    for t, n in zip(ds[:-1], ds[1:]):
        s0, s1 = day.loc[t, "close_1529"], day.loc[n, "open"]
        if t not in vix or not (s0 > 0) or not (s1 > 0):
            skipped += 1
            continue
        exp = expiry_for(t, trading)
        t0 = years(dt.datetime.combine(t, dt.time(15, 30)), exp)
        t1 = years(dt.datetime.combine(n, dt.time(9, 15)), exp)
        days_left = (exp - t).days
        for name, legs in STRATS.items():
            gross = cost = 0.0
            for side, off, ot in legs:
                k = strike(s0, off)
                iv = iv_for(mult, k / s0 - 1, days_left, vix[t])
                a, b = bs(s0, k, t0, iv, ot), bs(s1, k, t1, iv, ot)
                a, b = max(0.05, a), max(0.05, b)
                gross += side * (b - a) * LOT
                cost += leg_cost(side, a, b)
            trades[name].append({"day": t.isoformat(), "gross": gross, "net": gross - cost})
    return trades, skipped


def summarize(rows: list, by_year: bool) -> dict:
    nets = [r["net"] for r in rows]
    gross = [r["gross"] for r in rows]
    n = len(nets)
    mean = statistics.mean(nets) if n else 0.0
    sd = statistics.stdev(nets) if n > 1 else 0.0
    out = {"trades": n, "win": sum(x > 0 for x in nets) / n if n else 0.0, "gross_mean": statistics.mean(gross) if n else 0.0,
           "net_mean": mean, "net_total": sum(nets), "t": mean / (sd / math.sqrt(n)) if sd else 0.0}
    if by_year:
        yrs = defaultdict(float)
        for r in rows:
            yrs[r["day"][:4]] += r["net"]
        out["years"] = dict(sorted(yrs.items()))
        out["h1"] = sum(r["net"] for r in rows if r["day"] < "2020")
        out["h2"] = sum(r["net"] for r in rows if r["day"] >= "2020")
        out["years_pos"] = sum(v > 0 for v in yrs.values())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fo", required=True)
    ap.add_argument("--ic", required=True)
    ap.add_argument("--minute", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    fo, vix = load_fo(a.fo), load_vix(a.ic)
    real, mult, ncal = test_real(fo, vix)
    print("IV / India VIX multipliers (median, from close prices):")
    for k in sorted(mult):
        print(f"  {k[0]:>4} DTE {k[1]:>3}: {mult[k]:.2f}  (n={ncal[k]})")
    day = load_minute(a.minute)
    model, skipped = test_model(day, vix, mult)
    res = {"real": {}, "model": {}, "multipliers": {f"{k[0]} {k[1]}": v for k, v in mult.items()}, "model_skipped": skipped}
    print(f"\nTest 1 - real NSE prices, {min(r['day'] for r in real['B1_LONG_CALL'])} .. {max(r['day'] for r in real['B1_LONG_CALL'])}  (Rs. per lot of {LOT})")
    print(f"{'strategy':18} {'trades':>6} {'win':>5} {'gross/tr':>9} {'net/tr':>8} {'net total':>10} {'t':>6}")
    for name in STRATS:
        s = summarize(real[name], False)
        res["real"][name] = s
        print(f"{name:18} {s['trades']:6d} {s['win']:5.0%} {s['gross_mean']:9.0f} {s['net_mean']:8.0f} {s['net_total']:10.0f} {s['t']:6.2f}")
    print(f"\nTest 2 - 2015..Mar 2024 minute spot, VIX-priced options  (skipped nights: {skipped})")
    print(f"{'strategy':18} {'trades':>6} {'win':>5} {'gross/tr':>9} {'net/tr':>8} {'net total':>10} {'t':>6} {'15-19':>9} {'20-24':>9} {'yrs+':>5}  verdict")
    for name in STRATS:
        s = summarize(model[name], True)
        r = res["real"][name]
        ok = s["net_mean"] > 0 and s["t"] >= T_PASS and s["h1"] > 0 and s["h2"] > 0 and s["years_pos"] >= 6 and r["net_total"] > 0
        s["verdict"] = "PASS" if ok else "fail"
        res["model"][name] = s
        print(f"{name:18} {s['trades']:6d} {s['win']:5.0%} {s['gross_mean']:9.0f} {s['net_mean']:8.0f} {s['net_total']:10.0f} {s['t']:6.2f} "
              f"{s['h1']:9.0f} {s['h2']:9.0f} {s['years_pos']:3d}/{len(s['years'])}  {s['verdict']}")
    print("\nTest 2 net by year (Rs. per lot):")
    yrs = sorted(res["model"]["B1_LONG_CALL"]["years"])
    print(f"{'strategy':18} " + " ".join(f"{y:>7}" for y in yrs))
    for name in STRATS:
        print(f"{name:18} " + " ".join(f"{res['model'][name]['years'].get(y, 0):7.0f}" for y in yrs))
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"res": res, "real_trades": real}, f, indent=1, default=str)


if __name__ == "__main__":
    main()
