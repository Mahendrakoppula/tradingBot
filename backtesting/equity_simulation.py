"""Real account-equity curve simulation - combines the backtester's own
trade sequence (backtesting/event_loop.py) with REAL risk-based position
sizing, equity-protection capital tiers (risk/equity_protection.py), and
real transaction costs (execution/transaction_costs.py) to answer a
question neither the raw backtester (one conceptual unit) nor
cost_adjustment.py/slippage_sensitivity.py (a fixed illustrative lot
size) can: starting from a real amount of capital, sized and
risk-managed trade by trade, what does the account balance actually do?

SIZING NOTE - deliberately NOT reusing risk/position_sizing.py's
points-based position_size_lots() formula: that function takes a stop
distance in the traded instrument's own points and assumes loss scales
linearly with adverse price movement - true for a linear instrument
(futures/spot) or an undefined-risk position, but NOT true for a
BOUGHT option. Buying an option caps the maximum possible loss at the
premium paid, no matter how far the underlying's spot "stop" is
breached - a structural, well-known feature of long options, not an
approximation this project is choosing to make. Sizing a
long-options-only strategy correctly means budgeting against the
PREMIUM AT RISK (entry_premium * lot_size), not a spot-points stop
distance that doesn't correspond to this strategy's actual risk.
capital_at_risk_for_trade() from risk/position_sizing.py IS reused as
designed (instrument-agnostic: a fraction of current capital, scaled by
the current equity-protection tier) - only the points-based lot-count
formula is replaced with this options-appropriate premium-based one.

Processes trades in the SAME chronological order the underlying
backtest produced them. Equity-protection's allow_new_trades is
enforced HERE for the first time in this project (the raw backtester's
own daily-loss gate is a separate, already-enforced mechanism; this is
the multi-trade, capital-drawdown-driven tier) - a real depleted account
is modeled as skipping later trades entirely, not blindly continuing to
take the exact same signal sequence a full-capital account would have.
A trade is also skipped (not forced to a minimum of 1 lot) if the risk
budget can't afford even one lot at that trade's real premium - this
can happen often at the spec's own stated Rs.50,000 starting capital
against real current index-option premiums and lot sizes; reported
honestly via skipped_trades, not hidden.
"""
import math
from dataclasses import dataclass, field

from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates, option_round_trip_cost
from risk.equity_protection import classify_equity_tier
from risk.position_sizing import capital_at_risk_for_trade

DEFAULT_BASE_RISK_PCT = 0.01  # 1% of current capital per trade - a common, conservative default; not itself validated


@dataclass
class SimulatedTrade:
    trade: Trade
    lots: int
    quantity: int
    capital_before: float
    capital_at_risk: float
    equity_tier: str
    gross_pnl: float
    cost: float
    net_pnl: float
    capital_after: float


@dataclass
class EquitySimulationResult:
    starting_capital: float
    ending_capital: float
    simulated_trades: list[SimulatedTrade] = field(default_factory=list)
    skipped_trades: list[Trade] = field(default_factory=list)  # backtester took these, the equity sim couldn't/wouldn't
    peak_capital: float = 0.0
    max_drawdown_rupees: float = 0.0
    max_drawdown_pct: float = 0.0


def simulate_equity_curve(
    trades: list[Trade],
    lot_size: int,
    starting_capital: float,
    base_risk_pct: float = DEFAULT_BASE_RISK_PCT,
    max_lots: int | None = None,
    rates: TransactionCostRates = TransactionCostRates(),
) -> EquitySimulationResult:
    closed = sorted((t for t in trades if t.pnl is not None), key=lambda t: t.entry_index)

    capital = starting_capital
    peak = starting_capital
    max_dd_rupees = 0.0
    simulated: list[SimulatedTrade] = []
    skipped: list[Trade] = []

    for trade in closed:
        tier = classify_equity_tier(capital, starting_capital)
        if not tier.allow_new_trades:
            skipped.append(trade)
            continue

        risk_budget = capital_at_risk_for_trade(capital, base_risk_pct, tier.size_multiplier)
        max_loss_per_lot = trade.entry_premium * lot_size
        lots = math.floor(risk_budget / max_loss_per_lot) if max_loss_per_lot > 0 else 0
        if max_lots is not None:
            lots = min(lots, max_lots)
        if lots <= 0:
            skipped.append(trade)
            continue

        quantity = lots * lot_size
        gross_pnl = (trade.exit_premium - trade.entry_premium) * quantity
        cost = option_round_trip_cost(trade.entry_premium, trade.exit_premium, quantity, rates)
        net_pnl = gross_pnl - cost

        capital_before = capital
        capital += net_pnl
        peak = max(peak, capital)
        max_dd_rupees = max(max_dd_rupees, peak - capital)

        simulated.append(SimulatedTrade(
            trade=trade, lots=lots, quantity=quantity, capital_before=capital_before,
            capital_at_risk=risk_budget, equity_tier=tier.tier, gross_pnl=gross_pnl,
            cost=cost, net_pnl=net_pnl, capital_after=capital,
        ))

    return EquitySimulationResult(
        starting_capital=starting_capital, ending_capital=capital, simulated_trades=simulated,
        skipped_trades=skipped, peak_capital=peak, max_drawdown_rupees=max_dd_rupees,
        max_drawdown_pct=(max_dd_rupees / peak * 100) if peak > 0 else 0.0,
    )
