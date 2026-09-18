"""Validation (spec §74-§76, §79, §81, §84): walk-forward windows, Monte
Carlo on trade sequences, parameter sensitivity, execution parity and the
four paper-to-live gates. Everything takes journal ROWS (trade_results,
signals, executions, runs) so it works on Postgres and MemoryDAL alike,
and nothing here changes engine behaviour - it only reports.

There is no fitting step anywhere: walk-forward here means "evaluate on
consecutive chronological windows and look at the dispersion", which is
the honest form when parameters are hand-set (§84 "never tune against
OOS"). Sample sizes are printed next to every number for the same reason.
"""
import datetime as dt
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from trading_bot.engine.research.metrics import Metrics, compute, segment_key

# --- §75 walk-forward -------------------------------------------------------------------------


@dataclass
class Window:
    start: dt.date
    end: dt.date
    metrics: Metrics


@dataclass
class WalkForward:
    windows: list[Window]
    positive_share: float  # share of windows with net > 0
    expectancy_mean: float | None
    expectancy_stdev: float | None
    worst_window_net: float | None
    stable: bool  # positive_share >= threshold and no window loses more than max_window_loss_pct of capital

    def as_dict(self) -> dict:
        return {"windows": [{"start": w.start.isoformat(), "end": w.end.isoformat(), "trades": w.metrics.trades,
                             "net": w.metrics.net_pnl, "expectancy": w.metrics.expectancy, "pf": w.metrics.profit_factor}
                            for w in self.windows],
                "positive_share": self.positive_share, "expectancy_mean": self.expectancy_mean,
                "expectancy_stdev": self.expectancy_stdev, "worst_window_net": self.worst_window_net, "stable": self.stable}


def _exit_date(r: dict) -> dt.date:
    t = r.get("exit_ts")
    if isinstance(t, str):
        t = dt.datetime.fromisoformat(t)
    return t.date()


def walk_forward(trade_rows: list[dict], capital: float, *, window_days: int = 5, min_positive_share: float = 0.6,
                 max_window_loss_pct: float = 0.03) -> WalkForward:
    """Chronological, non-overlapping windows of `window_days` calendar days."""
    if not trade_rows:
        return WalkForward([], 0.0, None, None, None, False)
    rows = sorted(trade_rows, key=_exit_date)
    first, last = _exit_date(rows[0]), _exit_date(rows[-1])
    windows: list[Window] = []
    start = first
    while start <= last:
        end = start + dt.timedelta(days=window_days - 1)
        chunk = [r for r in rows if start <= _exit_date(r) <= end]
        if chunk:
            windows.append(Window(start, end, compute(chunk, capital)))
        start = end + dt.timedelta(days=1)
    nets = [w.metrics.net_pnl for w in windows]
    exps = [w.metrics.expectancy for w in windows]
    pos = sum(1 for n in nets if n > 0) / len(windows) if windows else 0.0
    worst = min(nets) if nets else None
    stable = bool(windows) and pos >= min_positive_share and (worst is None or -worst <= max_window_loss_pct * capital)
    return WalkForward(windows, round(pos, 3), round(statistics.mean(exps), 2) if exps else None,
                       round(statistics.pstdev(exps), 2) if len(exps) > 1 else None, worst, stable)


def coverage(signals: list[dict], trade_rows: list[dict]) -> dict:
    """§75/§78 'cover': which regimes, trends, indices, sides, expiries and
    times of day the sample actually contains, for signals and for trades."""
    def _count(rows, keys):
        out = {}
        for k in keys:
            c = Counter()
            for r in rows:
                if k == "regime" and "explanation" in r:
                    v = (r.get("explanation") or {}).get("Regime", "?").split(" ")[0]
                elif k == "trend" and "explanation" in r:
                    v = (r.get("explanation") or {}).get("Trend", "?").split(" ")[0]
                elif k == "time_of_day":
                    v = segment_key(r if "details" in r else {"details": {"entry_ts": r.get("ts")}}, "time_of_day")
                else:
                    v = segment_key(r, k) if "details" in r else str(r.get(k if k != "index" else "underlying", "?"))
                c[v] += 1
            out[k] = dict(sorted(c.items()))
        return out
    keys = ("regime", "trend", "underlying", "option_type", "time_of_day", "expiry")
    return {"signals": _count(signals, ("regime", "trend", "underlying", "option_type", "time_of_day")),
            "trades": _count(trade_rows, keys), "signal_count": len(signals), "trade_count": len(trade_rows)}


