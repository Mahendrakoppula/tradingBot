"""Research CLI over the engine journal (spec §69-§72, §74-§79, §83).

    python -m trading_bot.research_cli metrics    --from 2026-09-01 --to 2026-09-30
    python -m trading_bot.research_cli review     --from ... --to ...
    python -m trading_bot.research_cli gates      --from ... --to ...
    python -m trading_bot.research_cli walkforward --from ... --to ... [--window 5]
    python -m trading_bot.research_cli montecarlo --from ... --to ... [--runs 2000] [--slippage 50]
    python -m trading_bot.research_cli backtest   --from ... --to ... [--profile realistic]
    python -m trading_bot.research_cli counterfactuals --from ... --to ...

Reads TECH_DATABASE_URL (and the rest of the TECH_* config). Read-only
except `backtest`, which writes nothing to Postgres either (its journal is
in memory); nothing here can reach an order endpoint.
"""
import argparse
import datetime as dt
import json
import sys

from trading_bot.engine.config import EngineConfig
from trading_bot.engine.db.dal import Database
from trading_bot.engine.instruments import EXPECTED_SPOT
from trading_bot.engine.research import metrics as M
from trading_bot.engine.research import refinements as RF
from trading_bot.engine.research import review as R
from trading_bot.engine.research import validation as V
from trading_bot.engine.warmup import Instrument
from trading_bot.timeutil import IST


def _range(args) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(dt.date.fromisoformat(args.start), dt.time(0, 0), tzinfo=IST)
    end = dt.datetime.combine(dt.date.fromisoformat(args.end) + dt.timedelta(days=1), dt.time(0, 0), tzinfo=IST)
    return start, end


def _open(cfg: EngineConfig) -> Database:
    if not cfg.database_url:
        sys.exit("TECH_DATABASE_URL is not set")
    return Database(cfg.database_url).connect()


def cmd_metrics(cfg, dal, args) -> int:
    start, end = _range(args)
    rows = dal.trade_results_between(start, end)
    m = M.compute(rows, cfg.capital)
    print(M.render(m, f"{args.start}..{args.end}: "))
    if args.segments:
        for key, groups in M.segmented(rows, cfg.capital).items():
            print(f"\n[{key}]")
            for k, gm in groups.items():
                print(f"  {k}: n={gm.trades} net={gm.net_pnl:+.0f} exp={gm.expectancy:+.0f} pf={gm.profit_factor}")
    fps = M.fingerprint_stats(rows, cfg.capital)
    if fps:
        print("\n[fingerprints]")
        for f in fps[:20]:
            print(f"  {f.fingerprint}: n={f.sample_size} wr={f.win_rate:.0%} exp={f.expectancy:+.0f} {'reliable' if f.reliable else 'thin'}")
    return 0


def cmd_review(cfg, dal, args) -> int:
    start, end = _range(args)
    rev = R.daily_review(dal.trade_results_between(start, end), dal.signals_between(start, end),
                         dal.executions_between(start, end), cfg.capital, f"{args.start}..{args.end}",
                         candles_1m=_candle_loader(cfg, dal, start, end))
    print(rev.render())
    if getattr(args, "json", False):
        print(json.dumps(rev.rejected, indent=2, default=str))
    return 0


def cmd_gates(cfg, dal, args) -> int:
    start, end = _range(args)
    gates = V.evaluate_gates(runs=dal.runs_between(start, end), executions=dal.executions_between(start, end),
                             trade_rows=dal.trade_results_between(start, end), signals=dal.signals_between(start, end),
                             kill_events=dal.kill_events_between(start, end), capital=cfg.capital)
    print(V.render_gates(gates))
    return 0 if all(g.passed for g in gates) else 1


def cmd_walkforward(cfg, dal, args) -> int:
    start, end = _range(args)
    wf = V.walk_forward(dal.trade_results_between(start, end), cfg.capital, window_days=args.window)
    print(json.dumps(wf.as_dict(), indent=2))
    print(json.dumps(V.coverage(dal.signals_between(start, end), dal.trade_results_between(start, end)), indent=2))
    return 0


def cmd_montecarlo(cfg, dal, args) -> int:
    start, end = _range(args)
    mc = V.monte_carlo(dal.trade_results_between(start, end), cfg.capital, runs=args.runs, extra_slippage_per_trade=args.slippage)
    print(json.dumps(mc.as_dict(), indent=2))
    return 0


