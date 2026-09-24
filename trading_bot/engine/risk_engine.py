"""Risk engine (spec §25, §27, §29, §30, §33, §34, §35) - the FINAL
authority (§92 #36). Everything upstream proposes; this decides.

Order of authority (§29 priority list): hard risk -> valid thesis ->
technical SL -> position size -> sufficient movement -> positive net EV ->
option quality -> risk-adjusted reward -> the Rs.800 preference last.

All-in risk per unit (§25) = option move to SL + the round-trip
transaction costs + spread + slippage, ALL of it, priced per unit so
quantity is a pure division (§27): max permitted risk / all-in risk per
unit, rounded DOWN to lots. If one lot exceeds the permitted risk the
trade is REJECTED - risk is never raised to fit (§92 #13-17).
"""
import datetime as dt
from dataclasses import dataclass, field

from trading_bot.costs import CostRates
from trading_bot.engine.option_chain import OptionQuote
from trading_bot.engine.stops import Plan

DECISIONS = ("APPROVED", "REJECTED", "REDUCE_SIZE", "WAIT_FOR_CONFIRMATION", "WAIT_FOR_BETTER_PRICE")


@dataclass(frozen=True)
class RiskLimits:
    capital: float = 50_000.0
    risk_per_trade_pct: float = 0.005  # §25: 0.25%-1.0%
    daily_loss_cap_pct: float = 0.02
    weekly_loss_cap_pct: float = 0.05
    max_consecutive_losses: int = 3
    max_trades_per_day: int = 6  # §2: 4-6 is a SOFT target; this is the hard ceiling
    max_open_positions: int = 2
    max_portfolio_heat_pct: float = 0.06  # §30 sum of OPEN risk / equity; also caps one trade (see checks.binding_cap)
    max_spread_pct: float = 2.0
    max_slippage_pct: float = 1.0
    preferred_net_reward: float = 800.0  # §29: a preference, never a reason to do anything else
    tier_b_enabled: bool = True  # §29 Tier B may trade "if configured and statistically justified"
    tier_b_min_score: int = 70
    min_rr: float = 1.0  # a floor, not a target (§28: R:R is only one factor)
    ev_win_prob_default: float = 0.45  # until fingerprint stats exist (§30 "historical expectancy")
    slippage_pct_of_spread: float = 0.5


@dataclass
class AccountState:
    """§34 running tallies, owned by the loop and passed in."""
    equity: float
    realized_today: float = 0.0
    realized_week: float = 0.0
    consecutive_losses: int = 0
    trades_today: int = 0
    open_positions: int = 0
    open_risk: float = 0.0  # rupees at risk across open positions (portfolio heat numerator)
    open_directions: dict = field(default_factory=dict)  # underlying -> "up"/"down"
    daily_lock: bool = False
    weekly_lock: bool = False


@dataclass
class CostBreakdown:
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    gst: float
    stamp: float
    spread: float
    slippage: float
    total: float

    def as_dict(self) -> dict:
        return vars(self).copy()


@dataclass
class RiskDecision:
    decision: str
    reason_code: str | None
    quantity: int
    lots: int
    lot_size: int
    risk_per_unit_all_in: float
    planned_loss: float  # rupees, all-in, for the quantity
    max_permitted_loss: float
    gross_expected_profit: float
    net_expected_profit: float
    expected_value: float
    win_probability: float
    risk_reward: float
    tier: str  # "A" | "B" | "C"
    costs: CostBreakdown | None
    checks: dict = field(default_factory=dict)
    detail: str = ""

    @property
    def approved(self) -> bool:
        return self.decision in ("APPROVED", "REDUCE_SIZE")


def _cost_components(rates: CostRates, entry: float, exit_: float, qty: int, spread: float, slippage: float,
                     include_brokerage: bool = True) -> CostBreakdown:
    """§33: every component reported, never a silent zero. Brokerage is per
    ORDER (two per round trip), everything else scales with turnover."""
    buy_turn, sell_turn = entry * qty, exit_ * qty
    brokerage = 2 * rates.brokerage_per_order if include_brokerage else 0.0
    stt = sell_turn * rates.stt_sell_pct / 100.0
    exch = (buy_turn + sell_turn) * rates.exchange_txn_pct / 100.0
    sebi = (buy_turn + sell_turn) * rates.sebi_fee_pct / 100.0
    stamp = buy_turn * rates.stamp_duty_pct / 100.0
    gst = (brokerage + exch + sebi) * rates.gst_pct / 100.0
    spread_cost = spread * qty
    slip_cost = slippage * qty
    total = brokerage + stt + exch + sebi + stamp + gst + spread_cost + slip_cost
    return CostBreakdown(round(brokerage, 2), round(stt, 2), round(exch, 2), round(sebi, 2), round(gst, 2), round(stamp, 2),
                         round(spread_cost, 2), round(slip_cost, 2), round(total, 2))


