"""Order state machine (spec §42), timeouts (§43), partial fills (§44),
duplicate protection (§45) and latency measurement (§46).

    SIGNAL_READY -> RISK_APPROVED -> EXECUTION_PENDING -> ORDER_SENT ->
    ORDER_ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED -> POSITION_ACTIVE
    alternatives: REJECTED, CANCELLED, TIMEOUT, FAILED

"Never treat an order ID as confirmation of fill" - the record moves to
FILLED only on the broker's reported fills, and `actual_exposure` is
always the FILLED quantity, never the requested one.
"""
import datetime as dt
from dataclasses import dataclass, field

from trading_bot.engine.execution.broker import Broker, BrokerOrder, OrderRequest

ORDER_STATES: tuple[str, ...] = (
    "SIGNAL_READY", "RISK_APPROVED", "EXECUTION_PENDING", "ORDER_SENT", "ORDER_ACKNOWLEDGED", "PARTIALLY_FILLED",
    "FILLED", "POSITION_ACTIVE", "REJECTED", "CANCELLED", "TIMEOUT", "FAILED",
)
TERMINAL = frozenset({"POSITION_ACTIVE", "REJECTED", "CANCELLED", "TIMEOUT", "FAILED"})

_ALLOWED = {
    "SIGNAL_READY": {"RISK_APPROVED", "REJECTED"},
    "RISK_APPROVED": {"EXECUTION_PENDING", "REJECTED"},
    "EXECUTION_PENDING": {"ORDER_SENT", "REJECTED", "FAILED"},
    "ORDER_SENT": {"ORDER_ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "REJECTED", "CANCELLED", "TIMEOUT", "FAILED"},
    "ORDER_ACKNOWLEDGED": {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "TIMEOUT", "REJECTED", "FAILED"},
    "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "TIMEOUT", "FAILED"},
    "FILLED": {"POSITION_ACTIVE"},
}


@dataclass
class Latency:
    confirmation_at: dt.datetime | None = None  # bar close that confirmed
    decision_at: dt.datetime | None = None  # risk approved
    request_at: dt.datetime | None = None  # order sent
    ack_at: dt.datetime | None = None
    fill_at: dt.datetime | None = None

    def ms(self, a: str, b: str) -> int | None:
        x, y = getattr(self, a), getattr(self, b)
        return int((y - x).total_seconds() * 1000) if (x is not None and y is not None) else None

    def as_dict(self) -> dict:
        return {
            "confirmation_to_decision_ms": self.ms("confirmation_at", "decision_at"),
            "decision_to_request_ms": self.ms("decision_at", "request_at"),
            "request_to_ack_ms": self.ms("request_at", "ack_at"),
            "ack_to_fill_ms": self.ms("ack_at", "fill_at"),
            "signal_to_fill_ms": self.ms("confirmation_at", "fill_at"),
        }


@dataclass
class OrderRecord:
    execution_id: str
    signal_id: str
    setup_id: str | None
    request: OrderRequest | None = None
    state: str = "SIGNAL_READY"
    broker_order_id: str | None = None
    filled_quantity: int = 0
    average_price: float | None = None
    reason: str | None = None
    latency: Latency = field(default_factory=Latency)
    history: list[tuple[str, str, str]] = field(default_factory=list)  # (iso ts, state, note)
    cancel_requested: bool = False

    @property
    def actual_exposure(self) -> int:
        return self.filled_quantity  # §44: never the requested quantity

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL


class DuplicateOrderError(RuntimeError):
    pass


class OrderStateMachine:
    """Owns OrderRecords and drives them against a Broker. One instance per
    process; `active` is the §45 duplicate guard's memory."""

    def __init__(self, broker: Broker, *, latency_threshold_ms: int = 5000):
        self.broker = broker
        self.records: dict[str, OrderRecord] = {}
        self.latency_threshold_ms = latency_threshold_ms
        self.circuit_breaker_tripped = False

    # --- transitions ------------------------------------------------------------------

    def _move(self, rec: OrderRecord, state: str, now: dt.datetime, note: str = "") -> None:
        if state not in _ALLOWED.get(rec.state, set()) and state != rec.state:
            raise ValueError(f"illegal transition {rec.state} -> {state} ({rec.execution_id})")
        rec.state = state
        rec.history.append((now.isoformat(), state, note))

    def new(self, execution_id: str, signal_id: str, setup_id: str | None, confirmation_at: dt.datetime) -> OrderRecord:
        if execution_id in self.records:
            raise DuplicateOrderError(f"execution {execution_id} already exists")
        rec = OrderRecord(execution_id, signal_id, setup_id)
        rec.latency.confirmation_at = confirmation_at
        rec.history.append((confirmation_at.isoformat(), "SIGNAL_READY", ""))
        self.records[execution_id] = rec
        return rec

    def risk_approved(self, rec: OrderRecord, now: dt.datetime) -> None:
        rec.latency.decision_at = now
        self._move(rec, "RISK_APPROVED", now)

    def reject(self, rec: OrderRecord, now: dt.datetime, reason: str) -> None:
        rec.reason = reason
        self._move(rec, "REJECTED", now, reason)

    # --- §45 duplicate protection ----------------------------------------------------------

    def duplicate_check(self, signal_id: str, setup_id: str | None, token: str, purpose: str = "ENTRY") -> str | None:
        """Reason code when an order must NOT be placed, else None."""
        for r in self.records.values():
            if r.request is None or r.request.purpose != purpose:
                continue
            same_signal = r.signal_id == signal_id
            same_setup = setup_id is not None and r.setup_id == setup_id
            same_token_live = r.request.token == token and not r.terminal
            if (same_signal or same_setup) and (not r.terminal or r.state == "POSITION_ACTIVE"):
                return "duplicate_signal_or_setup"
            if purpose == "ENTRY" and same_token_live:
                return "order_already_open_for_token"
        if purpose == "ENTRY":
            for bo in self.broker.open_orders():
                if bo.request.token == token and bo.request.side == "BUY":
                    return "broker_has_open_buy_for_token"
            for pos in self.broker.positions():
                if pos.token == token and pos.quantity > 0:
                    return "broker_already_long_token"
        return None

    # --- send / poll / timeout -------------------------------------------------------------------

    def send(self, rec: OrderRecord, req: OrderRequest, now: dt.datetime) -> OrderRecord:
        dup = self.duplicate_check(rec.signal_id, rec.setup_id, req.token, req.purpose)
        if dup:
            rec.request = req
            rec.reason = dup
            self._move(rec, "EXECUTION_PENDING", now)
            self._move(rec, "REJECTED", now, dup)
            return rec
        rec.request = req
        self._move(rec, "EXECUTION_PENDING", now)
        rec.latency.request_at = now
        try:
            bo = self.broker.place(req, now)
        except Exception as exc:  # noqa: BLE001 - broker failure is a terminal FAILED, never a silent retry
            rec.reason = f"place_failed: {exc}"
            self._move(rec, "ORDER_SENT", now)
            self._move(rec, "FAILED", now, rec.reason)
            return rec
        rec.broker_order_id = bo.broker_order_id
        self._move(rec, "ORDER_SENT", now, bo.broker_order_id)
        self._apply(rec, bo, now)
        return rec

    def poll(self, rec: OrderRecord, now: dt.datetime) -> OrderRecord:
        if rec.terminal or rec.broker_order_id is None:
            return rec
        bo = self.broker.poll(rec.broker_order_id, now)
        self._apply(rec, bo, now)
        if not rec.terminal and rec.request is not None and rec.latency.request_at is not None:
            if (now - rec.latency.request_at).total_seconds() > rec.request.timeout_seconds and not rec.cancel_requested:
                # §43: cancel, then the caller REVALIDATES (reprice or reject) - never resend blindly
                rec.cancel_requested = True
                bo = self.broker.cancel(rec.broker_order_id, now)
                self._apply(rec, bo, now, timeout=True)
        return rec

    def _apply(self, rec: OrderRecord, bo: BrokerOrder, now: dt.datetime, timeout: bool = False) -> None:
        if bo.acknowledged_at and rec.latency.ack_at is None:
            rec.latency.ack_at = bo.acknowledged_at
            if rec.state == "ORDER_SENT":
                self._move(rec, "ORDER_ACKNOWLEDGED", now)
        if bo.filled_quantity > rec.filled_quantity:
            rec.filled_quantity = bo.filled_quantity
            rec.average_price = bo.average_price
            if bo.status == "FILLED":
                rec.latency.fill_at = bo.completed_at or now
                self._move(rec, "FILLED", now, f"{bo.filled_quantity}@{bo.average_price}")
                self._move(rec, "POSITION_ACTIVE", now)
                self._check_latency(rec)
                return
            self._move(rec, "PARTIALLY_FILLED", now, f"{bo.filled_quantity}/{bo.request.quantity}")
        if bo.status in ("REJECTED", "CANCELLED", "TIMEOUT", "FAILED") and not rec.terminal:
            if rec.filled_quantity > 0:
                # a partial that was then cancelled is a live position of the filled size (§44)
                rec.latency.fill_at = rec.latency.fill_at or now
                self._move(rec, "FILLED", now, f"partial final {rec.filled_quantity}")
                self._move(rec, "POSITION_ACTIVE", now, "partial")
                return
            rec.reason = "timeout" if timeout else (bo.reason or bo.status.lower())
            self._move(rec, "TIMEOUT" if timeout else bo.status, now, rec.reason)

    def _check_latency(self, rec: OrderRecord) -> None:
        total = rec.latency.ms("request_at", "fill_at")
        if total is not None and total > self.latency_threshold_ms:
            self.circuit_breaker_tripped = True  # §46: abnormal latency -> execution circuit breaker

    def active_entries(self) -> list[OrderRecord]:
        return [r for r in self.records.values() if r.request is not None and r.request.purpose == "ENTRY" and not r.terminal]
