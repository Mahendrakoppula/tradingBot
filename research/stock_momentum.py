"""Pre-registered NSE stock momentum test. Written 2026-11-22, BEFORE any run.

Why: momentum is the strongest-evidenced stock rule in India (IIMA factor library,
NSE's Nifty200 Momentum 30 white paper) and ranked first in our research notes. This
tests it the way Mahi would run it: about Rs.2 lakh, 30 stocks, Angel One delivery
orders, monthly or quarterly.

Data (survivorship-free): every daily NSE equity bhavcopy, 1 Jan 2004 - 13 Nov 2026,
EQ series only, so stocks that later crashed, merged or delisted are all in. Returns
are adjusted with NSE's corporate-action file: splits and consolidations (face value
from X to Y -> price x Y/X), bonuses (a:b -> price x b/(a+b)), and cash dividends are
added back on the ex-date (total return). A daily return below -50% or above +100%
after adjustment is treated as a data error (an unparsed action) and set to missing.
Demergers and rights issues are not adjusted (rare; noted as a limitation).

Rules, at the last trading day F of each month (decided on F's close, traded at the
next day's close, so nothing uses a price it could not have had)
  Universe: EQ stocks with close >= Rs.20, at least 240 valid daily returns in the last
    252 days, ranked by median daily traded value over the last 126 days (non-trading
    days count as zero) -> top 200. Built only from data up to F.
  Momentum  = return from day F-251 to day F-21 (12 months, skipping the latest month).
  Volatility = standard deviation of daily returns over the last 252 days.

  M1 MOMENTUM_30        top 30 by momentum, monthly.
  M2 MOM_LOWVOL_30      top 30 by (momentum percentile + low-volatility percentile), monthly.
  M3 MOM_VOL_TARGET     M1, with exposure set monthly to min(1, 15% / M1's realised
                        annualised volatility over the previous 126 days); the rest in
                        cash earning nothing.
  M4 MOM_LOWVOL_QTR     M2 rebalanced only at the end of March, June, September and December.

  Execution: names leaving the top 30 are sold in full; the proceeds are split equally
  across names entering. Continuing names are left alone (no resizing). The first month
  buys 30 names equally.

Costs per Rs.2 lakh account, Angel One delivery: brokerage 0.1% capped at Rs.20 per
order; STT 0.1% on buys and sells; stamp duty 0.015% on buys; exchange 0.00297%; SEBI
0.0001%; GST 18% on brokerage + exchange + SEBI; slippage 0.1% each way; DP charge
Rs.23.60 per stock sold.

Data note, added after the first run (a correction of what counts as a stock, not a rule
change): the EQ series also carries exchange-traded funds, and liquid and gold ETFs have
large traded values and near-zero volatility, so they entered the universe and the
low-volatility picks (the first run's latest M2 list held LIQUIDBEES and LIQUIDCASE).
ETFs are now excluded by symbol pattern, by the fund names in the 2024+ files, and by an
annualised volatility below 8% (no operating company in a top-200 universe is that calm).
The NIFTY 50 reference also failed to load before 2016 because the index was called
"S&P CNX Nifty" / "CNX Nifty" then. First-run numbers are kept in the commit message.

Benchmarks: EW200 - the same top-200 universe, equal weight, same execution rule, NO
costs (a deliberately tough hurdle); NIFTY 50 price index (no dividends; reference only).

Holding window: January 2006 - 13 November 2026.

Pass bar, per strategy, on monthly net returns minus EW200
  1. mean excess > 0 with t >= 2.6;
  2. mean excess > 0 in 2006-2015 AND in 2016-2026;
  3. beats EW200 in at least 12 of the 21 calendar years (2026 partial).

    python research/stock_momentum.py --bhav <bhavcopy dir> --ca <corporate_actions dir> \
        --ic <index_closes dir> [--json out.json]
"""
import argparse
import datetime as dt
import glob
import json
import math
import os
import re
import statistics
from collections import defaultdict

import numpy as np
import pandas as pd

START, END = dt.date(2004, 1, 1), dt.date(2026, 11, 13)
FIRST_FORMATION = dt.date(2005, 12, 30)
TOP_N, UNIVERSE_N, MIN_PRICE = 30, 200, 20.0
CAPITAL = 200_000.0
BROKER_PCT, BROKER_CAP = 0.1, 20.0
STT, STAMP_BUY, EXCH, SEBI, GST, SLIP, DP = 0.1, 0.015, 0.00297, 0.0001, 18.0, 0.1, 23.60
T_PASS = 2.6


