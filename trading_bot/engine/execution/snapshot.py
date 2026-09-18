"""Signal snapshot lock (spec §39) and maximum entry drift (§40).

At TRADE_READY the whole decision is frozen into an immutable snapshot -
prices, option, Greeks, SL, target, quantity, risk. The execution gate
compares the market NOW against that snapshot; if it has moved materially
the snapshot is INVALIDATED and the pipeline must recalculate and
revalidate rather than trade stale numbers (§92 #33, #38).
"""
import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SignalSnapshot:
    signal_id: str
    setup_id: str | None
    ts: dt.datetime
    underlying: str
    underlying_price: float
    direction: str
    strategy: str
    strategy_version: str
    trend: str
    regime: str
    structure: str
    score: int
    expected_move_points: float
    remaining_move_points: float
    option_token: str
    option_symbol: str
    option_exchange: str
    option_type: str
    strike: float
    expiry: str
    lot_size: int
    bid: float
    ask: float
    spread_pct: float
    iv: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    option_entry: float
    option_stop: float
    option_target1: float
    option_target2: float
    stop_ref: float
    target1_ref: float
    quantity: int
    risk_rupees: float
    fingerprint: str
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["ts"] = self.ts.isoformat()
        return d


def lock_snapshot(**fields) -> SignalSnapshot:
    return SignalSnapshot(**fields)


@dataclass(frozen=True)
class DriftParams:
    max_underlying_drift_atr: float = 0.35  # spot moved this far from the snapshot -> reprice
    max_option_drift_pct: float = 4.0  # option ask moved this much (%) from snapshot ask -> reprice/reject
    max_spread_widening_pct: float = 1.0  # absolute spread% increase tolerated
    max_age_seconds: int = 90  # snapshot freshness (§41 "signal freshness")
    adverse_only: bool = True  # favourable drift (cheaper entry) never blocks


@dataclass(frozen=True)
class DriftVerdict:
    action: str  # "PROCEED" | "REPRICE" | "REJECT"
    reason_code: str | None
    underlying_drift: float
    option_drift_pct: float
    spread_now_pct: float
    age_seconds: float


def drift_check(snap: SignalSnapshot, *, now: dt.datetime, spot_now: float, bid_now: float, ask_now: float,
                atr: float, params: DriftParams | None = None) -> DriftVerdict:
    p = params or DriftParams()
    age = (now - snap.ts).total_seconds()
    sgn = 1 if snap.direction == "up" else -1
    u_drift = (spot_now - snap.underlying_price) * sgn  # positive = ran away from us (adverse for a buyer)
    o_drift_pct = ((ask_now - snap.ask) / snap.ask * 100.0) if snap.ask > 0 else 0.0
    mid = 0.5 * (bid_now + ask_now)
    spread_now = ((ask_now - bid_now) / mid * 100.0) if mid > 0 and bid_now > 0 else float("inf")

    def v(action, code):
        return DriftVerdict(action, code, round(u_drift, 2), round(o_drift_pct, 2), round(spread_now, 3) if spread_now != float("inf") else -1.0, round(age, 1))

    if age > p.max_age_seconds:
        return v("REJECT", "snapshot_stale")
    if bid_now <= 0 or ask_now <= 0:
        return v("REJECT", "no_two_sided_quote")
    if spread_now - snap.spread_pct > p.max_spread_widening_pct:
        return v("REJECT", "spread_widened")
    if atr > 0 and u_drift > p.max_underlying_drift_atr * atr:
        return v("REJECT", "underlying_ran_away")  # the move happened without us: never chase (§22/§40)
    if o_drift_pct > p.max_option_drift_pct:
        return v("REJECT", "option_repriced_up")
    if p.adverse_only and (u_drift < -p.max_underlying_drift_atr * atr or o_drift_pct < -p.max_option_drift_pct):
        # the market moved AGAINST the thesis since the snapshot: cheaper, but the setup may be failing - revalidate
        return v("REPRICE", "adverse_move_since_snapshot")
    return v("PROCEED", None)
