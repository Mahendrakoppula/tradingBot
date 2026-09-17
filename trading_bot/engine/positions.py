"""Position management (spec §31/§32 in the loop), reconciliation (§54,
§55), kill switches + abnormal-market circuit breaker (§56, §57), EOD
exit (§69) and trade results (§62).

`PositionBook` owns open positions and their ThesisMonitor state, turns
ExitSignals into exit OrderRequests, and closes a position into a
TradeResult with gross P&L, every cost component, net P&L, MFE/MAE and
the exit reason. `Reconciler` compares the book with the broker; any
mismatch stops new entries (§55). `KillSwitches` is the auditable set of
switches the gate and the monitor read (§56).
"""
import datetime as dt
from dataclasses import dataclass, field

from trading_bot.costs import CostRates
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.execution.broker import Broker, OrderRequest
from trading_bot.engine.execution.snapshot import SignalSnapshot
from trading_bot.engine.stops import ExitSignal, Plan, ThesisMonitor, TradeState

# --- §56 kill switches, §57 circuit breaker ------------------------------------------------


@dataclass
class KillEvent:
    ts: dt.datetime
    switch: str  # strategy | index | trading | emergency | circuit_breaker
    action: str  # on | off
    reason: str
    target: str | None = None  # strategy name / underlying for scoped switches


@dataclass
class KillSwitches:
    strategies: set = field(default_factory=set)
    indices: set = field(default_factory=set)
    trading: bool = False  # stops ALL new entries
    emergency: bool = False  # protect/cancel everything
    circuit_breaker: str | None = None  # §57 reason while tripped
    log: list[KillEvent] = field(default_factory=list)

    def _log(self, now, switch, action, reason, target=None):
        self.log.append(KillEvent(now, switch, action, reason, target))

    def kill_strategy(self, name: str, now: dt.datetime, reason: str) -> None:
        self.strategies.add(name); self._log(now, "strategy", "on", reason, name)

    def kill_index(self, underlying: str, now: dt.datetime, reason: str) -> None:
        self.indices.add(underlying); self._log(now, "index", "on", reason, underlying)

    def kill_trading(self, now: dt.datetime, reason: str) -> None:
        self.trading = True; self._log(now, "trading", "on", reason)

    def kill_emergency(self, now: dt.datetime, reason: str) -> None:
        self.emergency = True; self.trading = True; self._log(now, "emergency", "on", reason)

    def trip(self, now: dt.datetime, reason: str) -> None:
        """§57: STOP NEW ENTRIES -> protect -> log -> alert -> recover."""
        if self.circuit_breaker is None:
            self._log(now, "circuit_breaker", "on", reason)
        self.circuit_breaker = reason

    def reset_breaker(self, now: dt.datetime, reason: str = "recovered") -> None:
        if self.circuit_breaker is not None:
            self._log(now, "circuit_breaker", "off", reason)
        self.circuit_breaker = None

    def blocks_entry(self, strategy: str, underlying: str) -> str | None:
        if self.emergency:
            return "emergency_kill"
        if self.trading:
            return "trading_kill"
        if self.circuit_breaker:
            return f"circuit_breaker:{self.circuit_breaker}"
        if underlying in self.indices:
            return "index_kill"
        if strategy in self.strategies:
            return "strategy_kill"
        return None


def circuit_breaker_reason(*, quality: str, feed_connected: bool, reconciled: bool, api_errors_recent: int,
                           clock_drift_seconds: float | None, spread_pct: float | None = None,
                           max_api_errors: int = 5, max_clock_drift: float = 5.0, max_spread_pct: float = 8.0) -> str | None:
    """§57 triggers, first match wins. Returns None when the market/plumbing is normal."""
    if not feed_connected:
        return "feed_disconnected"
    if quality in ("STALE", "GAP", "INVALID", "DISCONNECTED"):
        return f"data_{quality.lower()}"
    if not reconciled:
        return "reconciliation_mismatch"
    if api_errors_recent >= max_api_errors:
        return "repeated_api_errors"
    if clock_drift_seconds is not None and abs(clock_drift_seconds) > max_clock_drift:
        return "clock_drift"
    if spread_pct is not None and spread_pct > max_spread_pct:
        return "abnormal_spread"
    return None


