import datetime as dt
import random

from trading_bot.engine.research.metrics import Metrics
from trading_bot.engine.research.validation import (
    GateThresholds,
    coverage,
    evaluate_gates,
    execution_parity,
    monte_carlo,
    parameter_sensitivity,
    render_gates,
    walk_forward,
)
from trading_bot.timeutil import IST


def _rows(n=60, seed=3, edge=40.0):
    rnd = random.Random(seed)
    out = []
    day = dt.date(2026, 8, 3)
    for i in range(n):
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        net = rnd.choice([250.0, 300.0, -180.0, -150.0]) + edge
        entry = dt.datetime.combine(day, dt.time(10, 0), tzinfo=IST)
        out.append({"net_pnl": net, "gross_pnl": net + 60, "costs": 60.0, "exit_ts": entry + dt.timedelta(minutes=30),
                    "entry_ts": entry, "exit_reason": "TARGET_1", "mfe": 100.0, "mae": -50.0, "quantity": 75,
                    "details": {"strategy": "TREND_PULLBACK", "underlying": rnd.choice(["NIFTY", "BANKNIFTY"]), "option_type": "CE",
                                "regime": rnd.choice(["BULL", "RANGE", "BREAKOUT"]), "trend": "BULL", "fingerprint": "FP",
                                "expiry": "2026-09-23", "strike": 25000.0, "holding_minutes": 30, "entry_ts": entry.isoformat()}})
        if i % 3 == 2:
            day += dt.timedelta(days=1)
    return out


def test_walk_forward_windows_are_chronological_and_non_overlapping():
    wf = walk_forward(_rows(), 100000.0, window_days=7)
    assert len(wf.windows) >= 3
    for a, b in zip(wf.windows, wf.windows[1:]):
        assert a.end < b.start
    assert sum(w.metrics.trades for w in wf.windows) == 60
    assert 0.0 <= wf.positive_share <= 1.0 and wf.expectancy_mean is not None
    assert walk_forward([], 100000.0).stable is False


def test_walk_forward_stability_flag():
    good = walk_forward(_rows(edge=120.0), 100000.0, window_days=7)
    assert good.stable and good.positive_share >= 0.6
    bad = walk_forward(_rows(edge=-150.0), 100000.0, window_days=7)
    assert not bad.stable


def test_monte_carlo_distributions_and_stress():
    mc = monte_carlo(_rows(), 100000.0, runs=500, seed=1, extra_slippage_per_trade=100.0)
    assert mc.runs == 500 and mc.trades_per_run == 60
    assert mc.final_net_p5 <= mc.final_net_p50 <= mc.final_net_p95
    assert mc.max_dd_p50 <= mc.max_dd_p95 <= mc.max_dd_worst
    assert 0.0 <= mc.ruin_probability <= 1.0 and mc.loss_streak_p95 <= mc.loss_streak_worst
    assert mc.adverse_execution_net_p50 < mc.final_net_p50 and mc.adverse_execution_ruin >= mc.ruin_probability
    ruinous = monte_carlo(_rows(edge=-400.0), 10000.0, runs=200, seed=2)
    assert ruinous.ruin_probability > 0.5
    assert monte_carlo([], 100000.0).runs == 0
    assert monte_carlo(_rows(), 100000.0, runs=100, seed=9).as_dict() == monte_carlo(_rows(), 100000.0, runs=100, seed=9).as_dict()


def test_parameter_sensitivity_flags_sign_flips():
    def ev_stable(v):
        m = Metrics(); m.expectancy = 50.0 + v; m.profit_factor = 1.5; m.trades = 40; return m

    def ev_fragile(v):
        m = Metrics(); m.expectancy = -50.0 if v > 2 else 50.0; m.profit_factor = 1.0; m.trades = 40; return m

    s = parameter_sensitivity(ev_stable, "min_score", [30, 40, 50])
    assert s.robust and s.sign_flips == 0 and s.expectancy == [80.0, 90.0, 100.0]
    f = parameter_sensitivity(ev_fragile, "min_score", [1, 2, 3, 4])
    assert not f.robust and f.sign_flips == 1