# ---------------- data ----------------
ETF_SYMBOL = re.compile(r"BEES|ETF|LIQUID|NIFTY|SENSEX|BANKEX|GILT|CASE$|^N100$|^NV20|^MON100$|^MAFANG$|^HNGSNG"
                        r"|GOLDSHARE|^QGOLD|^KOTAKGOLD$|^AXISGOLD$|^IDBIGOLD$|^RELGOLD$|^SBIGETS$|^GOLD1$")
ETF_NAME = re.compile(r"ETF|BEES|\bAMC\b|AMC\s*-|MUTUAL|-GR\b|\bFUND\b", re.I)
ETF_NAMES: set = set()


def load_bhav(bhav_dir: str) -> pd.DataFrame:
    parts = []
    for f in sorted(glob.glob(os.path.join(bhav_dir, "*", "*.csv"))):
        day = dt.date.fromisoformat(os.path.basename(f)[:-4])
        if not (START <= day <= END):
            continue
        d = pd.read_csv(f)
        if "SYMBOL" in d.columns:
            d = d[d.SERIES.astype(str).str.strip() == "EQ"]
            d = pd.DataFrame({"sym": d.SYMBOL.astype(str).str.strip(), "close": d.CLOSE, "prev": d.PREVCLOSE, "value": d.TOTTRDVAL})
        else:
            d = d[d.SctySrs.astype(str).str.strip() == "EQ"]
            names = d.FinInstrmNm.astype(str)
            ETF_NAMES.update(d.TckrSymb[names.str.contains(ETF_NAME)].astype(str).str.strip())
            d = pd.DataFrame({"sym": d.TckrSymb.astype(str).str.strip(), "close": d.ClsPric, "prev": d.PrvsClsgPric, "value": d.TtlTrfVal})
        d["date"] = day
        parts.append(d)
    out = pd.concat(parts, ignore_index=True)
    return out.drop_duplicates(["date", "sym"], keep="last")


_NUM = r"(\d+(?:\.\d+)?)"


def parse_actions(ca_dir: str) -> dict:
    """{(symbol, ex_date): (price_factor, dividend_rs)} from NSE's corporate-action files."""
    acts = defaultdict(lambda: [1.0, 0.0])
    seen = set()
    for f in glob.glob(os.path.join(ca_dir, "*", "*.json")):
        for r in json.load(open(f)):
            s, sym, ex = (r.get("subject") or ""), (r.get("symbol") or "").strip(), r.get("exDate")
            if not sym or not ex or ex == "-":
                continue
            try:
                exd = dt.datetime.strptime(ex, "%d-%b-%Y").date()
            except ValueError:
                continue
            low = " ".join(s.lower().split())
            if (sym, exd, low) in seen:  # the same action can be listed in several daily files
                continue
            seen.add((sym, exd, low))
            a = acts[(sym, exd)]
            for m in re.finditer(r"bonus\s*" + _NUM + r"\s*:\s*" + _NUM, low):
                x, y = float(m.group(1)), float(m.group(2))
                if x > 0 and y > 0:
                    a[0] *= y / (x + y)
            if re.search(r"split|sub-division|sub division|consolidat", low):
                nums = [float(n) for n in re.findall(r"r[se]\.?\s*" + _NUM, low)]
                if len(nums) >= 2 and nums[0] > 0 and nums[1] > 0:
                    a[0] *= nums[1] / nums[0]
            for m in re.finditer(r"dividend[^;/]*?r[se]\.?\s*" + _NUM, low):
                a[1] += float(m.group(1))
            for m in re.finditer(r"dividend\s*-?\s*" + _NUM + r"\s*%", low):
                try:
                    a[1] += float(m.group(1)) / 100 * float(r.get("faceVal") or 0)
                except ValueError:
                    pass
    return {k: tuple(v) for k, v in acts.items()}