# --- positions ------------------------------------------------------------------------------


@dataclass
class Position:
    position_id: str
    signal_id: str
    setup_id: str | None
    snapshot: SignalSnapshot
    plan: Plan
    quantity: int  # currently open
    entry_price: float  # option average fill
    entry_ts: dt.datetime
    entry_bar: int
    state: TradeState
    fingerprint: str
    strategy: str
    strategy_version: str
    underlying: str
    token: str
    exchange: str
    tradingsymbol: str
    mfe: float = 0.0  # best unrealised, rupees
    mae: float = 0.0  # worst unrealised, rupees (negative)
    exits: list[dict] = field(default_factory=list)  # partial/final exits: {ts, qty, price, reason}
    pending_exit: OrderRequest | None = None
    pending_reason: str | None = None
    last_option_price: float | None = None
    execution_id: str = ""

    @property
    def closed(self) -> bool:
        return self.quantity <= 0

    def mark(self, option_bid: float) -> float:
        """Unrealised P&L at the bid (what we could get out at), and MFE/MAE upkeep."""
        self.last_option_price = option_bid
        upnl = (option_bid - self.entry_price) * self.quantity
        self.mfe = max(self.mfe, upnl)
        self.mae = min(self.mae, upnl)
        return upnl


@dataclass
class TradeResult:
    """§62 schema, one row per closed position."""
    trade_id: str
    signal_id: str
    setup_id: str | None
    execution_id: str
    underlying: str
    option_symbol: str
    strike: float
    expiry: str
    option_type: str
    strategy: str
    strategy_version: str
    fingerprint: str
    entry_ts: dt.datetime
    entry_price: float
    quantity: int
    initial_sl: float
    target: float
    exit_ts: dt.datetime
    exit_price: float  # quantity-weighted across partials
    exit_reason: str  # final exit's reason
    gross_pnl: float
    brokerage: float
    stt: float
    exchange_charges: float
    sebi: float
    gst: float
    stamp_duty: float
    spread_cost: float
    slippage: float
    total_cost: float
    net_pnl: float
    mfe: float
    mae: float
    holding_minutes: float
    regime: str
    trend: str
    signal_score: int
    expected_underlying_move: float
    actual_underlying_move: float
    expected_option_move: float
    actual_option_move: float
    iv: float | None
    delta: float | None
    exits: list[dict] = field(default_factory=list)
    status: str = "closed"

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["entry_ts"] = self.entry_ts.isoformat()
        d["exit_ts"] = self.exit_ts.isoformat()
        return d


