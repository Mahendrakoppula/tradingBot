"""Realistic paper broker (spec §52). Three profiles:

    realistic     fill at ask (+ slippage share of spread), latency, partial
                  fills on large size, occasional rejects/timeouts
    conservative  worse: full spread + extra slippage, more partials/timeouts
    ideal         fill at mid instantly - "must never be used as proof of live
                  readiness" (§52) and is labelled as such in every fill

Fills are driven by the prices the loop feeds through `set_quote()` (the
same option quotes the engines see), so replay and live paper are the
same code. Randomness (partials/rejects) comes from a seeded RNG so a
paper session is reproducible.
"""
import datetime as dt
import random
from dataclasses import dataclass, field

from trading_bot.engine.execution.broker import BrokerOrder, BrokerPosition, Fill, OrderRequest

PROFILES = ("realistic", "conservative", "ideal")


@dataclass(frozen=True)
class PaperParams:
    profile: str = "realistic"
    latency_ms: int = 350
    slippage_pct_of_spread: float = 0.5
    extra_slippage_pct: float = 0.0  # of price, conservative adds some
    partial_fill_threshold_qty: int = 300  # above this a first fill may be partial
    partial_first_fraction: float = 0.5
    reject_probability: float = 0.01
    timeout_probability: float = 0.01
    seed: int = 7

    @classmethod
    def for_profile(cls, profile: str, seed: int = 7) -> "PaperParams":
        if profile == "ideal":
            return cls("ideal", 0, 0.0, 0.0, 10**9, 1.0, 0.0, 0.0, seed)
        if profile == "conservative":
            return cls("conservative", 800, 1.0, 0.25, 150, 0.4, 0.03, 0.03, seed)
        return cls("realistic", seed=seed)


@dataclass
class _Live:
    order: BrokerOrder
    ack_at: dt.datetime
    next_fill_at: dt.datetime | None = None


