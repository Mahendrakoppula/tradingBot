"""Health monitoring (spec §87, §85, §86): one place that watches market
data, strategy, execution, risk and broker health, raises ALERTS with
hysteresis (fire once when a condition starts, once when it clears) and
emits a JSON heartbeat so an outside watcher can tell "quiet" from "dead".

Pure bookkeeping: the loop feeds it facts, it returns Alerts; the loop
decides what to do with them (Telegram, journal, circuit breaker). It never
reads the network or the clock itself.
"""
import datetime as dt
import sys
from dataclasses import dataclass, field

try:  # POSIX only; the box is Linux, the dev machine is Windows
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None

SEVERITIES = ("INFO", "WARN", "CRITICAL")


@dataclass(frozen=True)
class HealthThresholds:
    stale_tick_seconds: float = 20.0
    snapshot_gap_minutes: float = 7.0  # a 5m close missed by this much = engine stalled
    max_reconnects_per_hour: int = 6
    max_dropped_ticks: int = 1000
    max_api_errors: int = 5
    max_db_write_failures: int = 3
    max_order_latency_p95_ms: float = 3000.0
    max_rejected_order_share: float = 0.3
    max_clock_drift_seconds: float = 5.0
    daily_loss_warn_share: float = 0.75  # of the daily cap
    heat_warn_share: float = 0.9  # of the heat cap
    max_rss_mb: float = 450.0  # the unit is capped at 600M; warn before the kernel does
    heartbeat_seconds: int = 60


@dataclass(frozen=True)
class Alert:
    code: str
    severity: str
    message: str
    ts: dt.datetime
    cleared: bool = False  # True when this is the "recovered" edge


@dataclass
class HealthState:
    """Facts the loop keeps current. All optional so SHADOW and PAPER share it."""
    # market data
    feed_connected: bool = True
    last_tick_at: dt.datetime | None = None
    reconnects: int = 0
    dropped_ticks: int = 0
    clock_drift_seconds: float | None = None
    quality: dict = field(default_factory=dict)  # underlying -> status
    last_snapshot_at: dict = field(default_factory=dict)  # underlying -> ts
    # strategy
    snapshots: int = 0
    decisions: int = 0
    approved: int = 0
    rejections_by_stage: dict = field(default_factory=dict)
    # execution
    orders_sent: int = 0
    orders_filled: int = 0
    orders_rejected: int = 0
    latencies_ms: list = field(default_factory=list)
    slippages: list = field(default_factory=list)
    # risk
    realized_today: float = 0.0
    daily_cap: float = 0.0
    open_risk: float = 0.0
    heat_cap: float = 0.0
    open_positions: int = 0
    # broker / plumbing
    reconciled: bool = True
    api_errors: int = 0
    db_write_failures: int = 0
    circuit_breaker: str | None = None
    kills: list = field(default_factory=list)


def rss_mb() -> float:
    """Peak resident set size in MB; 0.0 where the platform cannot say."""
    if resource is None:
        return 0.0
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru / 1024.0 if sys.platform != "darwin" else ru / (1024.0 * 1024.0)  # Linux KiB, macOS bytes


def _p95(xs: list) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return float(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))])