class PositionBook:
    def __init__(self, monitor: ThesisMonitor, rates: CostRates):
        self.monitor = monitor
        self.rates = rates
        self.open: dict[str, Position] = {}
        self.closed: list[TradeResult] = []
        self._seq = 0

    # --- open / mark / manage -------------------------------------------------------------

    def open_position(self, snap: SignalSnapshot, plan: Plan, execution_id: str, filled_qty: int, avg_price: float,
                      now: dt.datetime, bar_index: int, entry_vwap: float | None) -> Position:
        self._seq += 1
        pid = f"pos-{self._seq:04d}"
        state = self.monitor.open(plan, filled_qty, bar_index, entry_vwap=entry_vwap)
        pos = Position(pid, snap.signal_id, snap.setup_id, snap, plan, filled_qty, avg_price, now, bar_index, state,
                       snap.fingerprint, snap.strategy, snap.strategy_version, snap.underlying, snap.option_token,
                       snap.option_exchange, snap.option_symbol)
        pos.execution_id = execution_id
        self.open[pid] = pos
        return pos

    def by_underlying(self, underlying: str) -> list[Position]:
        return [p for p in self.open.values() if p.underlying == underlying]

    def open_directions(self) -> dict[str, str]:
        return {p.underlying: p.plan.direction for p in self.open.values()}

    def open_risk(self) -> float:
        return sum(max(0.0, (p.entry_price - p.plan.option_stop)) * p.quantity for p in self.open.values())

    def manage(self, pos: Position, ctx: ContextSnapshot, *, option_bid: float, option_spread_pct: float | None,
               eod: bool, risk_limit_hit: bool, kills: KillSwitches) -> ExitSignal | None:
        """Run the thesis monitor for one position at a bar close (or on a
        tick for the hard stop) and return the exit to place, if any."""
        pos.mark(option_bid)
        if pos.pending_exit is not None:
            return None  # an exit is already in flight; never stack exits
        pos.state.quantity = pos.quantity
        # a trading kill only stops NEW entries; scoped strategy/index kills and the emergency kill close positions
        kill = pos.strategy in kills.strategies or pos.underlying in kills.indices
        return self.monitor.check(ctx, pos.state, option_bid=option_bid, option_spread_pct=option_spread_pct, eod=eod,
                                  risk_limit_hit=risk_limit_hit, kill=kill, emergency=kills.emergency)

    def exit_request(self, pos: Position, sig: ExitSignal, *, bid: float, buffer_pct: float = 0.5) -> OrderRequest:
        qty = min(sig.quantity, pos.quantity)
        limit = round(bid * (1.0 - buffer_pct / 100.0), 2)
        purpose = "EXIT" if qty >= pos.quantity else "PARTIAL_EXIT"
        n = len(pos.exits) + 1
        req = OrderRequest(f"{pos.signal_id}:{purpose}:{n}", pos.signal_id, pos.setup_id, pos.token, pos.exchange,
                           pos.tradingsymbol, "SELL", qty, "LIMIT", limit, limit, purpose=purpose,
                           timeout_seconds=15 if sig.reason not in ("EOD_EXIT", "EMERGENCY_EXIT") else 8)
        pos.pending_exit = req
        pos.pending_reason = sig.reason
        return req

    def exit_abandoned(self, pos: Position) -> None:
        pos.pending_exit = None

    def on_exit_filled(self, pos: Position, filled_qty: int, avg_price: float, now: dt.datetime, *,
                       spot_now: float, ctx: ContextSnapshot | None = None) -> TradeResult | None:
        reason = pos.pending_reason or "UNKNOWN"
        pos.exits.append({"ts": now.isoformat(), "qty": filled_qty, "price": avg_price, "reason": reason})
        pos.quantity -= filled_qty
        pos.pending_exit = None
        if pos.quantity > 0:
            return None
        return self._close(pos, now, spot_now, ctx)

    def _close(self, pos: Position, now: dt.datetime, spot_now: float, ctx: ContextSnapshot | None) -> TradeResult:
        qty = sum(e["qty"] for e in pos.exits)
        exit_px = sum(e["qty"] * e["price"] for e in pos.exits) / qty if qty else pos.entry_price
        gross = sum((e["price"] - pos.entry_price) * e["qty"] for e in pos.exits)
        # §33 full component breakdown for the round trip
        from trading_bot.engine.risk_engine import _cost_components
        spread_pts = max(0.0, pos.snapshot.ask - pos.snapshot.bid)
        costs = _cost_components(self.rates, pos.entry_price, exit_px, qty, spread_pts, 0.5 * spread_pts)
        # brokerage: one buy + one sell per exit leg
        extra_legs = max(0, len(pos.exits) - 1)
        brokerage = costs.brokerage + extra_legs * self.rates.brokerage_per_order
        gst = costs.gst + extra_legs * self.rates.brokerage_per_order * self.rates.gst_pct / 100.0
        total = costs.total - costs.brokerage - costs.gst + brokerage + gst
        net = gross - total
        snap = pos.snapshot
        exp_u = snap.remaining_move_points
        act_u = (spot_now - snap.underlying_price) * (1 if snap.direction == "up" else -1)
        result = TradeResult(
            trade_id=pos.position_id, signal_id=pos.signal_id, setup_id=pos.setup_id, execution_id=pos.execution_id,
            underlying=pos.underlying, option_symbol=pos.tradingsymbol, strike=snap.strike, expiry=snap.expiry,
            option_type=snap.option_type, strategy=pos.strategy, strategy_version=pos.strategy_version,
            fingerprint=pos.fingerprint, entry_ts=pos.entry_ts, entry_price=pos.entry_price, quantity=qty,
            initial_sl=pos.plan.option_stop, target=pos.plan.option_target1, exit_ts=now, exit_price=round(exit_px, 2),
            exit_reason=pos.exits[-1]["reason"], gross_pnl=round(gross, 2), brokerage=round(brokerage, 2), stt=costs.stt,
            exchange_charges=costs.exchange, sebi=costs.sebi, gst=round(gst, 2), stamp_duty=costs.stamp, spread_cost=costs.spread,
            slippage=costs.slippage, total_cost=round(total, 2), net_pnl=round(net, 2), mfe=round(pos.mfe, 2), mae=round(pos.mae, 2),
            holding_minutes=round((now - pos.entry_ts).total_seconds() / 60.0, 1), regime=snap.regime, trend=snap.trend,
            signal_score=snap.score, expected_underlying_move=exp_u, actual_underlying_move=round(act_u, 2),
            expected_option_move=round(snap.option_target1 - snap.option_entry, 2),
            actual_option_move=round(exit_px - pos.entry_price, 2), iv=snap.iv, delta=snap.delta, exits=list(pos.exits),
        )
        self.closed.append(result)
        del self.open[pos.position_id]
        return result

    # --- §69 EOD sweep ----------------------------------------------------------------------------

    def eod_exit_requests(self, bids: dict[str, float]) -> list[tuple[Position, OrderRequest]]:
        out = []
        for pos in list(self.open.values()):
            if pos.pending_exit is not None:
                continue
            bid = bids.get(pos.token)
            if bid is None:
                continue
            out.append((pos, self.exit_request(pos, ExitSignal("EOD_EXIT", pos.quantity), bid=bid, buffer_pct=1.0)))
        return out