# --- §76 Monte Carlo ---------------------------------------------------------------------------------


@dataclass
class MonteCarlo:
    runs: int
    trades_per_run: int
    final_net_p5: float
    final_net_p50: float
    final_net_p95: float
    max_dd_p50: float
    max_dd_p95: float
    max_dd_worst: float
    ruin_probability: float  # share of runs whose equity fell below -ruin_pct of capital at any point
    loss_streak_p95: int
    loss_streak_worst: int
    adverse_execution_net_p50: float  # median final net with extra slippage applied to every trade
    adverse_execution_ruin: float

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def _path_stats(nets: list[float], ruin_level: float) -> tuple[float, float, bool, int]:
    cum = peak = 0.0
    dd = 0.0
    ruined = False
    streak = worst = 0
    for n in nets:
        cum += n
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
        if cum <= -ruin_level:
            ruined = True
        streak = streak + 1 if n <= 0 else 0
        worst = max(worst, streak)
    return cum, dd, ruined, worst


def monte_carlo(trade_rows: list[dict], capital: float, *, runs: int = 2000, seed: int = 7, ruin_pct: float = 0.10,
                extra_slippage_per_trade: float = 0.0) -> MonteCarlo:
    """Bootstrap resampling of the trade NET P&L sequence (with replacement,
    same length) - the ordering risk the spec asks about. `extra_slippage_
    per_trade` (rupees) stresses execution: every trade loses that much more."""
    nets = [float(r.get("net_pnl") or 0.0) for r in trade_rows]
    n = len(nets)
    if n == 0:
        return MonteCarlo(0, 0, 0, 0, 0, 0, 0, 0, 0.0, 0, 0, 0, 0.0)
    rng = random.Random(seed)
    finals, dds, streaks = [], [], []
    ruins = 0
    adv_finals, adv_ruins = [], 0
    level = ruin_pct * capital
    for _ in range(runs):
        seq = [nets[rng.randrange(n)] for _ in range(n)]
        f, d, r, s = _path_stats(seq, level)
        finals.append(f); dds.append(d); streaks.append(s); ruins += int(r)
        if extra_slippage_per_trade:
            f2, _, r2, _ = _path_stats([x - extra_slippage_per_trade for x in seq], level)
            adv_finals.append(f2); adv_ruins += int(r2)
    return MonteCarlo(runs, n, round(_pct(finals, 0.05), 2), round(_pct(finals, 0.5), 2), round(_pct(finals, 0.95), 2),
                      round(_pct(dds, 0.5), 2), round(_pct(dds, 0.95), 2), round(max(dds), 2), round(ruins / runs, 4),
                      int(_pct(streaks, 0.95)), max(streaks),
                      round(_pct(adv_finals, 0.5), 2) if adv_finals else round(_pct(finals, 0.5), 2),
                      round(adv_ruins / runs, 4) if adv_finals else round(ruins / runs, 4))


# --- parameter sensitivity (§79 gate 3) --------------------------------------------------------------


@dataclass
class Sensitivity:
    parameter: str
    values: list
    expectancy: list[float]
    profit_factor: list
    trades: list[int]
    sign_flips: int  # how often expectancy changes sign across adjacent values
    robust: bool  # no sign flips and expectancy dispersion below a share of its mean

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def parameter_sensitivity(evaluate, parameter: str, values: list, *, max_rel_dispersion: float = 1.0) -> Sensitivity:
    """`evaluate(value) -> Metrics` runs the backtest with `parameter` set to
    `value`. A strategy whose sign depends on a knob is not robust."""
    ms = [evaluate(v) for v in values]
    exps = [m.expectancy for m in ms]
    flips = sum(1 for a, b in zip(exps, exps[1:]) if (a > 0) != (b > 0) and a != 0 and b != 0)
    mean = statistics.mean(exps) if exps else 0.0
    disp = statistics.pstdev(exps) if len(exps) > 1 else 0.0
    robust = flips == 0 and (mean == 0 or disp / abs(mean) <= max_rel_dispersion)
    return Sensitivity(parameter, list(values), exps, [m.profit_factor for m in ms], [m.trades for m in ms], flips, robust)


