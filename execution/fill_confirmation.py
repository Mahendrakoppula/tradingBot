"""Realistic fill confirmation (spec: never assume LTP=fill, never
assume an order succeeded without broker confirmation). Polls the
broker's own order book for a specific order id until it reaches a
terminal status or a wait budget is exhausted - the only source of
truth for what actually happened to an order is the broker, never the
price the strategy computed at signal time.
"""
import time
from dataclasses import dataclass

TERMINAL_STATUSES = {"complete", "rejected", "cancelled"}

DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_WAIT_SECONDS = 30.0


@dataclass
class FillResult:
    order_id: str
    status: str  # "COMPLETE" | "REJECTED" | "CANCELLED" | "PENDING" (wait budget exhausted, still not terminal)
    filled_quantity: int
    average_price: float | None
    rejection_reason: str | None = None


def confirm_fill(
    order_client,
    order_id: str,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    sleep_fn=time.sleep,
) -> FillResult:
    """PENDING (not an exception, not a false success) if the wait
    budget runs out before the broker reports a terminal status -
    callers must treat PENDING as "still don't know", never as either
    a fill or a failure."""
    elapsed = 0.0
    while True:
        order_book = order_client.get_order_book()
        order = next((o for o in order_book if o.get("orderid") == order_id), None)
        if order is not None:
            status = str(order.get("status", "")).lower()
            if status in TERMINAL_STATUSES:
                avg_price_raw = order.get("averageprice")
                return FillResult(
                    order_id=order_id,
                    status=status.upper(),
                    filled_quantity=int(order.get("filledshares") or 0),
                    average_price=float(avg_price_raw) if avg_price_raw else None,
                    rejection_reason=order.get("text") if status == "rejected" else None,
                )
        if elapsed >= max_wait_seconds:
            return FillResult(order_id=order_id, status="PENDING", filled_quantity=0, average_price=None)
        sleep_fn(poll_interval_seconds)
        elapsed += poll_interval_seconds
