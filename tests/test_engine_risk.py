import dataclasses
import datetime as dt

from trading_bot.costs import CostRates
from trading_bot.engine.option_chain import OptionQuote
from trading_bot.engine.risk_engine import AccountState, RiskLimits, evaluate, record_result
from trading_bot.engine.stops import Plan
from trading_bot.options import OptionContract
from trading_bot.timeutil import IST

NOW = dt.datetime(2026, 9, 16, 10, 30, tzinfo=IST)
RATES = CostRates()


def _quote(mid=150.0, spread=0.5, lot=75) -> OptionQuote:
    c = OptionContract("1", "NIFTY23SEP2625000CE", "NIFTY", dt.date(2026, 9, 23), 25000.0, "CE", lot, 1800, "NFO")
    return OptionQuote(c, NOW, mid, mid - spread / 2, mid + spread / 2, 500, 500, 20000, 80000, 0.14, 0.5, 0.002, -8.0, 30.0, "broker", 25000.0)


def _plan(entry=150.0, stop=148.5, t1=160.0, direction="up") -> Plan:
    return Plan(direction, 25000.0, 24970.0, 25060.0, 25096.0, 25116.0, 1.5, entry, stop, t1, t1 + 6, entry - stop, t1 - entry)


def _acct(**over) -> AccountState:
    base = dict(equity=50000.0)
    base.update(over)
    return AccountState(**base)


def test_approved_trade_sizes_after_all_in_costs_and_reports_everything():
    d = evaluate(_plan(), _quote(), _acct(), RiskLimits(), RATES, score=80)
    assert d.decision == "APPROVED" and d.reason_code is None and d.approved
    assert d.lots >= 1 and d.quantity == d.lots * 75
    # planned loss includes the option move, spread, slippage, brokerage, taxes and is under the cap
    assert d.planned_loss <= d.max_permitted_loss == 250.0
    assert d.planned_loss > d.lots * 75 * 1.5  # more than the bare option move
    c = d.costs
    assert c.brokerage == 40.0 and c.stt > 0 and c.exchange > 0 and c.sebi > 0 and c.gst > 0 and c.stamp > 0
    assert c.spread == 0.5 * d.quantity and c.slippage == 0.25 * d.quantity
    assert d.net_expected_profit < d.gross_expected_profit and d.expected_value > 0 and d.risk_reward > 1
    assert d.tier in ("A", "B") and d.win_probability == 0.45


def test_one_lot_over_budget_is_rejected_not_resized_upward():
    # 4 points of option risk x 75 = 300 + costs > 250 permitted -> at Rs.50k and 0.5% one NIFTY lot
    # only fits ~3.3 points of ALL-IN risk; the spec says reject, never raise the budget
    d = evaluate(_plan(stop=146.0, t1=175.0), _quote(), _acct(), RiskLimits(), RATES, score=90)
    assert d.decision == "REJECTED" and d.reason_code == "one_lot_exceeds_max_risk"
    assert d.quantity == 0 and d.risk_per_unit_all_in > 4.0


def test_hard_account_locks_come_first():
    L = RiskLimits()
    assert evaluate(_plan(), _quote(), _acct(realized_today=-1000.0), L, RATES, score=99).reason_code == "daily_loss_lock"
    assert evaluate(_plan(), _quote(), _acct(daily_lock=True), L, RATES, score=99).reason_code == "daily_loss_lock"
    assert evaluate(_plan(), _quote(), _acct(realized_week=-2500.0), L, RATES, score=99).reason_code == "weekly_loss_lock"
    assert evaluate(_plan(), _quote(), _acct(consecutive_losses=3), L, RATES, score=99).reason_code == "consecutive_loss_lock"
    assert evaluate(_plan(), _quote(), _acct(trades_today=6), L, RATES, score=99).reason_code == "max_trades_per_day"
    assert evaluate(_plan(), _quote(), _acct(open_positions=2), L, RATES, score=99).reason_code == "max_open_positions"
    assert evaluate(_plan(), _quote(), _acct(open_directions={"BANKNIFTY": "up"}), L, RATES, score=99).reason_code == "correlated_exposure"
    # heat exhausted: open risk == capital x max_portfolio_heat_pct (50,000 x 6%)
    assert evaluate(_plan(), _quote(), _acct(open_risk=3000.0), L, RATES, score=99).reason_code == "no_risk_budget_left"


