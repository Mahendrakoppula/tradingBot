"""Funnel diagnostic: replay real historical NIFTY 1m bars through the SAME
PaperLoop + pipeline the live engine runs, and report where every setup dies.

Why this exists: the live journal (Postgres on the instance) is the primary
evidence, but it only holds a few hundred signals and is not reachable from a
development box. A year of real 1m history gives thousands of pre-signal
setups, so a gate that blocks everything shows up as a number, not a hunch.

Inputs: a CSV with columns Instrument,Date(dd-mm-yyyy),Time(H:MM:SS),Open,High,
Low,Close (the public sandeepkapri/Nifty50-Minute-Data layout). Index bars carry
no volume, and the live engine reads volume (relative volume, session VWAP) from
the near-month future. A synthetic proxy is built from each 1m bar's range -
futures volume and bar range are strongly correlated and both U-shaped through
the day - so VWAP and relative volume exist. It is an approximation and is
reported as such; run with --no-volume to see the gates without it.

Option prices come from backtest.ModelChainService (Black-Scholes from spot), so
fills/P&L are for plumbing questions only, never evidence of edge (spec 52/83).

    python -m research.funnel_diagnostic --csv PATH --from 2023-06-01 --to 2024-03-27
"""
import argparse
import collections
import csv
import datetime as dt
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from trading_bot.engine.candles import Candle  # noqa: E402
from trading_bot.timeutil import IST  # noqa: E402

SPOT_TOKEN = "SPOT"
PROXY_TOKEN = "FUTPROXY"
LOTS = {"NIFTY": 65, "BANKNIFTY": 35, "SENSEX": 20}  # current exchange lot sizes (2026)


def load_env_file(path: Path) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


def read_1m(csv_path: Path, start: dt.date, end: dt.date) -> dict[dt.date, list[tuple]]:
    """Two layouts: the public dataset (Instrument,Date,Time,Open,High,Low,Close) and this repo's own
    research/fetch_historical.py output (timestamp,open,high,low,close,volume, ISO timestamps from
    SmartAPI getCandleData) - so the same replay can run on your own recent 1m pulls."""
    days: dict[dt.date, list[tuple]] = collections.defaultdict(list)
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            if "timestamp" in row:
                ts = dt.datetime.fromisoformat(row["timestamp"])
                d, t = ts.date(), dt.time(ts.hour, ts.minute)
                o, h_, l_, c_ = (float(row[k]) for k in ("open", "high", "low", "close"))
            else:
                d = dt.datetime.strptime(row["Date"], "%d-%m-%Y").date()
                hh, mm, _s = (int(x) for x in row["Time"].split(":"))
                t = dt.time(hh, mm)
                o, h_, l_, c_ = (float(row[k]) for k in ("Open", "High", "Low", "Close"))
            if d < start or d > end:
                continue
            if t < dt.time(9, 15) or t >= dt.time(15, 30):
                continue
            days[d].append((t, o, h_, l_, c_))
    for d in days:
        days[d].sort()
    return dict(sorted(days.items()))