def evaluate(plan: Plan, quote: OptionQuote, account: AccountState, limits: RiskLimits, rates: CostRates, *,
             score: int, win_probability: float | None = None, now: dt.datetime | None = None,
             expected_move_ok: bool = True, thesis_ok: bool = True) -> RiskDecision:
    lot = quote.contract.lotsize
    checks: dict = {}

    def reject(code: str, detail: str = "", **kw) -> RiskDecision:
        return RiskDecision("REJECTED", code, 0, 0, lot, kw.get("rpu", 0.0), 0.0, kw.get("max_loss", 0.0), 0.0, 0.0, 0.0,
                            kw.get("p", 0.0), 0.0, "C", kw.get("costs"), checks, detail)

    # --- 1. hard account-level risk (§34) -----------------------------------------------------
    max_loss = limits.capital * limits.risk_per_trade_pct
    daily_cap = limits.capital * limits.daily_loss_cap_pct
    weekly_cap = limits.capital * limits.weekly_loss_cap_pct
    checks["daily_loss"] = account.realized_today
    if account.daily_lock or -account.realized_today >= daily_cap:
        return reject("daily_loss_lock", f"realized today {account.realized_today:.0f} vs cap -{daily_cap:.0f}", max_loss=max_loss)
    if account.weekly_lock or -account.realized_week >= weekly_cap:
        return reject("weekly_loss_lock", max_loss=max_loss)
    if account.consecutive_losses >= limits.max_consecutive_losses:
        return reject("consecutive_loss_lock", f"{account.consecutive_losses} in a row", max_loss=max_loss)
    if account.trades_today >= limits.max_trades_per_day:
        return reject("max_trades_per_day", max_loss=max_loss)
    if account.open_positions >= limits.max_open_positions:
        return reject("max_open_positions", max_loss=max_loss)
    if plan.direction in set(account.open_directions.values()):
        return reject("correlated_exposure", "same direction already open in the index bucket", max_loss=max_loss)
    # the smaller of per-trade cap and what is left under the daily cap
    room_today = daily_cap + account.realized_today  # realized_today is negative when losing
    heat_room = limits.capital * limits.max_portfolio_heat_pct - account.open_risk
    caps = {"per_trade": max_loss, "daily_room": max(0.0, room_today), "portfolio_heat": max(0.0, heat_room)}
    binding = min(caps, key=caps.get)
    max_loss = caps[binding]
    checks["max_permitted_loss"] = round(max_loss, 2)
    # which of the three caps actually bound: a silent portfolio-heat cap made the per-trade
    # setting look like it was in force when it was not (2026-09-24)
    checks["binding_cap"] = binding
    checks["caps"] = {k: round(v, 2) for k, v in caps.items()}
    if max_loss <= 0:
        return reject("no_risk_budget_left", max_loss=max_loss)

    # --- 2. valid thesis, 3. technical SL ------------------------------------------------------
    if not thesis_ok:
        return reject("invalid_thesis", max_loss=max_loss)
    if plan.risk_per_unit <= 0:
        return reject("no_technical_stop", max_loss=max_loss)

    # --- execution quality gates (§34 max spread/slippage) ---------------------------------------
    if quote.spread_pct > limits.max_spread_pct:
        return reject("spread_too_wide", f"{quote.spread_pct:.2f}%", max_loss=max_loss)
    slippage = limits.slippage_pct_of_spread * quote.spread
    if quote.mid > 0 and slippage / quote.mid * 100.0 > limits.max_slippage_pct:
        return reject("slippage_too_high", max_loss=max_loss)

    # --- 4. position size after SL (§27), all-in per unit (§25) -----------------------------------
    unit_costs = _cost_components(rates, plan.option_entry, max(0.05, plan.option_stop), 1, quote.spread, slippage,
                                  include_brokerage=False)
    rpu = plan.risk_per_unit + unit_costs.total  # option move to SL + taxes + spread + slippage, per unit
    fixed = 2 * rates.brokerage_per_order * (1.0 + rates.gst_pct / 100.0)  # per round trip, not per unit
    checks["risk_per_unit_all_in"] = round(rpu, 2)
    lots = int(max(0.0, max_loss - fixed) // (rpu * lot))
    if lots < 1:
        return reject("one_lot_exceeds_max_risk", f"one lot risks {rpu * lot:.0f} > permitted {max_loss:.0f}", rpu=rpu, max_loss=max_loss)
    qty = lots * lot
    # brokerage is per order, not per unit - recompute costs at the real quantity
    costs_at_qty = _cost_components(rates, plan.option_entry, max(0.05, plan.option_stop), qty, quote.spread, slippage)
    planned_loss = plan.risk_per_unit * qty + costs_at_qty.total
    while planned_loss > max_loss and lots > 1:
        lots -= 1
        qty = lots * lot
        costs_at_qty = _cost_components(rates, plan.option_entry, max(0.05, plan.option_stop), qty, quote.spread, slippage)
        planned_loss = plan.risk_per_unit * qty + costs_at_qty.total
    if planned_loss > max_loss:
        return reject("one_lot_exceeds_max_risk", f"{planned_loss:.0f} > {max_loss:.0f}", rpu=rpu, max_loss=max_loss, costs=costs_at_qty)
    checks["lots"] = lots

    # --- 5. sufficient movement ------------------------------------------------------------------------
    if not expected_move_ok:
        return reject("insufficient_movement", rpu=rpu, max_loss=max_loss, costs=costs_at_qty)

    # --- 6. net EV (§30) --------------------------------------------------------------------------------
    win_costs = _cost_components(rates, plan.option_entry, plan.option_target1, qty, quote.spread, slippage)
    gross_profit = plan.reward1_per_unit * qty
    net_profit = gross_profit - win_costs.total
    net_loss = planned_loss
    p = limits.ev_win_prob_default if win_probability is None else max(0.0, min(1.0, win_probability))
    ev = p * net_profit - (1.0 - p) * net_loss
    rr = net_profit / net_loss if net_loss > 0 else 0.0
    checks.update({"net_profit": round(net_profit, 2), "net_loss": round(net_loss, 2), "ev": round(ev, 2), "rr": round(rr, 2), "p": p})
    if ev <= 0:
        return RiskDecision("REJECTED", "negative_expected_value", 0, 0, lot, round(rpu, 2), round(planned_loss, 2),
                            round(max_loss, 2), round(gross_profit, 2), round(net_profit, 2), round(ev, 2), p, round(rr, 2),
                            "C", costs_at_qty, checks)
    if rr < limits.min_rr:
        return RiskDecision("REJECTED", "risk_reward_below_floor", 0, 0, lot, round(rpu, 2), round(planned_loss, 2),
                            round(max_loss, 2), round(gross_profit, 2), round(net_profit, 2), round(ev, 2), p, round(rr, 2),
                            "C", costs_at_qty, checks)

    # --- 7-9. option quality is upstream (selection); Rs.800 preference LAST (§29) --------------------------
    if net_profit >= limits.preferred_net_reward:
        tier = "A"
    else:
        tier = "B"
        if not limits.tier_b_enabled or score < limits.tier_b_min_score:
            return RiskDecision("REJECTED", "below_preferred_reward_and_not_tier_b", 0, 0, lot, round(rpu, 2),
                                round(planned_loss, 2), round(max_loss, 2), round(gross_profit, 2), round(net_profit, 2),
                                round(ev, 2), p, round(rr, 2), "C", costs_at_qty, checks,
                                f"net {net_profit:.0f} < {limits.preferred_net_reward:.0f}; score {score} < {limits.tier_b_min_score}")
    # REDUCE_SIZE = approved, but the budget was cut below the nominal per-trade cap by the
    # daily room or portfolio heat (§34) - the caller sees the size was constrained
    nominal = limits.capital * limits.risk_per_trade_pct
    decision = "REDUCE_SIZE" if max_loss < nominal - 1e-9 else "APPROVED"
    return RiskDecision(decision, None, qty, lots, lot, round(rpu, 2), round(planned_loss, 2), round(max_loss, 2),
                        round(gross_profit, 2), round(net_profit, 2), round(ev, 2), p, round(rr, 2), tier, costs_at_qty, checks)


def record_result(account: AccountState, net_pnl: float, now: dt.datetime | None = None) -> None:
    """Update the §34 tallies after a closed trade."""
    account.realized_today += net_pnl
    account.realized_week += net_pnl
    account.trades_today += 1
    account.consecutive_losses = account.consecutive_losses + 1 if net_pnl < 0 else 0
    account.equity += net_pnl