def cmd_counterfactuals(cfg, dal, args) -> int:
    start, end = _range(args)
    signals = dal.signals_between(start, end)
    tokens = {u: EXPECTED_SPOT[u][0] for u in cfg.underlyings if u in EXPECTED_SPOT}
    cache: dict[str, list] = {}

    def candles(u):
        if u not in cache:
            cache[u] = [c.as_dict() for c in dal.load_candles(tokens[u], "1m", start, until=end + dt.timedelta(days=1))]
        return cache[u]

    cfs = R.counterfactuals(signals, candles)
    print(json.dumps(R.counterfactual_summary(cfs), indent=2))
    for c in cfs[: args.limit]:
        print(json.dumps(c.as_dict()))
    return 0


def _candle_loader(cfg, dal, start, end):
    tokens = {u: EXPECTED_SPOT[u][0] for u in cfg.underlyings if u in EXPECTED_SPOT}
    cache: dict[str, list] = {}

    def candles(u):
        if u not in cache:
            cache[u] = [c.as_dict() for c in dal.load_candles(tokens[u], "1m", start, until=end + dt.timedelta(days=1))] if u in tokens else []
        return cache[u]

    return candles


def cmd_refinements(cfg, dal, args) -> int:
    """§71/§83: count how often each candidate refinement recurred and what the
    underlying did afterwards. Reports only - never changes a rule."""
    start, end = _range(args)
    statuses = RF.track(dal.signals_between(start, end), _candle_loader(cfg, dal, start, end))
    print(RF.render(statuses))
    if args.json:
        print(json.dumps([s.as_dict() for s in statuses], indent=2))
    return 0


def cmd_backtest(cfg, dal, args) -> int:
    from trading_bot.engine.research.backtest import CandleSource, run_backtest
    instruments = [Instrument(u, EXPECTED_SPOT[u][1], EXPECTED_SPOT[u][0], "spot") for u in cfg.underlyings if u in EXPECTED_SPOT]
    rep = run_backtest(cfg, instruments, CandleSource(dal=dal), dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end),
                       profile=args.profile, holidays=cfg.holidays)
    print(rep.summary())
    if args.json:
        print(json.dumps({"days": [d.__dict__ for d in rep.days], "metrics": rep.metrics.as_dict(), "rejections": rep.rejections},
                         default=str, indent=2))
    return 0


class _Tee:
    """Capture stdout so --telegram can post the same report (chunked)."""

    def __init__(self, real):
        self.real, self.buf = real, []

    def write(self, s):
        self.real.write(s)
        self.buf.append(s)

    def flush(self):
        self.real.flush()


def _post_telegram(text: str) -> None:
    from trading_bot import technical_notifier
    text = text.strip()
    for i in range(0, len(text), 3500):
        technical_notifier.notify(text[i:i + 3500])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research_cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("metrics", "review", "gates", "walkforward", "montecarlo", "backtest", "counterfactuals", "refinements"):
        p = sub.add_parser(name)
        p.add_argument("--from", dest="start", required=True)
        p.add_argument("--to", dest="end", required=True)
        p.add_argument("--telegram", action="store_true", help="also post the report to the TECH Telegram chat")
        if name == "metrics":
            p.add_argument("--segments", action="store_true")
        if name == "walkforward":
            p.add_argument("--window", type=int, default=5)
        if name == "montecarlo":
            p.add_argument("--runs", type=int, default=2000)
            p.add_argument("--slippage", type=float, default=0.0)
        if name == "backtest":
            p.add_argument("--profile", default="realistic", choices=("realistic", "conservative", "ideal"))
            p.add_argument("--json", action="store_true")
        if name == "counterfactuals":
            p.add_argument("--limit", type=int, default=50)
        if name in ("refinements", "review"):
            p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    cfg = EngineConfig.from_env()
    dal = _open(cfg)
    tee = _Tee(sys.stdout) if getattr(args, "telegram", False) else None
    if tee:
        sys.stdout = tee
    try:
        rc = {"metrics": cmd_metrics, "review": cmd_review, "gates": cmd_gates, "walkforward": cmd_walkforward,
              "montecarlo": cmd_montecarlo, "backtest": cmd_backtest, "counterfactuals": cmd_counterfactuals,
              "refinements": cmd_refinements}[args.cmd](cfg, dal, args)
    finally:
        dal.close()
        if tee:
            sys.stdout = tee.real
    if tee:
        try:
            _post_telegram("".join(tee.buf))
        except Exception as exc:  # noqa: BLE001 - a Telegram failure must not fail the report
            print(f"telegram post failed: {exc}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