class HealthMonitor:
    def __init__(self, thresholds: HealthThresholds | None = None, *, in_session=None):
        self.t = thresholds or HealthThresholds()
        self.state = HealthState()
        self._active: dict[str, Alert] = {}  # code -> the alert currently firing
        self._last_heartbeat: dt.datetime | None = None
        self._reconnect_times: list[dt.datetime] = []
        self.in_session = in_session or (lambda now: True)
        self.history: list[Alert] = []

    # --- facts in ------------------------------------------------------------------

    def note_reconnect(self, now: dt.datetime) -> None:
        self._reconnect_times.append(now)
        self.state.reconnects += 1

    def note_order(self, *, filled: bool, rejected: bool, latency_ms: int | None, slippage: float | None) -> None:
        self.state.orders_sent += 1
        if filled:
            self.state.orders_filled += 1
        if rejected:
            self.state.orders_rejected += 1
        if latency_ms is not None:
            self.state.latencies_ms.append(latency_ms)
            del self.state.latencies_ms[:-200]
        if slippage is not None:
            self.state.slippages.append(slippage)
            del self.state.slippages[:-200]

    # --- evaluation ----------------------------------------------------------------------

    def _conditions(self, now: dt.datetime) -> list[tuple[str, str, str]]:
        """(code, severity, message) for every condition that is TRUE now."""
        s, t = self.state, self.t
        out: list[tuple[str, str, str]] = []
        live = self.in_session(now)
        if not s.feed_connected:
            out.append(("feed_disconnected", "CRITICAL", "market data feed disconnected"))
        elif live and s.last_tick_at is not None and (now - s.last_tick_at).total_seconds() > t.stale_tick_seconds:
            out.append(("feed_stale", "CRITICAL", f"no tick for {(now - s.last_tick_at).total_seconds():.0f}s"))
        recent = [r for r in self._reconnect_times if (now - r).total_seconds() <= 3600]
        if len(recent) > t.max_reconnects_per_hour:
            out.append(("feed_flapping", "WARN", f"{len(recent)} reconnects in the last hour"))
        if s.dropped_ticks > t.max_dropped_ticks:
            out.append(("ticks_dropped", "WARN", f"{s.dropped_ticks} ticks dropped (queue full)"))
        if s.clock_drift_seconds is not None and abs(s.clock_drift_seconds) > t.max_clock_drift_seconds:
            out.append(("clock_drift", "CRITICAL", f"clock drift {s.clock_drift_seconds:+.1f}s (§86: stop new entries)"))
        for u, q in sorted(s.quality.items()):
            if q != "OK":
                out.append((f"data_quality_{u}", "WARN", f"{u} data quality {q}"))
        for u, ts in sorted(s.last_snapshot_at.items()):
            if live and (now - ts).total_seconds() > t.snapshot_gap_minutes * 60:
                out.append((f"engine_stalled_{u}", "CRITICAL", f"{u}: no 5m snapshot for {(now - ts).total_seconds() / 60:.0f} min"))
        if s.orders_sent >= 5 and s.orders_rejected / s.orders_sent > t.max_rejected_order_share:
            out.append(("orders_rejected", "WARN", f"{s.orders_rejected}/{s.orders_sent} orders rejected"))
        p95 = _p95(s.latencies_ms)
        if len(s.latencies_ms) >= 3 and p95 > t.max_order_latency_p95_ms:
            out.append(("order_latency", "WARN", f"order latency p95 {p95:.0f} ms"))
        if s.daily_cap > 0 and -s.realized_today >= t.daily_loss_warn_share * s.daily_cap:
            sev = "CRITICAL" if -s.realized_today >= s.daily_cap else "WARN"
            out.append(("daily_loss", sev, f"realized today {s.realized_today:+.0f} vs cap -{s.daily_cap:.0f}"))
        if s.heat_cap > 0 and s.open_risk >= t.heat_warn_share * s.heat_cap:
            out.append(("portfolio_heat", "WARN", f"open risk {s.open_risk:.0f} vs heat cap {s.heat_cap:.0f}"))
        if not s.reconciled:
            out.append(("reconciliation_mismatch", "CRITICAL", "book vs broker mismatch - new entries stopped"))
        if s.api_errors >= t.max_api_errors:
            out.append(("api_errors", "WARN", f"{s.api_errors} broker API errors"))
        if s.db_write_failures >= t.max_db_write_failures:
            out.append(("db_write_failures", "CRITICAL", f"{s.db_write_failures} journal write failures"))
        if s.circuit_breaker:
            out.append(("circuit_breaker", "CRITICAL", f"circuit breaker tripped: {s.circuit_breaker}"))
        mem = rss_mb()
        if mem > t.max_rss_mb:
            out.append(("memory", "WARN", f"RSS {mem:.0f} MB > {t.max_rss_mb:.0f} MB"))
        return out

    def check(self, now: dt.datetime) -> list[Alert]:
        """Edge-triggered: returns alerts that just STARTED and alerts that
        just CLEARED; steady conditions return nothing."""
        current = {c: (sev, msg) for c, sev, msg in self._conditions(now)}
        out: list[Alert] = []
        for code, (sev, msg) in current.items():
            if code not in self._active:
                a = Alert(code, sev, msg, now)
                self._active[code] = a
                out.append(a)
            elif self._active[code].severity != sev:
                a = Alert(code, sev, msg, now)  # escalation/de-escalation is worth a line
                self._active[code] = a
                out.append(a)
        for code in list(self._active):
            if code not in current:
                prev = self._active.pop(code)
                out.append(Alert(code, prev.severity, f"recovered: {prev.message}", now, cleared=True))
        self.history.extend(out)
        del self.history[:-500]
        return out

    def active(self) -> list[Alert]:
        return sorted(self._active.values(), key=lambda a: (-SEVERITIES.index(a.severity), a.code))  # worst first

    def worst(self) -> str:
        sevs = [a.severity for a in self._active.values()]
        return max(sevs, key=SEVERITIES.index) if sevs else "OK"

    def heartbeat_due(self, now: dt.datetime) -> bool:
        if self._last_heartbeat is None or (now - self._last_heartbeat).total_seconds() >= self.t.heartbeat_seconds:
            self._last_heartbeat = now
            return True
        return False

    def snapshot(self, now: dt.datetime) -> dict:
        """The §87 dashboard row, one JSON line per heartbeat."""
        s = self.state
        return {
            "status": self.worst(), "active_alerts": [a.code for a in self.active()],
            "feed": {"connected": s.feed_connected, "tick_age_s": round((now - s.last_tick_at).total_seconds(), 1) if s.last_tick_at else None,
                     "reconnects": s.reconnects, "dropped": s.dropped_ticks, "clock_drift_s": s.clock_drift_seconds},
            "data": {"quality": dict(s.quality), "snapshots": s.snapshots},
            "strategy": {"decisions": s.decisions, "approved": s.approved, "rejections": dict(s.rejections_by_stage)},
            "execution": {"sent": s.orders_sent, "filled": s.orders_filled, "rejected": s.orders_rejected,
                          "latency_p95_ms": round(_p95(s.latencies_ms), 0), "slippage_mean": round(sum(s.slippages) / len(s.slippages), 3) if s.slippages else None},
            "risk": {"realized_today": round(s.realized_today, 2), "daily_cap": s.daily_cap, "open_risk": round(s.open_risk, 2),
                     "open_positions": s.open_positions},
            "broker": {"reconciled": s.reconciled, "api_errors": s.api_errors, "circuit_breaker": s.circuit_breaker},
            "process": {"rss_mb": round(rss_mb(), 1), "db_write_failures": s.db_write_failures},
        }


def format_alert(a: Alert, mode: str) -> str:
    head = "RECOVERED" if a.cleared else a.severity
    return f"[{head}] {mode} health: {a.message} ({a.code}) {a.ts:%H:%M:%S}"