# --- §54/§55 reconciliation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconcileResult:
    ok: bool
    mismatches: tuple[str, ...]
    checked_at: dt.datetime


def reconcile(book: PositionBook, broker: Broker, now: dt.datetime, *, price_tolerance: float = 0.05) -> ReconcileResult:
    """Bot expected state vs broker actual state (§55): positions and
    quantities per token, average prices, and open orders the book does
    not know about. Any mismatch -> not ok -> gate blocks new entries."""
    issues: list[str] = []
    expected: dict[str, tuple[int, float]] = {}
    for p in book.open.values():
        q, px = expected.get(p.token, (0, 0.0))
        expected[p.token] = (q + p.quantity, p.entry_price)
    actual = {bp.token: (bp.quantity, bp.average_price) for bp in broker.positions() if bp.quantity != 0}
    for token, (q, px) in expected.items():
        aq, apx = actual.get(token, (0, 0.0))
        if aq != q:
            issues.append(f"{token}: book {q} vs broker {aq}")
        elif abs(apx - px) > price_tolerance:
            issues.append(f"{token}: avg price book {px} vs broker {apx}")
    for token, (aq, _) in actual.items():
        if token not in expected:
            issues.append(f"{token}: broker holds {aq}, book has none")
    known_tags = {p.pending_exit.client_order_id for p in book.open.values() if p.pending_exit is not None}
    for bo in broker.open_orders():
        if bo.request.purpose in ("EXIT", "PARTIAL_EXIT") and bo.request.client_order_id not in known_tags:
            issues.append(f"unknown open order {bo.broker_order_id}")
    return ReconcileResult(not issues, tuple(issues), now)