def build_panels(bhav: pd.DataFrame, acts: dict):
    key = list(zip(bhav.sym, bhav.date))
    fac = np.array([acts.get(k, (1.0, 0.0))[0] for k in key])
    div = np.array([acts.get(k, (1.0, 0.0))[1] for k in key])
    prev = bhav.prev.to_numpy(dtype=float) * fac
    r = (bhav.close.to_numpy(dtype=float) + div) / prev - 1
    r[(prev <= 0) | ~np.isfinite(r) | (r < -0.5) | (r > 1.0)] = np.nan
    bhav = bhav.assign(r=r)
    R = bhav.pivot(index="date", columns="sym", values="r").sort_index()
    V = bhav.pivot(index="date", columns="sym", values="value").reindex(R.index)
    C = bhav.pivot(index="date", columns="sym", values="close").reindex(R.index)
    flagged = int(np.isnan(r).sum() - (prev <= 0).sum())
    return R, V, C, flagged


# ---------------- strategy ----------------
def month_ends(dates: list) -> list:
    out = []
    for a, b in zip(dates, dates[1:]):
        if (a.year, a.month) != (b.year, b.month):
            out.append(a)
    return out


def leg_rates(value: float) -> tuple[float, float]:
    """(buy cost, sell cost) as a fraction of the traded value, excluding the DP charge."""
    brok = min(BROKER_PCT / 100 * value, BROKER_CAP) / value if value > 0 else 0.0
    common = brok + STT / 100 + EXCH / 100 + SEBI / 100 + (brok + EXCH / 100 + SEBI / 100) * GST / 100 + SLIP / 100
    return common + STAMP_BUY / 100, common


def select(Rv, Vv, Cv, i, mode, syms, not_etf):
    """Top-30 picks and the top-200 universe at calendar index i (data up to i only)."""
    win = Rv[i - 251:i + 1]
    valid = (np.sum(~np.isnan(win), axis=0) >= 240) & not_etf
    with np.errstate(invalid="ignore"):
        valid &= np.nan_to_num(np.nanstd(win, axis=0) * math.sqrt(252), nan=0.0) >= 0.08
    price_ok = np.nan_to_num(Cv[i], nan=0.0) >= MIN_PRICE
    liq = np.median(np.nan_to_num(Vv[i - 125:i + 1], nan=0.0), axis=0)
    elig = np.where(valid & price_ok)[0]
    if len(elig) < TOP_N:
        return [], []
    uni = elig[np.argsort(-liq[elig])[:UNIVERSE_N]]
    mom = np.prod(1 + np.nan_to_num(Rv[i - 251:i - 20, uni], nan=0.0), axis=0) - 1
    if mode == "mom":
        score = mom
    else:
        vol = np.nanstd(win[:, uni], axis=0)
        score = pd.Series(mom).rank(pct=True).to_numpy() + pd.Series(-vol).rank(pct=True).to_numpy()
    picks = uni[np.argsort(-score)[:TOP_N]]
    return [syms[j] for j in picks], [syms[j] for j in uni]


def run_book(R: pd.DataFrame, sched: dict, with_costs: bool):
    """Daily NAV returns for a book that, on each formation index f in sched, trades at the
    close of f+1 into the listed names (exits sold, proceeds split across entries)."""
    dates = list(R.index)
    Rv = R.to_numpy()
    col = {s: j for j, s in enumerate(R.columns)}
    hold = {}  # sym -> value as a fraction of NAV
    daily = np.zeros(len(dates))
    costs = np.zeros(len(dates))
    orders = 0
    cost_frac_total = 0.0
    fidx = sorted(sched)
    for n, f in enumerate(fidx):
        t = f + 1  # trade day
        if t >= len(dates):
            break
        target = sched[f]
        if not target:
            continue
        exits = [s for s in hold if s not in target]
        enters = [s for s in target if s not in hold]
        nav = sum(hold.values()) if hold else 1.0
        cost = 0.0
        if not hold:
            per = 1.0 / len(target)
            for s in target:
                hold[s] = per
                if with_costs:
                    b, _ = leg_rates(per * CAPITAL)
                    cost += per * b
            orders += len(target)
        else:
            proceeds = 0.0
            for s in exits:
                v = hold.pop(s)
                if with_costs:
                    _, sl = leg_rates(v * CAPITAL)
                    cost += v * sl + DP / CAPITAL
                proceeds += v
            orders += len(exits) + len(enters)
            if enters:
                per = proceeds / len(enters)
                for s in enters:
                    hold[s] = per
                    if with_costs:
                        b, _ = leg_rates(per * CAPITAL)
                        cost += per * b
            elif proceeds:  # nothing to buy: spread across holdings
                tot = sum(hold.values())
                for s in hold:
                    hold[s] += proceeds * hold[s] / tot
        # normalise to NAV 1 after costs; cost is charged on the trade day
        tot = sum(hold.values())
        for s in hold:
            hold[s] = hold[s] / tot
        cost_frac_total += cost
        costs[t] += cost
        end = fidx[n + 1] + 1 if n + 1 < len(fidx) else len(dates) - 1
        for d in range(t + 1, end + 1):
            rr = {s: Rv[d, col[s]] for s in hold}
            pr = sum(hold[s] * (0.0 if math.isnan(rr[s]) else rr[s]) for s in hold)
            daily[d] += pr
            for s in hold:
                hold[s] *= 1 + (0.0 if math.isnan(rr[s]) else rr[s])
            tot = sum(hold.values())
            for s in hold:
                hold[s] /= tot
    return pd.Series(daily, index=dates), pd.Series(costs, index=dates), orders