def test_daily_room_and_heat_reduce_size():
    # 1000 daily cap, already down 800 -> only 200 of room -> REDUCE_SIZE with a smaller budget
    d = evaluate(_plan(stop=149.5), _quote(), _acct(realized_today=-800.0), RiskLimits(), RATES, score=80)
    assert d.decision == "REDUCE_SIZE" and d.max_permitted_loss == 200.0 and d.approved
    # heat: 6% of 50,000 = 3,000 of total open risk, 2,800 already committed -> 200 left
    heat = evaluate(_plan(stop=149.5), _quote(), _acct(open_risk=2800.0), RiskLimits(), RATES, score=80)
    assert heat.decision == "REDUCE_SIZE" and heat.max_permitted_loss == 200.0 and heat.checks["binding_cap"] == "portfolio_heat"
    # with 1% risk the same setup gets three lots of room (Rs.500 / ~Rs.115 per lot + brokerage)
    two = evaluate(_plan(stop=149.5), _quote(), _acct(), RiskLimits(risk_per_trade_pct=0.01), RATES, score=80)
    assert two.decision == "APPROVED" and two.lots == 3 and two.planned_loss <= 500.0


def test_execution_quality_gates():
    wide = evaluate(_plan(), _quote(spread=4.0), _acct(), RiskLimits(), RATES, score=80)
    assert wide.reason_code == "spread_too_wide"
    assert evaluate(_plan(), _quote(), _acct(), RiskLimits(), RATES, score=80, thesis_ok=False).reason_code == "invalid_thesis"
    assert evaluate(_plan(stop=150.0), _quote(), _acct(), RiskLimits(), RATES, score=80).reason_code == "no_technical_stop"
    assert evaluate(_plan(), _quote(), _acct(), RiskLimits(), RATES, score=80, expected_move_ok=False).reason_code == "insufficient_movement"


def test_negative_ev_and_rr_floor_reject():
    d = evaluate(_plan(t1=151.0), _quote(), _acct(), RiskLimits(), RATES, score=80)  # 1 point reward vs 1.5 risk + costs
    assert d.decision == "REJECTED" and d.reason_code in ("negative_expected_value", "risk_reward_below_floor")
    assert d.expected_value <= 0 or d.risk_reward < 1.0
    low_p = evaluate(_plan(), _quote(), _acct(), RiskLimits(), RATES, score=80, win_probability=0.1)
    assert low_p.reason_code == "negative_expected_value"


def test_rs800_preference_is_last_and_tier_b_is_configurable():
    big = evaluate(_plan(t1=165.0), _quote(), _acct(), RiskLimits(), RATES, score=60)
    assert big.approved and big.tier == "A" and big.net_expected_profit >= 800
    small = evaluate(_plan(t1=158.0), _quote(), _acct(), RiskLimits(), RATES, score=75)
    assert small.approved and small.tier == "B" and small.net_expected_profit < 800
    weak = evaluate(_plan(t1=158.0), _quote(), _acct(), RiskLimits(), RATES, score=50)
    assert weak.decision == "REJECTED" and weak.reason_code == "below_preferred_reward_and_not_tier_b"
    off = evaluate(_plan(t1=158.0), _quote(), _acct(), RiskLimits(tier_b_enabled=False), RATES, score=95)
    assert off.decision == "REJECTED"


def test_record_result_updates_tallies():
    a = _acct()
    record_result(a, -120.0)
    record_result(a, -80.0)
    assert a.consecutive_losses == 2 and a.trades_today == 2 and a.realized_today == -200.0 and a.equity == 49800.0
    record_result(a, 300.0)
    assert a.consecutive_losses == 0 and a.realized_week == 100.0


def test_the_binding_cap_is_named_in_the_checks():
    """2026-09-24: a hard-coded 1.5% portfolio heat held every trade to Rs.750
    while TECH_RISK_PER_TRADE_PCT said Rs.1,500, and the rejection only showed
    "permitted 750" - nothing said which of the three caps bound."""
    limits = dataclasses.replace(RiskLimits(), capital=50_000.0, risk_per_trade_pct=0.03, daily_loss_cap_pct=0.30,
                                 max_portfolio_heat_pct=0.06)
    d = evaluate(_plan(), _quote(), _acct(), limits, RATES, score=99)
    assert d.checks["binding_cap"] == "per_trade" and d.checks["caps"]["per_trade"] == 1500.0
    assert d.checks["max_permitted_loss"] == 1500.0
    # heat below risk x positions is what silently overrode the per-trade setting
    squeezed = dataclasses.replace(limits, max_portfolio_heat_pct=0.015)
    d2 = evaluate(_plan(), _quote(), _acct(), squeezed, RATES, score=99)
    assert d2.checks["binding_cap"] == "portfolio_heat" and d2.checks["max_permitted_loss"] == 750.0