# --- §79 gate 4 / §81 execution parity --------------------------------------------------------------------


@dataclass
class Parity:
    orders: int
    filled: int
    fill_probability: float
    slippage_vs_executable_mean: float  # fill - limit (BUY) in premium points
    slippage_vs_theoretical_mean: float  # fill - snapshot entry (BUY)
    latency_ms_p50: float
    latency_ms_p95: float
    partial_fills: int
    rejected: int
    timeouts: int

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def execution_parity(executions: list[dict], signals: list[dict]) -> Parity:
    """Theoretical entry (snapshot.option_entry) vs executable (limit) vs
    paper fill, from the executions journal. Entry BUY legs only."""
    theo = {s["signal_id"]: (s.get("snapshot") or {}).get("option_entry") for s in signals}
    entries = [e for e in executions if e.get("side") == "BUY"]
    if not entries:
        return Parity(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0)
    filled = [e for e in entries if e.get("state") == "POSITION_ACTIVE" and e.get("fill_price") is not None]
    s_exec = [float(e["fill_price"]) - float(e["requested_price"]) for e in filled if e.get("requested_price") is not None]
    s_theo = [float(e["fill_price"]) - float(theo[e["signal_id"]]) for e in filled
              if e.get("signal_id") in theo and theo[e["signal_id"]] is not None]
    lat = [float(e["latency_ms"]) for e in filled if e.get("latency_ms") is not None]
    partial = sum(1 for e in filled if int(e.get("filled_quantity") or 0) < int(e.get("quantity") or 0))
    rejected = sum(1 for e in entries if e.get("state") == "REJECTED")
    timeouts = sum(1 for e in entries if e.get("state") == "TIMEOUT")
    return Parity(len(entries), len(filled), round(len(filled) / len(entries), 4),
                  round(statistics.mean(s_exec), 3) if s_exec else 0.0, round(statistics.mean(s_theo), 3) if s_theo else 0.0,
                  round(_pct(lat, 0.5), 1), round(_pct(lat, 0.95), 1), partial, rejected, timeouts)


# --- §79 gates ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GateThresholds:
    min_valid_opportunities: int = 100  # §78 "prefer 100+"
    min_trades: int = 50
    min_expectancy: float = 0.0
    min_profit_factor: float = 1.2
    max_drawdown_pct: float = 0.06
    min_positive_windows: float = 0.6
    max_ruin_probability: float = 0.01
    min_regimes_covered: int = 3
    min_fill_probability: float = 0.9
    max_slippage_points: float = 1.0
    max_latency_p95_ms: float = 3000.0
    max_reconcile_mismatches: int = 0
    max_crashes: int = 0


@dataclass
class GateResult:
    name: str
    passed: bool
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def note(self, check: str, ok: bool, detail: str = "") -> None:
        self.checks.append((check, ok, detail))
        self.passed = self.passed and ok


def gate1_technical(runs: list[dict], executions: list[dict], trade_rows: list[dict], kill_events: list[dict],
                    th: GateThresholds) -> GateResult:
    g = GateResult("Gate 1 - Technical reliability", True)
    crashes = sum(1 for r in runs if r.get("status") == "crashed")
    g.note("no_crashes", crashes <= th.max_crashes, f"{crashes} crashed runs")
    completed = sum(1 for r in runs if r.get("status") == "completed")
    g.note("sessions_completed_with_eod", completed == len([r for r in runs if r.get("status") != "stopped"]), f"{completed}/{len(runs)}")
    mism = sum(1 for k in kill_events if "reconcil" in str(k.get("reason", "")))
    g.note("reconciliation_clean", mism <= th.max_reconcile_mismatches, f"{mism} mismatch events")
    failed = sum(1 for e in executions if e.get("state") == "FAILED")
    g.note("no_failed_orders", failed == 0, f"{failed} FAILED")
    bad_pnl = [t for t in trade_rows if abs(float(t.get("gross_pnl") or 0) - float(t.get("costs") or 0) - float(t.get("net_pnl") or 0)) > 0.05]
    g.note("pnl_arithmetic", not bad_pnl, f"{len(bad_pnl)} rows with net != gross - costs")
    bad_qty = [t for t in trade_rows if int(t.get("quantity") or 0) <= 0]
    g.note("quantities_positive", not bad_qty, f"{len(bad_qty)} bad")
    return g


