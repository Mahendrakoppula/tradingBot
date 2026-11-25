"""Pre-registered test: BUY index straddles only when options look cheap. Written 2026-11-25,
BEFORE any run.

Why: Goyal & Saretto (Journal of Financial Economics 2009) found that buying straddles on
stocks whose at-the-money implied volatility sits well below their recent realised
volatility earned significant monthly returns. Mahi prefers buying options; this is the
one buy-side rule with top-journal support that our data can test on NIFTY and BANKNIFTY.

Rule (one trade per weekly cycle, only when the signal is on)
  * At each weekly expiry close E0, compare the implied volatility the next weekly
    straddle is priced at (IV) with the index's realised volatility over the last 20
    trading days (RV20, close-to-close, annualised).
  * CHEAP when IV < RV20: buy the ATM straddle of the next weekly expiry E1, hold to E1,
    settle at intrinsic on the official close (only the entry price is modelled).
  * The not-cheap cycles are reported alongside for contrast; they are not a test.

Pricing and data
  * IV = India VIX close on E0 x the ATM multiplier from real option close prices
    (NIFTY: weekly options with 5+ days left, overnight_options.py's calibration;
    BANKNIFTY: monthly options with 15-45 days left, index_trend.py's - it has no
    weeklies in 2026), Black-Scholes with r = 0. Official index closes and VIX from NSE.
  * Weekly expiries: Thursday, Tuesday for NIFTY from Sep 2025; holiday -> previous
    trading day. NIFTY: 2011 - Nov 2026 (weeklies only listed from Feb 2019: earlier
    cycles are a model of what a 7-day straddle would have cost). BANKNIFTY: 2011 -
    Nov 2024, when its weeklies ended.
  * Lots: NIFTY 65, BANKNIFTY 30. Costs as in weekly_options.py (entry slippage max(Rs.0.50,
    0.5%), brokerage, charges; Rs.20 + GST and 0.15% STT on intrinsic for a leg that
    settles in the money).

Pass bar (each index): CHEAP trades have mean net > 0 with t >= 2.6, are positive in both
halves (before / from 2019) and positive in at least 9 calendar years.

    python research/cheap_vol_straddles.py --ic <index_closes dir> --fo <fo_bhavcopy/2026 dir>
"""
import argparse
import datetime as dt
import math
import os
import statistics
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import index_trend as T  # noqa: E402
import overnight_options as O  # noqa: E402

T_PASS = 2.6


def weekly_expiries(trading: list, sym: str, end: dt.date) -> list:
    tset, out = set(trading), []
    d = trading[0]
    while d <= min(end, trading[-1]):
        wd = 1 if (sym == "NIFTY" and d >= dt.date(2025, 9, 1)) else 3
        e = d + dt.timedelta(days=(wd - d.weekday()) % 7)
        x = e
        while x not in tset and x > d - dt.timedelta(days=1):
            x -= dt.timedelta(days=1)
        if x in tset and (not out or x > out[-1]) and x <= trading[-1]:
            out.append(x)
        d = e + dt.timedelta(days=1)
    return out


