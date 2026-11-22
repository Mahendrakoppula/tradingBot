"""Pre-registered trend-following tests on NIFTY and BANKNIFTY. Written 2026-11-22, BEFORE any run.

Why: trend following (time-series momentum) has the longest record of any rule we
reviewed (Moskowitz-Ooi-Pedersen 2012; Hurst-Ooi-Pedersen 2017; Singh et al. 2022 for
India), it trades a few times a year so costs stay small, and it can be traded by BUYING
options, which Mahi prefers. Nothing below may be tuned after a run.

Data: NSE's official daily index closes, 2004 - 13 Nov 2026 (NIFTY 50 = "S&P CNX Nifty"
/ "CNX Nifty" in older files; NIFTY BANK = "Bank Nifty" / "CNX Bank"), India VIX closes,
and the Aug - Nov 2026 F&O bhavcopies for option-volatility calibration.

Signals, decided at a close and acted on at the NEXT day's close
  T1 SMA200_LONG_FLAT      long when the close is above its 200-day average, else flat (daily).
  T2 TSMOM12_LONG_FLAT     at each month-end, long next month if the 252-day return > 0, else flat.
  T3 TSMOM12_LONG_SHORT    as T2, but short when the 252-day return < 0.

Futures version (both indices, 2005 - Nov 2026): P&L = index move while positioned; flat
earns nothing (futures carry is taken to offset interest on idle margin). Costs: 0.11% per
round trip (slippage 0.02% a side, STT 0.05%, charges 0.02%), and one round trip a month
while positioned for the futures roll. Benchmark: buy-and-hold the index, no costs.
Pass: on BOTH indices and in BOTH halves (2005-15, 2016-26), CAGR / |max drawdown| above
buy-and-hold's AND CAGR at least 80% of buy-and-hold's.

Option-buying version (monthly cycles from the first year India VIX is in the data):
  At each monthly expiry close E0, read T2's signal. Up: buy the next monthly expiry's call
  1% in the money; down: buy the put 1% in the money (O1/O2), or stay out (O3/O4, calls
  only). Hold to the next monthly expiry E1 and settle at intrinsic on the official close,
  so only the entry premium is modelled: Black-Scholes, r = 0, IV = India VIX close x a
  multiplier per index and moneyness = median(implied vol / VIX) of that index's options
  with 15-45 days left in the Aug - Nov 2026 bhavcopies (close prices only).
  Monthly expiry: last Thursday up to Aug 2025, last Tuesday from Sep 2025; a non-trading
  day moves to the previous trading day. Lots: NIFTY 65, BANKNIFTY 30.
  Costs: overnight_options.py's rate card on entry; at expiry Rs.20 + GST per leg that
  finishes in the money and STT 0.15% of its intrinsic value.
  O1 NIFTY call/put, O2 BANKNIFTY call/put, O3 NIFTY calls only, O4 BANKNIFTY calls only.
Pass (each): mean net per trade > 0 with t >= 2.6, positive in both halves (split at
2018), and positive in at least 10 calendar years.

    python research/index_trend.py --ic <index_closes dir> --fo <fo_bhavcopy/2026 dir> [--json out.json]
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

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import overnight_options as O  # noqa: E402

NAMES = {"NIFTY": {"s&p cnx nifty", "cnx nifty", "nifty 50"},
         "BANKNIFTY": {"bank nifty", "cnx bank", "cnx bank index", "nifty bank"},
         "VIX": {"india vix"}}
LOTS = {"NIFTY": 65, "BANKNIFTY": 30}
STEPS = {"NIFTY": 50.0, "BANKNIFTY": 100.0}
RT_COST = 0.0011
T_PASS = 2.6


def load_index(ic_dir: str) -> pd.DataFrame:
    rows = defaultdict(dict)
    for f in sorted(glob.glob(os.path.join(ic_dir, "*", "*.csv"))):
        day = dt.date.fromisoformat(os.path.basename(f)[:-4])
        d = pd.read_csv(f)
        nm = d["Index Name"].astype(str).str.strip().str.lower()
        for key, names in NAMES.items():
            r = d[nm.isin(names)]
            if len(r):
                try:
                    rows[day][key] = float(r["Closing Index Value"].iloc[0])
                except ValueError:
                    pass
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


# ---------------- futures version ----------------
def positions(close: pd.Series, rule: str) -> pd.Series:
    """Target position decided at each close (+1/0/-1), before the one-day lag."""
    if rule == "T1":
        sma = close.rolling(200).mean()
        return (close > sma).astype(float).where(sma.notna(), np.nan)
    mom = close / close.shift(252) - 1
    month_end = pd.Series(close.index, index=close.index).map(lambda d: (d.year, d.month))
    is_me = month_end != month_end.shift(-1)
    sig = pd.Series(np.nan, index=close.index)
    sig[is_me] = np.sign(mom[is_me]) if rule == "T3" else (mom[is_me] > 0).astype(float)
    return sig.ffill()


def futures_book(close: pd.Series, rule: str, start: dt.date):
    tgt = positions(close, rule).shift(2)  # decided at close d, traded at close d+1, earns from d+2
    ret = close.pct_change()
    pos = tgt.fillna(0.0)
    keep = pos.index >= start
    pos, ret = pos[keep], ret[keep].fillna(0.0)
    daily = pos * ret
    changes = pos.diff().abs().fillna(pos.abs())
    trades = int((changes > 0).sum())
    month = pd.Series([(d.year, d.month) for d in pos.index], index=pos.index)
    rolls = ((month != month.shift(1)) & (pos != 0)).sum()
    cost = changes * RT_COST / 2  # half a round trip per unit of position changed
    cost += ((month != month.shift(1)) & (pos != 0)).astype(float) * RT_COST
    net = daily - cost
    return net, trades, int(rolls), float((pos != 0).mean())


def curve_stats(daily: pd.Series) -> dict:
    nav = (1 + daily).cumprod()
    yrs = (daily.index[-1] - daily.index[0]).days / 365.25
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    dd = float((nav / nav.cummax() - 1).min())
    return {"cagr": float(cagr), "maxdd": dd, "mar": float(cagr / abs(dd)) if dd else float("nan")}


# ---------------- option-buying version ----------------
def monthly_expiries(trading: list) -> list:
    tset, out, last_day = set(trading), [], max(trading)
    months = sorted({(d.year, d.month) for d in trading})
    for y, m in months:
        last = (dt.date(y, m, 28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        wd = 3 if dt.date(y, m, 1) < dt.date(2025, 9, 1) else 1
        e = last - dt.timedelta(days=(last.weekday() - wd) % 7)
        if e > last_day:
            continue
        while e not in tset and e.month == m:
            e -= dt.timedelta(days=1)
        if e in tset:
            out.append(e)
    return out


def calibrate(fo_dir: str, sym: str, vix: dict) -> dict:
    """median(IV / VIX) by moneyness for options with 15-45 days left, close prices only."""
    cols = ["TradDt", "FinInstrmTp", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "ClsPric", "UndrlygPric"]
    obs = defaultdict(list)
    step = STEPS[sym]
    for f in sorted(glob.glob(os.path.join(fo_dir, "*.csv"))):
        d = pd.read_csv(f, usecols=cols)
        d = d[(d.TckrSymb == sym) & (d.FinInstrmTp == "IDO")]
        if d.empty:
            continue
        t = dt.date.fromisoformat(str(d.TradDt.iloc[0]))
        if t not in vix:
            continue
        d = d.assign(XpryDt=pd.to_datetime(d.XpryDt).dt.date)
        for exp in sorted(d.XpryDt.unique()):
            if not 15 <= (exp - t).days <= 45:
                continue
            spot = O.parity_underlying(d, exp)
            tt = O.years(dt.datetime.combine(t, dt.time(15, 30)), exp)
            for off in (-0.01, 0.0, 0.01):
                k = round(spot * (1 + off) / step) * step
                sides = ("CE", "PE") if off == 0 else (("CE",) if off > 0 else ("PE",))
                ivs = []
                for ot in sides:
                    r = d[(d.XpryDt == exp) & (d.StrkPric == k) & (d.OptnTp == ot)]
                    if len(r) and float(r.ClsPric.iloc[0]) > 0:
                        v = O.implied_vol(float(r.ClsPric.iloc[0]), spot, k, tt, ot)
                        if v:
                            ivs.append(v)
                if ivs:
                    obs[off].append(statistics.mean(ivs) / (vix[t] / 100))
    return {off: statistics.median(v) for off, v in obs.items()}, {off: len(v) for off, v in obs.items()}


def option_book(close: pd.Series, vix: pd.Series, sym: str, mult: dict, calls_only: bool, start: dt.date):
    trading = [d for d in close.index if d >= start]
    exps = monthly_expiries(list(close.index))
    exps = [e for e in exps if e >= start]
    mom = (close / close.shift(252) - 1).shift(1)  # decided on the previous day's close
    lot, step = LOTS[sym], STEPS[sym]
    trades = []
    for e0, e1 in zip(exps, exps[1:]):
        if e0 not in vix.index or pd.isna(vix.get(e0)) or pd.isna(mom.get(e0)):
            continue
        up = mom[e0] > 0
        if not up and calls_only:
            continue
        ot, off = ("CE", -0.01) if up else ("PE", +0.01)
        s0 = close[e0]
        k = round(s0 * (1 + off) / step) * step
        iv = mult[off] * vix[e0] / 100
        t0 = O.years(dt.datetime.combine(e0, dt.time(15, 30)), e1)
        px = max(0.05, O.bs(s0, k, t0, iv, ot))
        intrinsic = max(0.0, close[e1] - k) if ot == "CE" else max(0.0, k - close[e1])
        gross = (intrinsic - px) * lot
        turnover = px * lot
        entry = (O.slip(px) * lot + O.BROKERAGE + turnover * (O.EXCH + O.SEBI + O.STAMP_BUY) / 100
                 + (O.BROKERAGE + turnover * (O.EXCH + O.SEBI) / 100) * O.GST / 100)
        settle = (20.0 * (1 + O.GST / 100) + intrinsic * lot * O.STT_SELL / 100) if intrinsic > 0 else 0.0
        trades.append({"day": e0.isoformat(), "side": ot, "premium": px, "gross": gross, "net": gross - entry - settle})
    return trades


def trade_stats(rows: list, split: str) -> dict:
    nets = [r["net"] for r in rows]
    n = len(nets)
    mean = statistics.mean(nets) if n else 0.0
    sd = statistics.stdev(nets) if n > 1 else 0.0
    yrs = defaultdict(float)
    for r in rows:
        yrs[r["day"][:4]] += r["net"]
    return {"trades": n, "win": sum(x > 0 for x in nets) / n if n else 0.0, "net_mean": mean, "net_total": sum(nets),
            "t": mean / (sd / math.sqrt(n)) if sd else 0.0, "h1": sum(r["net"] for r in rows if r["day"] < split),
            "h2": sum(r["net"] for r in rows if r["day"] >= split), "years_pos": sum(v > 0 for v in yrs.values()),
            "n_years": len(yrs), "worst": min(nets) if n else 0.0, "years": dict(sorted(yrs.items()))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ic", required=True)
    ap.add_argument("--fo", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    px = load_index(a.ic)
    out = {"futures": {}, "options": {}}
    start = dt.date(2005, 1, 3)
    print(f"index data: NIFTY {px.NIFTY.first_valid_index()}, BANKNIFTY {px.BANKNIFTY.first_valid_index()}, "
          f"VIX {px.VIX.first_valid_index()} .. {px.index[-1]}")
    print("\nFutures version, 2005 .. Nov 2026 (net of costs; buy-and-hold has none)")
    print(f"{'index':10} {'rule':20} {'trades':>6} {'in mkt':>6} {'CAGR':>6} {'maxDD':>6} {'MAR':>5} | {'05-15 CAGR/DD':>14} {'16-26 CAGR/DD':>14}")
    for sym in ("NIFTY", "BANKNIFTY"):
        close = px[sym].dropna()
        bh = close.pct_change().fillna(0.0)
        bh = bh[bh.index >= start]
        halves = lambda s: (curve_stats(s[s.index < dt.date(2016, 1, 1)]), curve_stats(s[s.index >= dt.date(2016, 1, 1)]))
        b_all, (b1, b2) = curve_stats(bh), halves(bh)
        out["futures"][f"{sym} buy&hold"] = b_all
        print(f"{sym:10} {'BUY_AND_HOLD':20} {'':6} {'100%':>6} {b_all['cagr']:6.1%} {b_all['maxdd']:6.0%} {b_all['mar']:5.2f} | "
              f"{b1['cagr']:6.1%}/{b1['maxdd']:5.0%}   {b2['cagr']:6.1%}/{b2['maxdd']:5.0%}")
        for rule, label in (("T1", "T1_SMA200_LONG_FLAT"), ("T2", "T2_TSMOM12_LONG_FLAT"), ("T3", "T3_TSMOM12_LONG_SHORT")):
            net, trades, rolls, inmkt = futures_book(close, rule, start)
            s, (h1, h2) = curve_stats(net), halves(net)
            ok = all(h["mar"] > b["mar"] and h["cagr"] >= 0.8 * b["cagr"] for h, b in ((h1, b1), (h2, b2)))
            out["futures"][f"{sym} {label}"] = {**s, "trades": trades, "rolls": rolls, "in_market": inmkt, "h1": h1, "h2": h2, "half_pass": ok}
            print(f"{sym:10} {label:20} {trades:6d} {inmkt:6.0%} {s['cagr']:6.1%} {s['maxdd']:6.0%} {s['mar']:5.2f} | "
                  f"{h1['cagr']:6.1%}/{h1['maxdd']:5.0%}   {h2['cagr']:6.1%}/{h2['maxdd']:5.0%}   {'ok' if ok else 'no'}")
    for label in ("T1_SMA200_LONG_FLAT", "T2_TSMOM12_LONG_FLAT", "T3_TSMOM12_LONG_SHORT"):
        both = out["futures"][f"NIFTY {label}"]["half_pass"] and out["futures"][f"BANKNIFTY {label}"]["half_pass"]
        out["futures"][label + " verdict"] = "PASS" if both else "fail"
        print(f"  {label}: {'PASS' if both else 'fail'} (needs both indices, both halves)")

    vix = px.VIX.dropna()
    vix_d = {d: v for d, v in vix.items()}
    ostart = dt.date(vix.index[0].year + 1, 1, 1)  # a full year of history for the 252-day signal
    print(f"\nOption-buying version, monthly cycles from {ostart} (Rs. per lot)")
    for sym in ("NIFTY", "BANKNIFTY"):
        mult, n = calibrate(a.fo, sym, vix_d)
        print(f"  {sym} IV/VIX multipliers (15-45 days left): " + ", ".join(f"{k:+.0%}: {v:.2f} (n={n[k]})" for k, v in sorted(mult.items())))
        for calls_only, label in ((False, "CALL_OR_PUT"), (True, "CALLS_ONLY")):
            rows = option_book(px[sym].dropna(), vix, sym, mult, calls_only, ostart)
            s = trade_stats(rows, "2018")
            ok = s["net_mean"] > 0 and s["t"] >= T_PASS and s["h1"] > 0 and s["h2"] > 0 and s["years_pos"] >= 10
            s["verdict"] = "PASS" if ok else "fail"
            out["options"][f"{sym} {label}"] = s
            print(f"  {sym:10} {label:12} trades {s['trades']:4d}  win {s['win']:4.0%}  net/trade {s['net_mean']:7.0f}  total {s['net_total']:9.0f}  "
                  f"t {s['t']:5.2f}  halves {s['h1']:8.0f}/{s['h2']:8.0f}  years+ {s['years_pos']}/{s['n_years']}  worst {s['worst']:7.0f}  {s['verdict']}")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(out, fh, indent=1, default=str)


if __name__ == "__main__":
    main()