def gate2_performance(trade_rows: list[dict], signals: list[dict], capital: float, th: GateThresholds) -> GateResult:
    g = GateResult("Gate 2 - Performance", True)
    m = compute(trade_rows, capital)
    valid = sum(1 for s in signals if s.get("status") == "valid")
    g.note("valid_opportunities", valid >= th.min_valid_opportunities, f"{valid} (need {th.min_valid_opportunities})")
    g.note("trade_sample", m.trades >= th.min_trades, f"{m.trades} (need {th.min_trades})")
    g.note("expectancy_positive", m.expectancy > th.min_expectancy, f"{m.expectancy:+.0f}/trade")
    g.note("profit_factor", (m.profit_factor or 0) >= th.min_profit_factor, f"{m.profit_factor}")
    g.note("max_drawdown", m.max_drawdown_pct <= th.max_drawdown_pct, f"{m.max_drawdown_pct:.1%}")
    g.note("mfe_mae_recorded", m.trades == 0 or (m.avg_mfe != 0 or m.avg_mae != 0), "")
    return g


def gate3_robustness(trade_rows: list[dict], signals: list[dict], capital: float, th: GateThresholds,
                     sensitivities: list[Sensitivity] | None = None) -> GateResult:
    g = GateResult("Gate 3 - Robustness", True)
    wf = walk_forward(trade_rows, capital, min_positive_share=th.min_positive_windows)
    g.note("walk_forward_stable", wf.stable, f"positive windows {wf.positive_share:.0%} over {len(wf.windows)}")
    mc = monte_carlo(trade_rows, capital)
    g.note("monte_carlo_ruin", mc.ruin_probability <= th.max_ruin_probability, f"ruin p={mc.ruin_probability:.2%} dd95={mc.max_dd_p95:.0f}")
    cov = coverage(signals, trade_rows)
    regimes = len(cov["trades"].get("regime", {}))
    g.note("regimes_covered", regimes >= th.min_regimes_covered, f"{regimes} regimes in trades")
    if sensitivities:
        g.note("parameter_sensitivity", all(s.robust for s in sensitivities), ", ".join(f"{s.parameter}:{'ok' if s.robust else 'fragile'}" for s in sensitivities))
    return g


def gate4_parity(executions: list[dict], signals: list[dict], th: GateThresholds) -> GateResult:
    g = GateResult("Gate 4 - Execution parity", True)
    p = execution_parity(executions, signals)
    g.note("fill_probability", p.orders > 0 and p.fill_probability >= th.min_fill_probability, f"{p.fill_probability:.0%} of {p.orders}")
    g.note("slippage_vs_theoretical", abs(p.slippage_vs_theoretical_mean) <= th.max_slippage_points, f"{p.slippage_vs_theoretical_mean:+.2f} pts")
    g.note("latency_p95", p.latency_ms_p95 <= th.max_latency_p95_ms, f"{p.latency_ms_p95:.0f} ms")
    return g


def evaluate_gates(*, runs, executions, trade_rows, signals, kill_events, capital, thresholds: GateThresholds | None = None,
                   sensitivities=None) -> list[GateResult]:
    th = thresholds or GateThresholds()
    return [gate1_technical(runs, executions, trade_rows, kill_events, th), gate2_performance(trade_rows, signals, capital, th),
            gate3_robustness(trade_rows, signals, capital, th, sensitivities), gate4_parity(executions, signals, th)]


def render_gates(gates: list[GateResult]) -> str:
    lines = []
    for g in gates:
        lines.append(f"{'PASS' if g.passed else 'FAIL'} {g.name}")
        for name, ok, detail in g.checks:
            lines.append(f"  [{'x' if ok else ' '}] {name} {detail}".rstrip())
    lines.append("ALL GATES PASSED" if all(g.passed for g in gates) else "NOT READY FOR LIVE")
    return "\n".join(lines)
