"""Post-hoc transaction-cost adjustment for already-run backtests -
answers "does this survive realistic costs?" directly, closing the #1
gap every result in backtesting/BACKTESTS.md has been logged with since
Run 001: "zero transaction costs, slippage, and bid-ask spread on the
option leg (currently zero)."

Deliberately a SEPARATE, post-hoc analysis step, not wired into
backtesting/event_loop.py's process_bar(): that function's P&L is
ONE CONCEPTUAL UNIT by design (see its own docstring - keeps a
strategy's raw edge separate from sizing/cost decisions). Real
transaction costs need a REAL lot size to mean anything (a flat Rs.20/
order brokerage fee doesn't scale with an arbitrary "1 unit"), so this
module explicitly scales the backtester's per-unit premiums up to a
real lot size AFTER the fact, using data/lot_size.py's live-verified
current lot size.

HONEST LIMITATION, same class as data/expiry_calendar.py and
data/lot_size.py's own caveats: lot sizes (and cost rates - brokerage
plans, statutory charges) have changed multiple times within the
backtester's own 2021-2026 window. Using TODAY's live lot size/rates
uniformly across 5 years of historical trades is an ILLUSTRATIVE
"what would this look like at today's real-world costs" analysis, not
a historically-precise reconstruction of what each trade would actually
have cost on its own historical date.
"""
from dataclasses import dataclass

from backtesting.metrics import PerformanceSummary, summarize_pnls
from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates, option_round_trip_cost


@dataclass
class CostAdjustedTrade:
    trade: Trade
    gross_pnl: float  # trade.pnl scaled to lot_size (still zero cost)
    cost: float
    net_pnl: float  # gross_pnl - cost


def cost_adjust_trade(trade: Trade, lot_size: int, rates: TransactionCostRates = TransactionCostRates()) -> CostAdjustedTrade:
    if trade.pnl is None:
        raise ValueError("trade must be closed (pnl is not None) to cost-adjust")
    gross_pnl = trade.pnl * lot_size
    cost = option_round_trip_cost(trade.entry_premium, trade.exit_premium, lot_size, rates)
    return CostAdjustedTrade(trade=trade, gross_pnl=gross_pnl, cost=cost, net_pnl=gross_pnl - cost)


@dataclass
class CostAdjustedResult:
    lot_size: int
    adjusted_trades: list[CostAdjustedTrade]
    gross_summary: PerformanceSummary  # scaled to lot_size, zero cost
    net_summary: PerformanceSummary  # after realistic costs
    total_cost: float


def cost_adjust_backtest(trades: list[Trade], lot_size: int, rates: TransactionCostRates = TransactionCostRates()) -> CostAdjustedResult:
    closed = [t for t in trades if t.pnl is not None]
    adjusted = [cost_adjust_trade(t, lot_size, rates) for t in closed]
    return CostAdjustedResult(
        lot_size=lot_size,
        adjusted_trades=adjusted,
        gross_summary=summarize_pnls([a.gross_pnl for a in adjusted]),
        net_summary=summarize_pnls([a.net_pnl for a in adjusted]),
        total_cost=sum(a.cost for a in adjusted),
    )