def _executions(n_filled=9, n_rejected=1, latency=800):
    ex = []
    for i in range(n_filled):
        ex.append({"signal_id": f"s{i}", "side": "BUY", "state": "POSITION_ACTIVE", "requested_price": 150.5, "fill_price": 150.8,
                   "quantity": 75, "filled_quantity": 75, "latency_ms": latency + i * 10})
    for i in range(n_rejected):
        ex.append({"signal_id": f"r{i}", "side": "BUY", "state": "REJECTED", "requested_price": 150.5, "fill_price": None,
                   "quantity": 75, "filled_quantity": 0, "latency_ms": None})
    ex.append({"signal_id": "s0", "side": "SELL", "state": "POSITION_ACTIVE", "requested_price": 160.0, "fill_price": 159.9,
               "quantity": 75, "filled_quantity": 75, "latency_ms": 500})
    return ex


def _signals(n=10):
    return [{"signal_id": f"s{i}", "status": "valid", "snapshot": {"option_entry": 150.2}, "underlying": "NIFTY",
             "option_type": "CE", "ts": dt.datetime(2026, 9, 16, 10, 0, tzinfo=IST),
             "explanation": {"Regime": "BULL (x)", "Trend": "BULL 5m"}} for i in range(n)]


def test_execution_parity_reads_entry_legs_only():
    p = execution_parity(_executions(), _signals())
    assert p.orders == 10 and p.filled == 9 and p.fill_probability == 0.9 and p.rejected == 1
    assert abs(p.slippage_vs_executable_mean - 0.3) < 1e-6 and abs(p.slippage_vs_theoretical_mean - 0.6) < 1e-6
    assert p.latency_ms_p50 >= 800 and p.latency_ms_p95 <= 880 and p.partial_fills == 0
    assert execution_parity([], []).orders == 0


def test_coverage_counts_signals_and_trades():
    cov = coverage(_signals(), _rows(30))
    assert cov["signal_count"] == 10 and cov["trade_count"] == 30
    assert cov["signals"]["regime"] == {"BULL": 10} and set(cov["trades"]["regime"]) <= {"BULL", "RANGE", "BREAKOUT"}
    assert cov["trades"]["time_of_day"] == {"10:00-11:30": 30}


def test_gates_report_each_check_and_overall_verdict():
    runs = [{"status": "completed"}] * 5 + [{"status": "stopped"}]
    trades = _rows(120, edge=120.0)
    signals = _signals(150)
    gates = evaluate_gates(runs=runs, executions=_executions(), trade_rows=trades, signals=signals, kill_events=[],
                           capital=100000.0, thresholds=GateThresholds(min_regimes_covered=2))
    assert [g.name.split(" ")[1] for g in gates] == ["1", "2", "3", "4"]
    text = render_gates(gates)
    assert "Gate 1" in text and ("ALL GATES PASSED" in text or "NOT READY FOR LIVE" in text)
    g1, g2, g3, g4 = gates
    assert g1.passed and all(ok for _, ok, _ in g1.checks)
    assert g2.passed, g2.checks
    assert g4.passed, g4.checks
    # a crash and a failed order fail gate 1; a thin sample fails gate 2
    bad = evaluate_gates(runs=runs + [{"status": "crashed"}], executions=_executions() + [{"side": "BUY", "state": "FAILED"}],
                         trade_rows=trades[:10], signals=signals[:20], kill_events=[{"reason": "reconciliation_mismatch"}],
                         capital=100000.0)
    assert not bad[0].passed and not bad[1].passed
    failed_checks = {c for c, ok, _ in bad[0].checks if not ok}
    assert failed_checks == {"no_crashes", "sessions_completed_with_eod", "reconciliation_clean", "no_failed_orders"}
    assert "NOT READY FOR LIVE" in render_gates(bad)
