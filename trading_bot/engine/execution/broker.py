"""Broker interface (spec §47): strategies and risk never see a broker;
the execution engine talks to this abstraction and adapters implement it.

Only order-LEVEL operations; the adapter owns retries/idempotency and
must never resubmit an order blindly (§48, §92 #59). `client_order_id`
is the idempotency key (Angel's `ordertag`) and doubles as the §45
duplicate guard.
"""
import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str  # unique per (signal, leg); used as the broker order tag
    signal_id: str
    setup_id: str | None
    token: str
    exchange: str  # NFO | BFO
    tradingsymbol: str
    side: str  # "BUY" | "SELL"
    quantity: int
    order_type: str  # "LIMIT" | "MARKET"
    limit_price: float | None
    max_price: float | None  # BUY: never pay above; SELL: never accept below
    product: str = "INTRADAY"
    timeout_seconds: int = 20
    purpose: str = "ENTRY"  # ENTRY | EXIT | PARTIAL_EXIT


@dataclass
class Fill:
    ts: dt.datetime
    quantity: int
    price: float


@dataclass
class BrokerOrder:
    """What the broker reports about an order. `status` uses the §42
    vocabulary: SENT, ACKNOWLEDGED, PARTIALLY_FILLED, FILLED, REJECTED,
    CANCELLED, TIMEOUT, FAILED."""
    broker_order_id: str
    request: OrderRequest
    status: str
    filled_quantity: int = 0
    average_price: float | None = None
    fills: list[Fill] = field(default_factory=list)
    reason: str | None = None
    sent_at: dt.datetime | None = None
    acknowledged_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None

    @property
    def remaining(self) -> int:
        return self.request.quantity - self.filled_quantity

    @property
    def terminal(self) -> bool:
        return self.status in ("FILLED", "REJECTED", "CANCELLED", "TIMEOUT", "FAILED")


@dataclass
class BrokerPosition:
    token: str
    tradingsymbol: str
    quantity: int  # signed: long > 0
    average_price: float


class Broker(Protocol):
    name: str

    def place(self, req: OrderRequest, now: dt.datetime) -> BrokerOrder: ...
    def cancel(self, broker_order_id: str, now: dt.datetime) -> BrokerOrder: ...
    def poll(self, broker_order_id: str, now: dt.datetime) -> BrokerOrder:
        """Latest state; adapters may fill/expire orders on poll."""
    def open_orders(self) -> list[BrokerOrder]: ...
    def positions(self) -> list[BrokerPosition]: ...
    def quote(self, token: str) -> tuple[float, float, float] | None:
        """(bid, ask, ltp) or None - the final pre-order price check (§38/§40)."""