def run(px, sym: str, mult_atm: float, start: dt.date, end: dt.date):
    close = px[sym].dropna()
    vix = px["VIX"].dropna()
    days = list(close.index)
    logret = np.log(close / close.shift(1))
    rv20 = logret.rolling(20).std() * math.sqrt(252)
    exps = [e for e in weekly_expiries(days, sym, end) if e >= start]
    lot, step = T.LOTS[sym], T.STEPS[sym]
    rows = []
    for e0, e1 in zip(exps, exps[1:]):
        if e1 > end or e0 not in vix.index or np.isnan(rv20.get(e0, np.nan)):
            continue
        s0 = close[e0]
        iv = mult_atm * vix[e0] / 100
        k = round(s0 / step) * step
        t0 = O.years(dt.datetime.combine(e0, dt.time(15, 30)), e1)
        gross = cost = 0.0
        for ot in ("CE", "PE"):
            prem = max(0.05, O.bs(s0, k, t0, iv, ot))
            intrinsic = max(0.0, close[e1] - k) if ot == "CE" else max(0.0, k - close[e1])
            gross += (intrinsic - prem) * lot
            turnover = prem * lot
            cost += (O.slip(prem) * lot + O.BROKERAGE + turnover * (O.EXCH + O.SEBI + O.STAMP_BUY) / 100
                     + (O.BROKERAGE + turnover * (O.EXCH + O.SEBI) / 100) * O.GST / 100)
            if intrinsic > 0:
                cost += 20.0 * (1 + O.GST / 100) + intrinsic * lot * O.STT_SELL / 100
        rows.append({"day": e0.isoformat(), "cheap": iv < rv20[e0], "iv": iv, "rv": float(rv20[e0]), "gross": gross, "net": gross - cost})
    return rows


def stats(rows):
    nets = [r["net"] for r in rows]
    n = len(nets)
    if n < 3:
        return {"trades": n}
    mean, sd = statistics.mean(nets), statistics.stdev(nets)
    yrs = {}
    for r in rows:
        yrs[r["day"][:4]] = yrs.get(r["day"][:4], 0.0) + r["net"]
    return {"trades": n, "win": sum(x > 0 for x in nets) / n, "mean": mean, "total": sum(nets), "t": mean / (sd / math.sqrt(n)),
            "h1": sum(r["net"] for r in rows if r["day"] < "2019"), "h2": sum(r["net"] for r in rows if r["day"] >= "2019"),
            "years_pos": sum(v > 0 for v in yrs.values()), "n_years": len(yrs), "years": yrs}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ic", required=True)
    ap.add_argument("--fo", required=True)
    a = ap.parse_args()
    px = T.load_index(a.ic)
    vix_d = {d: v for d, v in px.VIX.dropna().items()}
    _, mult_n, _ = O.test_real(O.load_fo(a.fo), vix_d)
    m_nifty = mult_n[("+0%", "5+")]
    m_bank = T.calibrate(a.fo, "BANKNIFTY", vix_d)[0][0.0]
    print(f"ATM IV / India VIX multipliers: NIFTY weekly {m_nifty:.2f}, BANKNIFTY monthly {m_bank:.2f}")
    for sym, m, end in (("NIFTY", m_nifty, dt.date(2026, 11, 13)), ("BANKNIFTY", m_bank, dt.date(2024, 11, 30))):
        rows = run(px, sym, m, dt.date(2011, 1, 1), end)
        cheap = [r for r in rows if r["cheap"]]
        dear = [r for r in rows if not r["cheap"]]
        s, c = stats(cheap), stats(dear)
        ok = s.get("trades", 0) >= 3 and s["mean"] > 0 and s["t"] >= T_PASS and s["h1"] > 0 and s["h2"] > 0 and s["years_pos"] >= 9
        print(f"\n{sym} weekly ATM straddles, {rows[0]['day']} .. {rows[-1]['day']} (Rs. per lot of {T.LOTS[sym]}); all cycles {len(rows)}")
        for label, x in (("CHEAP  (IV < RV20) - the test", s), ("not cheap (contrast)", c)):
            print(f"  {label:30} trades {x['trades']:4d}  win {x['win']:4.0%}  net/trade {x['mean']:7.0f}  total {x['total']:10.0f}  "
                  f"t {x['t']:5.2f}  before-2019 {x['h1']:9.0f}  2019+ {x['h2']:9.0f}  years+ {x['years_pos']}/{x['n_years']}")
        print(f"  verdict: {'PASS' if ok else 'fail'}")
        print("  CHEAP by year: " + ", ".join(f"{y} {v:,.0f}" for y, v in sorted(s["years"].items())))


if __name__ == "__main__":
    main()