def vol_target(ret: pd.Series, cost: pd.Series, formations: list, dates: list):
    """Scale M1 to a 15% volatility target, monthly, from M1's own past returns."""
    out_r, out_c = ret * 0.0, cost * 0.0
    exposures, prev_e = [], 0.0
    fset = sorted(formations)
    for n, f in enumerate(fset):
        start, end = f + 1, (fset[n + 1] + 1 if n + 1 < len(fset) else len(dates) - 1)
        hist = ret.iloc[max(0, f - 125):f + 1]
        hist = hist[hist != 0]
        vol = hist.std() * math.sqrt(252) if len(hist) > 60 else float("nan")
        e = min(1.0, 0.15 / vol) if vol == vol and vol > 0 else 1.0
        exposures.append(e)
        out_r.iloc[start + 1:end + 1] = ret.iloc[start + 1:end + 1] * e
        out_c.iloc[start] += cost.iloc[start] * e + abs(e - prev_e) * 0.0032  # resize cost
        prev_e = e
    return out_r, out_c, exposures


# ---------------- stats ----------------
def monthly(daily: pd.Series) -> pd.Series:
    idx = pd.to_datetime(pd.Index(daily.index))
    key = idx.year * 100 + idx.month
    return (1 + pd.Series(daily.to_numpy(), index=idx)).groupby(key).prod() - 1