class PaperBroker:
    name = "paper"

    def __init__(self, params: PaperParams | None = None):
        self.p = params or PaperParams()
        self.rng = random.Random(self.p.seed)
        self._orders: dict[str, BrokerOrder] = {}
        self._live: dict[str, _Live] = {}
        self._positions: dict[str, BrokerPosition] = {}
        self._quotes: dict[str, tuple[float, float, float]] = {}  # token -> (bid, ask, ltp)
        self._seq = 0
        self.realized_pnl = 0.0
        self.trade_log: list[dict] = []

    # --- market data in ------------------------------------------------------------

    def set_quote(self, token: str, bid: float, ask: float, ltp: float | None = None) -> None:
        self._quotes[token] = (bid, ask, ltp if ltp is not None else 0.5 * (bid + ask))

    def quote(self, token: str):
        return self._quotes.get(token)

    # --- Broker interface -----------------------------------------------------------------

    def place(self, req: OrderRequest, now: dt.datetime) -> BrokerOrder:
        for existing in self._orders.values():
            if existing.request.client_order_id == req.client_order_id:
                return existing  # idempotent on the tag (§45/§59): the same request never creates a second order
        self._seq += 1
        oid = f"P{self._seq:06d}"
        bo = BrokerOrder(oid, req, "SENT", sent_at=now)
        self._orders[oid] = bo
        q = self._quotes.get(req.token)
        if q is None:
            bo.status, bo.reason, bo.completed_at = "REJECTED", "no_quote", now
            return bo
        if self.rng.random() < self.p.reject_probability:
            bo.status, bo.reason, bo.completed_at = "REJECTED", "simulated_broker_reject", now
            return bo
        ack_at = now + dt.timedelta(milliseconds=self.p.latency_ms)
        live = _Live(bo, ack_at)  # the ack is REPORTED only once poll() passes ack_at
        if self.rng.random() < self.p.timeout_probability:
            live.next_fill_at = None  # never fills; the state machine times it out
        else:
            live.next_fill_at = ack_at + dt.timedelta(milliseconds=self.p.latency_ms)
        self._live[oid] = live
        return bo

    def _fill_price(self, req: OrderRequest, q: tuple[float, float, float]) -> float | None:
        bid, ask, _ = q
        spread = max(0.0, ask - bid)
        if self.p.profile == "ideal":
            px = 0.5 * (bid + ask)
        elif req.side == "BUY":
            px = ask + self.p.slippage_pct_of_spread * spread * 0.5 + self.p.extra_slippage_pct / 100.0 * ask
        else:
            px = bid - self.p.slippage_pct_of_spread * spread * 0.5 - self.p.extra_slippage_pct / 100.0 * bid
        px = round(max(0.05, px), 2)
        if req.order_type == "LIMIT" and req.limit_price is not None:
            if req.side == "BUY" and px > req.limit_price:
                return None  # would not fill at this price
            if req.side == "SELL" and px < req.limit_price:
                return None
        if req.max_price is not None:
            if req.side == "BUY" and px > req.max_price:
                return None
            if req.side == "SELL" and px < req.max_price:
                return None
        return px

    def poll(self, broker_order_id: str, now: dt.datetime) -> BrokerOrder:
        bo = self._orders[broker_order_id]
        live = self._live.get(broker_order_id)
        if live is None or bo.status in ("FILLED", "REJECTED", "CANCELLED", "TIMEOUT", "FAILED"):
            return bo
        if bo.status == "SENT" and now >= live.ack_at:
            bo.status, bo.acknowledged_at = "ACKNOWLEDGED", live.ack_at
        if live.next_fill_at is None or now < live.next_fill_at:
            return bo
        q = self._quotes.get(bo.request.token)
        if q is None:
            return bo
        px = self._fill_price(bo.request, q)
        if px is None:
            return bo  # resting; the state machine's timeout will cancel it
        remaining = bo.request.quantity - bo.filled_quantity
        if bo.filled_quantity == 0 and remaining > self.p.partial_fill_threshold_qty and self.p.profile != "ideal":
            qty = max(1, int(remaining * self.p.partial_first_fraction))
            live.next_fill_at = now + dt.timedelta(milliseconds=self.p.latency_ms * 2)
        else:
            qty = remaining
        self._record_fill(bo, qty, px, now)
        return bo

    def _record_fill(self, bo: BrokerOrder, qty: int, px: float, now: dt.datetime) -> None:
        bo.fills.append(Fill(now, qty, px))
        total_qty = bo.filled_quantity + qty
        bo.average_price = round(((bo.average_price or 0.0) * bo.filled_quantity + px * qty) / total_qty, 2)
        bo.filled_quantity = total_qty
        req = bo.request
        pos = self._positions.get(req.token)
        signed = qty if req.side == "BUY" else -qty
        if pos is None:
            self._positions[req.token] = BrokerPosition(req.token, req.tradingsymbol, signed, px)
        else:
            if (pos.quantity > 0) == (signed > 0) or pos.quantity == 0:
                new_q = pos.quantity + signed
                pos.average_price = round((pos.average_price * abs(pos.quantity) + px * qty) / max(1, abs(new_q)), 2)
                pos.quantity = new_q
            else:
                closed = min(abs(pos.quantity), qty)
                pnl = (px - pos.average_price) * closed * (1 if pos.quantity > 0 else -1)
                self.realized_pnl += pnl
                self.trade_log.append({"ts": now.isoformat(), "token": req.token, "qty": closed, "entry": pos.average_price,
                                       "exit": px, "gross_pnl": round(pnl, 2), "profile": self.p.profile})
                pos.quantity += signed
                if pos.quantity == 0:
                    del self._positions[req.token]
        if bo.filled_quantity >= req.quantity:
            bo.status, bo.completed_at = "FILLED", now
            self._live.pop(bo.broker_order_id, None)
        else:
            bo.status = "PARTIALLY_FILLED"

    def cancel(self, broker_order_id: str, now: dt.datetime) -> BrokerOrder:
        bo = self._orders[broker_order_id]
        if bo.status in ("FILLED", "REJECTED", "CANCELLED", "TIMEOUT", "FAILED"):
            return bo
        bo.status, bo.completed_at = "CANCELLED", now
        bo.reason = bo.reason or "cancelled"
        self._live.pop(broker_order_id, None)
        return bo

    def open_orders(self) -> list[BrokerOrder]:
        return [bo for bo in self._orders.values() if bo.status in ("SENT", "ACKNOWLEDGED", "PARTIALLY_FILLED")]

    def positions(self) -> list[BrokerPosition]:
        return list(self._positions.values())

    def orders(self) -> list[BrokerOrder]:
        return list(self._orders.values())