def _bucket(t: dt.time, minutes: int) -> dt.time:
    k = ((t.hour * 60 + t.minute) - (9 * 60 + 15)) // minutes
    total = 9 * 60 + 15 + k * minutes
    return dt.time(total // 60, total % 60)


def _agg(day: dt.date, rows: list[Candle], minutes: int) -> list[Candle]:
    groups: dict[dt.time, list[Candle]] = collections.OrderedDict()
    for c in rows:
        groups.setdefault(_bucket(c.ts.time(), minutes), []).append(c)
    out = []
    for t, cs in groups.items():
        out.append(Candle(ts=dt.datetime.combine(day, t, tzinfo=IST), open=cs[0].open, high=max(c.high for c in cs),
                          low=min(c.low for c in cs), close=cs[-1].close, volume=sum(c.volume for c in cs),
                          complete=True, source="replay"))
    return out


def build_data(days: dict[dt.date, list[tuple]], with_volume: bool) -> dict:
    spot = {"1m": [], "5m": [], "30m": [], "1d": []}
    proxy = {"1m": [], "5m": [], "30m": [], "1d": []}
    for day, rows in days.items():
        s1, p1 = [], []
        for (t, o, h, l, c) in rows:
            ts = dt.datetime.combine(day, t, tzinfo=IST)
            s1.append(Candle(ts=ts, open=o, high=h, low=l, close=c, volume=0, complete=True, source="replay"))
            # synthetic proxy volume: proportional to the bar's range (floor so a flat bar still trades)
            v = max(50, int(round((h - l) * 100)))
            p1.append(Candle(ts=ts, open=o, high=h, low=l, close=c, volume=v, complete=True, source="replay"))
        for store, one in ((spot, s1), (proxy, p1)):
            store["1m"].extend(one)
            store["5m"].extend(_agg(day, one, 5))
            store["30m"].extend(_agg(day, one, 30))
            store["1d"].append(Candle(ts=one[0].ts, open=one[0].open, high=max(c.high for c in one),
                                      low=min(c.low for c in one), close=one[-1].close,
                                      volume=sum(c.volume for c in one), complete=True, source="replay"))
    data = {SPOT_TOKEN: spot}
    if with_volume:
        data[PROXY_TOKEN] = proxy
    return data


def underlying_outcome(bars: list[tuple], start: dt.time, direction: str, entry: float, stop: float, target: float,
                       last: dt.time = dt.time(15, 20)) -> tuple[float, str]:
    """What the UNDERLYING did after a decision, in R (risk = |entry - stop|): walk the 1m bars from
    `start`; stop first if both are touched in one bar (conservative); flat at the `last` bar's close
    if neither. Underlying-only - no theta, spread or costs - so it ranks setups, it is not P&L."""
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0, "degenerate"
    up = direction == "up"
    close = entry
    for (t, o, h, l, c) in bars:
        if t < start:
            continue
        if t > last:
            break
        if (l <= stop) if up else (h >= stop):
            return -1.0, "stop"
        if (h >= target) if up else (l <= target):
            return abs(target - entry) / risk, "target"
        close = c
    return ((close - entry) if up else (entry - close)) / risk, "eod"


class IndexedSource:
    """CandleSource drop-in that slices sorted lists with bisect instead of
    scanning every bar for every day (O(days x bars) -> O(days x log bars))."""

    def __init__(self, data: dict):
        import bisect
        self._bisect = bisect
        self.data = data
        self.keys = {tok: {tf: [c.ts for c in cs] for tf, cs in tfs.items()} for tok, tfs in data.items()}

    def load(self, token, tf, since, until):
        cs = self.data.get(token, {}).get(tf, [])
        ks = self.keys.get(token, {}).get(tf, [])
        return cs[self._bisect.bisect_left(ks, since):self._bisect.bisect_left(ks, until)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--underlying", default="NIFTY", choices=sorted(LOTS))
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--warmup-days", type=int, default=420, help="calendar days of history loaded before --from")
    ap.add_argument("--env", default=str(REPO / "deploy" / "config.env"), help="config file to load (production by default)")
    ap.add_argument("--set", action="append", default=[], help="KEY=VALUE override on top of --env (repeatable)")
    ap.add_argument("--no-volume", action="store_true", help="no synthetic futures-volume proxy")
    ap.add_argument("--spread-pct", type=float, default=1.0,
                    help="model option spread, %% of mid (1.0 = backtest default, conservative; real NIFTY ATM is ~0.1-0.3)")
    ap.add_argument("--json", help="write the full report as JSON here")
    args = ap.parse_args(argv)

    load_env_file(Path(args.env))
    os.environ.update({"TECH_MODE": "BACKTEST", "TECH_DATABASE_URL": "", "TECH_UNDERLYINGS": args.underlying})
    for kv in args.set:
        k, v = kv.split("=", 1)
        os.environ[k] = v

    import logging
    logging.disable(logging.WARNING)
    from trading_bot.engine import jsonlog
    jsonlog.event = lambda *a, **k: None  # the per-event JSON log is noise for a 200-day replay

    from trading_bot.engine.config import EngineConfig
    from trading_bot.engine.db.memory import MemoryDAL
    from trading_bot.engine.pipeline import PipelineParams
    from trading_bot.engine.research.backtest import DEFAULT_SPECS, ModelChainParams, run_backtest
    from trading_bot.engine.warmup import Instrument

    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    t0 = time.time()
    days = read_1m(Path(args.csv), start - dt.timedelta(days=args.warmup_days), end)
    data = build_data(days, with_volume=not args.no_volume)
    print(f"loaded {len(days)} sessions in {time.time() - t0:.1f}s", file=sys.stderr)

    cfg = EngineConfig.from_env()
    pipeline = PipelineParams.from_config(cfg)
    u = args.underlying
    insts = [Instrument(u, "BSE" if u == "SENSEX" else "NSE", SPOT_TOKEN, "spot")]
    if not args.no_volume:
        insts.append(Instrument(u, "BFO" if u == "SENSEX" else "NFO", PROXY_TOKEN, "volume_proxy"))
    specs = {u: dict(DEFAULT_SPECS[u], lot=LOTS[u])}
    dal = MemoryDAL()

    # capture every pipeline decision (rejected or not) so its underlying outcome can be measured
    from trading_bot.engine import paper_loop
    captured: list = []
    real_decide = paper_loop.decide

    def spy(ctx, event, **kw):
        d = real_decide(ctx, event, **kw)
        captured.append((ctx, d))
        return d
    paper_loop.decide = spy
    t0 = time.time()
    rep = run_backtest(cfg, insts, IndexedSource(data), start, end, pipeline=pipeline, specs=specs,
                       history_days=args.warmup_days, dal=dal, profile=cfg.paper_profile,
                       chain_params=ModelChainParams(spread_pct=args.spread_pct))
    took = time.time() - t0

    # --- pre-signal funnel -------------------------------------------------------------------
    ev = dal.presignal_events
    to_stage = collections.Counter(e["to_stage"] for e in ev)
    term = collections.Counter(f"{e['to_stage']}:{e['reason_code']}" for e in ev
                               if e["to_stage"] in ("EXPIRED", "REJECTED", "EXTENDED", "EXHAUSTED"))
    from_stage_term = collections.Counter(f"{e['from_stage']}->{e['to_stage']}" for e in ev
                                          if e["to_stage"] in ("EXPIRED", "REJECTED", "EXTENDED", "EXHAUSTED"))
    # --- pipeline funnel ----------------------------------------------------------------------
    sigs = rep.signals
    stage_counter = collections.Counter(s["stage"] for s in sigs)
    reason_counter = collections.Counter(f"{s['stage']}:{s.get('reason_code')}" for s in sigs)
    routing = collections.Counter()
    regime = collections.Counter()
    for s in sigs:
        snap = s.get("snapshot") or {}
        for fam, why in (snap.get("routing") or {}).items():
            routing[f"{fam}:{why}"] += 1
        regime[snap.get("regime") or "n/a"] += 1
    # --- underlying outcome of every decision that had a candidate ------------------------------
    min_stop_atr = pipeline.stops.min_stop_atr
    outcome_rows = []
    for ctx, d in captured:
        c = d.candidate
        if c is None or not ctx.atr:
            continue
        stop = c.invalidation
        if abs(c.entry_ref - stop) < min_stop_atr * ctx.atr:  # build_plan widens to the floor
            stop = c.entry_ref - (1 if c.direction == "up" else -1) * min_stop_atr * ctx.atr
        target = d.target_ref if d.target_ref is not None else c.target_ref
        r, how = underlying_outcome(days.get(ctx.ts.date(), []), ctx.ts.time(), c.direction, ctx.spot, stop, target)
        outcome_rows.append({"ts": ctx.ts.isoformat(), "dir": c.direction, "strategy": c.strategy,
                             "stage": d.stage_reached, "reason": d.reason_code, "approved": d.approved,
                             "score": d.score.total if d.score else None, "r": round(r, 3), "how": how,
                             "rr": round(abs(target - ctx.spot) / abs(ctx.spot - stop), 2) if abs(ctx.spot - stop) else None,
                             "stop_atr": round(abs(ctx.spot - stop) / ctx.atr, 2),
                             "remaining_atr": d.move.remaining_atr if d.move else None,
                             "regime": ctx.regime.get("primary")})

    def agg(key):
        g = collections.defaultdict(list)
        for row in outcome_rows:
            g[key(row)].append(row["r"])
        return {k: {"n": len(v), "avg_r": round(statistics.mean(v), 3), "win": round(sum(1 for x in v if x > 0) / len(v), 3)}
                for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))}

    trades = rep.trade_rows
    net = [float(t["net_pnl"]) for t in trades]
    out = {
        "underlying": args.underlying, "range": [str(start), str(end)], "sessions": len(rep.days), "seconds": round(took, 1),
        "volume_proxy": not args.no_volume, "overrides": args.set, "model_spread_pct": args.spread_pct,
        "presignal_transitions": dict(to_stage.most_common()),
        "presignal_terminal_reasons": dict(term.most_common()),
        "presignal_terminal_from": dict(from_stage_term.most_common()),
        "trade_ready": to_stage.get("TRADE_READY", 0),
        "decisions": len(sigs),
        "pipeline_stage_reached": dict(stage_counter.most_common()),
        "pipeline_reasons": dict(reason_counter.most_common(40)),
        "regime_at_decision": dict(regime.most_common()),
        "family_verdicts": dict(routing.most_common(60)),
        "rejections_incl_gate": rep.rejections.get("by_reason", {}),
        "approved": sum(d.approved for d in rep.days),
        "trades": len(trades),
        "trades_per_session": round(len(trades) / max(1, len(rep.days)), 3),
        "net_pnl": round(sum(net), 2),
        "win_rate": round(sum(1 for x in net if x > 0) / len(net), 3) if net else None,
        "median_trade": round(statistics.median(net), 2) if net else None,
        "exit_reasons": dict(collections.Counter(t["exit_reason"] for t in trades).most_common()),
        "underlying_r_by_stage": agg(lambda r: r["stage"]),
        "underlying_r_by_strategy": agg(lambda r: r["strategy"]),
    }
    print(json.dumps(out, indent=2, default=str))
    if args.json:
        Path(args.json).write_text(json.dumps({**out, "trades_rows": trades, "outcome_rows": outcome_rows}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
