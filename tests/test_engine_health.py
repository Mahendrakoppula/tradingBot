import datetime as dt
import importlib.util
from pathlib import Path

from trading_bot.engine.health import HealthMonitor, HealthThresholds, format_alert, rss_mb
from trading_bot.timeutil import IST

T0 = dt.datetime(2026, 9, 16, 10, 0, tzinfo=IST)


def _t(s):
    return T0 + dt.timedelta(seconds=s)


def test_quiet_state_has_no_alerts_and_a_heartbeat():
    m = HealthMonitor(HealthThresholds(max_rss_mb=10_000))
    m.state.last_tick_at = T0
    assert m.check(_t(1)) == [] and m.worst() == "OK"
    assert m.heartbeat_due(_t(1)) and not m.heartbeat_due(_t(30)) and m.heartbeat_due(_t(61))
    snap = m.snapshot(_t(61))
    assert snap["status"] == "OK" and snap["feed"]["tick_age_s"] == 61.0 and set(snap) >= {"feed", "data", "strategy", "execution", "risk", "broker", "process"}
    assert isinstance(rss_mb(), float)


def test_alerts_are_edge_triggered_and_clear():
    m = HealthMonitor(HealthThresholds(stale_tick_seconds=10, max_rss_mb=10_000))
    m.state.last_tick_at = T0
    a = m.check(_t(15))
    assert [x.code for x in a] == ["feed_stale"] and a[0].severity == "CRITICAL" and not a[0].cleared
    assert m.check(_t(20)) == []  # steady state: no repeat
    m.state.last_tick_at = _t(25)
    r = m.check(_t(26))
    assert len(r) == 1 and r[0].cleared and r[0].code == "feed_stale" and r[0].message.startswith("recovered")
    assert m.active() == [] and m.worst() == "OK"
    assert format_alert(r[0], "PAPER").startswith("[RECOVERED] PAPER health")


def test_out_of_session_silence_is_not_stale():
    m = HealthMonitor(HealthThresholds(stale_tick_seconds=10, max_rss_mb=10_000), in_session=lambda now: False)
    m.state.last_tick_at = T0
    m.state.last_snapshot_at = {"NIFTY": T0}
    assert m.check(_t(3600)) == []


def test_every_condition_fires_with_its_severity():
    m = HealthMonitor(HealthThresholds(max_rss_mb=10_000, max_reconnects_per_hour=2))
    s = m.state
    s.feed_connected = False
    s.dropped_ticks = 5000
    s.clock_drift_seconds = 9.0
    s.quality = {"NIFTY": "GAP", "SENSEX": "OK"}
    s.last_snapshot_at = {"BANKNIFTY": T0}
    s.orders_sent, s.orders_rejected = 10, 5
    s.latencies_ms = [4000, 5000, 6000]
    s.realized_today, s.daily_cap = -1000.0, 1000.0
    s.open_risk, s.heat_cap = 700.0, 750.0
    s.reconciled = False
    s.api_errors = 5
    s.db_write_failures = 3
    s.circuit_breaker = "data_gap"
    for k in range(3):
        m.note_reconnect(_t(k))
    alerts = {a.code: a.severity for a in m.check(_t(600))}
    assert alerts == {
        "feed_disconnected": "CRITICAL", "feed_flapping": "WARN", "ticks_dropped": "WARN", "clock_drift": "CRITICAL",
        "data_quality_NIFTY": "WARN", "engine_stalled_BANKNIFTY": "CRITICAL", "orders_rejected": "WARN", "order_latency": "WARN",
        "daily_loss": "CRITICAL", "portfolio_heat": "WARN", "reconciliation_mismatch": "CRITICAL", "api_errors": "WARN",
        "db_write_failures": "CRITICAL", "circuit_breaker": "CRITICAL",
    }
    assert m.worst() == "CRITICAL" and m.active()[0].severity == "CRITICAL"
    # escalation: daily loss goes from WARN to CRITICAL as the cap is crossed
    m2 = HealthMonitor(HealthThresholds(max_rss_mb=10_000))
    m2.state.daily_cap = 1000.0
    m2.state.realized_today = -800.0
    assert m2.check(_t(1))[0].severity == "WARN"
    m2.state.realized_today = -1000.0
    esc = m2.check(_t(2))
    assert len(esc) == 1 and esc[0].severity == "CRITICAL" and not esc[0].cleared


def test_note_order_keeps_bounded_latency_history():
    m = HealthMonitor()
    for i in range(300):
        m.note_order(filled=True, rejected=False, latency_ms=100 + i, slippage=0.1)
    assert len(m.state.latencies_ms) == 200 and m.state.orders_filled == 300


def test_paper_loop_emits_health_alerts_and_heartbeat():
    pl = importlib.util.spec_from_file_location("pl_t", Path(__file__).resolve().parent / "test_engine_paper_loop.py")
    pl_t = importlib.util.module_from_spec(pl)
    pl.loader.exec_module(pl_t)
    notes = pl_t._Notes()
    loop, clock = pl_t._loop(execute=True, profile="ideal", notes=notes)
    loop.health.t = HealthThresholds(stale_tick_seconds=10, max_rss_mb=10_000)
    ctx = pl_t.pipe_t._ctx()
    loop._on_context(ctx, 1)
    loop._health_tick(clock.now())
    assert loop.health.state.quality == {"NIFTY": "OK"} and "NIFTY" in loop.health.state.last_snapshot_at
    # simulate a stale feed: the source's health says the last tick was long ago
    from trading_bot.engine.quality import FeedHealth
    loop.source.health = lambda: FeedHealth(connected=True, last_tick_at=clock.now() - dt.timedelta(seconds=60))
    loop._health_tick(clock.now())
    assert any("[CRITICAL] PAPER health: no tick" in m for m in notes.msgs)
    assert loop.health.active()[0].code == "feed_stale"
    loop.source.health = lambda: FeedHealth(connected=True, last_tick_at=clock.now())
    loop._health_tick(clock.now())
    assert loop.health.active() == [] and any("[RECOVERED]" in m for m in notes.msgs)
    # a journal write failure is counted, not fatal
    loop.dal.insert_signal = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
    pl_t._trade_ready(loop, ctx)
    assert loop.health.state.db_write_failures == 1


def test_clock_drift_only_counts_in_session():
    closed = HealthMonitor(HealthThresholds(max_rss_mb=10_000), in_session=lambda now: False)
    closed.state.clock_drift_seconds = 3379.0
    assert closed.check(_t(1)) == []
    live = HealthMonitor(HealthThresholds(max_rss_mb=10_000))
    live.state.clock_drift_seconds = 9.0
    assert [a.code for a in live.check(_t(1))] == ["clock_drift"]