def stats(daily: pd.Series, bench_m: pd.Series) -> dict:
    m = monthly(daily)
    m = m[m.index >= 200601]
    b = bench_m.reindex(m.index)
    ex = (m - b).dropna()
    nav = (1 + m).cumprod()
    years = len(m) / 12
    yr = (1 + m).groupby(m.index // 100).prod() - 1
    byr = (1 + b).groupby(b.index // 100).prod() - 1
    return {
        "cagr": nav.iloc[-1] ** (1 / years) - 1, "vol": m.std() * math.sqrt(12),
        "maxdd": float((nav / nav.cummax() - 1).min()), "worst_month": float(m.min()),
        "ex_mean_m": float(ex.mean()), "ex_t": float(ex.mean() / (ex.std() / math.sqrt(len(ex)))),
        "ex_h1": float(ex[ex.index < 201601].mean()), "ex_h2": float(ex[ex.index >= 201601].mean()),
        "years_beat": int(sum(yr[y] > byr[y] for y in yr.index)), "n_years": int(len(yr)),
        "by_year": {int(y): float(yr[y]) for y in yr.index},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bhav", required=True)
    ap.add_argument("--ca", required=True)
    ap.add_argument("--ic", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    bhav = load_bhav(a.bhav)
    acts = parse_actions(a.ca)
    R, V, C, flagged = build_panels(bhav, acts)
    dates = list(R.index)
    print(f"stock-days {len(bhav):,}  symbols {R.shape[1]:,}  days {len(dates):,}  ({dates[0]} .. {dates[-1]})")
    print(f"corporate actions parsed {len(acts):,}; returns set missing as data errors: {flagged:,}")
    Rv, Vv, Cv, syms = R.to_numpy(), V.to_numpy(), C.to_numpy(), list(R.columns)
    di = {d: k for k, d in enumerate(dates)}
    not_etf = np.array([not (ETF_SYMBOL.search(x) or x in ETF_NAMES) for x in syms])
    print(f"ETFs excluded by symbol/name: {int((~not_etf).sum())}")
    forms = [di[d] for d in month_ends(dates) if d >= FIRST_FORMATION]
    picks = {"mom": {}, "mlv": {}}
    uni = {}
    for f in forms:
        p1, u = select(Rv, Vv, Cv, f, "mom", syms, not_etf)
        p2, _ = select(Rv, Vv, Cv, f, "mlv", syms, not_etf)
        picks["mom"][f], picks["mlv"][f], uni[f] = p1, p2, u
    qtr = {f: picks["mlv"][f] for f in forms if dates[f].month in (3, 6, 9, 12)}
    first = forms[0]
    if first not in qtr:
        qtr[first] = picks["mlv"][first]
    books = {}
    r1, c1, o1 = run_book(R, picks["mom"], True)
    books["M1_MOMENTUM_30"] = (r1 - c1, o1)
    r2, c2, o2 = run_book(R, picks["mlv"], True)
    books["M2_MOM_LOWVOL_30"] = (r2 - c2, o2)
    r3, c3, exps = vol_target(r1, c1, forms, dates)
    books["M3_MOM_VOL_TARGET"] = (r3 - c3, o1)
    r4, c4, o4 = run_book(R, qtr, True)
    books["M4_MOM_LOWVOL_QTR"] = (r4 - c4, o4)
    ew_r, _, _ = run_book(R, uni, False)
    ew = (ew_r,)
    ew_m = monthly(ew_r)
    # NIFTY 50 price index
    nifty = {}
    for f in sorted(glob.glob(os.path.join(a.ic, "*", "*.csv"))):
        d = pd.read_csv(f)
        n = d[d["Index Name"].astype(str).str.strip().str.lower().isin(["nifty 50", "cnx nifty", "s&p cnx nifty"])]
        if len(n):
            nifty[dt.date.fromisoformat(os.path.basename(f)[:-4])] = float(n["Closing Index Value"].iloc[0])
    ns = pd.Series(nifty).sort_index()
    nd = ns.pct_change().reindex(dates).fillna(0.0)
    res = {"EW200 (no costs)": stats(ew[0], ew_m), "NIFTY 50 price": stats(nd, ew_m)}
    print(f"\nholding Jan 2006 .. {dates[-1]}; universe top {UNIVERSE_N} by traded value; {TOP_N} stocks; Rs.{CAPITAL:,.0f} account")
    print(f"{'book':20} {'orders':>7} {'CAGR':>6} {'vol':>5} {'maxDD':>6} {'worst m':>8} {'ex/mo':>6} {'t':>5} {'06-15':>6} {'16-26':>6} {'yrs>EW':>7}  verdict")
    for name, (daily, orders) in books.items():
        s = stats(daily, ew_m)
        ok = s["ex_mean_m"] > 0 and s["ex_t"] >= T_PASS and s["ex_h1"] > 0 and s["ex_h2"] > 0 and s["years_beat"] >= 12
        s["verdict"], s["orders"] = ("PASS" if ok else "fail"), orders
        res[name] = s
        print(f"{name:20} {orders:7d} {s['cagr']:6.1%} {s['vol']:5.0%} {s['maxdd']:6.0%} {s['worst_month']:8.1%} {s['ex_mean_m']:6.2%} "
              f"{s['ex_t']:5.2f} {s['ex_h1']:6.2%} {s['ex_h2']:6.2%} {s['years_beat']:3d}/{s['n_years']}  {s['verdict']}")
    for name in ("EW200 (no costs)", "NIFTY 50 price"):
        s = res[name]
        print(f"{name:20} {'':7} {s['cagr']:6.1%} {s['vol']:5.0%} {s['maxdd']:6.0%} {s['worst_month']:8.1%}")
    print(f"\nM3 average exposure {statistics.mean(exps):.2f} (min {min(exps):.2f})")
    print("\ncalendar-year returns:")
    yrs = sorted(res["EW200 (no costs)"]["by_year"])
    print(f"{'book':20} " + " ".join(f"{y % 100:>5}" for y in yrs))
    for name in list(books) + ["EW200 (no costs)", "NIFTY 50 price"]:
        print(f"{name:20} " + " ".join(f"{res[name]['by_year'].get(y, 0) * 100:5.0f}" for y in yrs))
    last = forms[-1]
    print(f"\nlatest M2 picks (formed {dates[last]}): {', '.join(picks['mlv'][last])}")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"res": res, "latest_m1": picks["mom"][last], "latest_m2": picks["mlv"][last], "formed": str(dates[last])}, fh, indent=1, default=str)


if __name__ == "__main__":
    main()
