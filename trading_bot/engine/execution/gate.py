"""Execution gate (spec §41) - the dedicated firewall between an approved
signal and an order. Every check in the spec's list, in order, each with
a reason code; the first failure stops the order. Mandatory (§92 #37).

    Risk Engine -> Execution Gate -> Execution Engine -> Broker
"""
import datetime as dt
from dataclasses import dataclass, field

from trading_bot.engine.execution.broker import Broker
from trading_bot.engine.execution.orders import OrderStateMachine
from trading_bot.engine.execution.snapshot import DriftParams, DriftVerdict, SignalSnapshot, drift_check
from trading_bot.engine.risk_engine import RiskDecision


@dataclass(frozen=True)
class GateParams:
    drift: DriftParams = DriftParams()
    max_spread_pct: float = 2.0
    min_bid_qty: int = 1
    min_ask_qty: int = 1
    require_quality_ok: bool = True
    require_feed_connected: bool = True
    require_reconciled: bool = True
    entry_buffer_pct: float = 0.25  # LIMIT price = ask * (1 + buffer) - fills fast, caps the worst case


@dataclass
class GateResult:
    passed: bool
    reason_code: str | None
    limit_price: float | None
    max_price: float | None
    quantity: int
    drift: DriftVerdict | None
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def note(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))


def execution_gate(snap: SignalSnapshot, risk: RiskDecision, *, now: dt.datetime, spot_now: float, atr: float,
                   bid: float, ask: float, bid_qty: int, ask_qty: int, quality: str, feed_connected: bool,
                   reconciled: bool, broker: Broker, osm: OrderStateMachine, kill_new_entries: bool,
                   eod_cutoff_passed: bool, params: GateParams | None = None) -> GateResult:
    p = params or GateParams()
    res = GateResult(False, None, None, None, 0, None)

    def fail(code: str, detail: str = "") -> GateResult:
        res.note(code, False, detail)
        res.reason_code = code
        return res

    # kill switches / session
    if kill_new_entries:
        return fail("new_entries_killed")
    if eod_cutoff_passed:
        return fail("after_eod_cutoff")
    res.note("kill_switches", True)
    # risk decision
    if not risk.approved or risk.quantity <= 0:
        return fail("risk_not_approved", risk.reason_code or "")
    res.note("risk_approved", True, risk.decision)
    # data quality / connectivity
    if p.require_quality_ok and quality != "OK":
        return fail("data_quality", quality)
    if p.require_feed_connected and not feed_connected:
        return fail("feed_disconnected")
    if p.require_reconciled and not reconciled:
        return fail("reconciliation_mismatch")
    res.note("data_and_connectivity", True)
    # circuit breaker (§46)
    if osm.circuit_breaker_tripped:
        return fail("execution_circuit_breaker")
    # freshness + drift + spread (§39/§40)
    dv = drift_check(snap, now=now, spot_now=spot_now, bid_now=bid, ask_now=ask, atr=atr, params=p.drift)
    res.drift = dv
    if dv.action != "PROCEED":
        return fail(f"drift_{dv.action.lower()}", dv.reason_code or "")
    res.note("snapshot_fresh_and_price_ok", True, f"u_drift={dv.underlying_drift} o_drift={dv.option_drift_pct}%")
    mid = 0.5 * (bid + ask)
    spread_pct = (ask - bid) / mid * 100.0 if mid > 0 else float("inf")
    if spread_pct > p.max_spread_pct:
        return fail("spread_too_wide", f"{spread_pct:.2f}%")
    if bid_qty < p.min_bid_qty or ask_qty < p.min_ask_qty:
        return fail("no_depth")
    res.note("liquidity", True, f"spread={spread_pct:.2f}% depth={bid_qty}/{ask_qty}")
    # duplicate protection (§45)
    dup = osm.duplicate_check(snap.signal_id, snap.setup_id, snap.option_token, "ENTRY")
    if dup:
        return fail("duplicate", dup)
    res.note("duplicate_protection", True)
    # broker connectivity: a quote must exist for the token
    if broker.quote(snap.option_token) is None:
        return fail("broker_no_quote")
    res.note("broker_connectivity", True)
    # final numbers
    limit = round(ask * (1.0 + p.entry_buffer_pct / 100.0), 2)
    res.passed = True
    res.limit_price = limit
    res.max_price = limit
    res.quantity = risk.quantity
    return res
