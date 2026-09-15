"""Slippage/bid-ask-spread sensitivity analysis - the other half of Run
005's "no costs/slippage" gap (backtesting/cost_adjustment.py closed
transaction costs, using REAL, verifiable statutory/brokerage rates).
Slippage cannot be closed the same honest way: SmartAPI never exposes
historical bid-ask depth (data/pull_history.py has only ever pulled
OHLCV candles, confirmed nowhere in this project's history), so there is
no real historical spread data to derive a validated slippage number
from - `main`'s own liquidity.py needs a LIVE order-book quote, which
doesn't exist for arbitrary historical bars.

Rather than fabricate a single "the real slippage is X%" number this
project has no way to verify, this sweeps a RANGE of assumed round-trip
slippage percentages (split as a half-spread on each leg - buy slightly
above the theoretical entry premium, sell slightly below the
theoretical exit premium, a standard simple spread proxy) and reports
how the P&L conclusion changes across that range. Combined with the
REAL transaction costs from cost_adjustment.py's rates, so the reported
numbers reflect known costs exactly and unknown costs as an explicit,
swept assumption - never blended together as if both were equally
certain.
"""
from dataclasses import dataclass

from backtesting.metrics import PerformanceSummary, summarize_pnls
from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates, option_round_trip_cost

DEFAULT_SLIPPAGE_LEVELS_PCT = (0.0, 0.5, 1.0, 2.0, 5.0)


def combined_adjusted_pnl(
    trade: Trade, lot_size: int, slippage_pct: float, rates: TransactionCostRates = TransactionCostRates(),
) -> float:
    """Net P&L for one closed trade after REAL transaction costs (exact,
    per execution/transaction_costs.py) and an ASSUMED round-trip
    slippage of `slippage_pct` (split half on entry, half on exit)."""
    if trade.pnl is None:
        raise ValueError("trade must be closed (pnl is not None) to adjust")
    gross_pnl = (trade.exit_premium - trade.entry_premium) * lot_size
    slippage_cost = (trade.entry_premium + trade.exit_premium) * (slippage_pct / 200) * lot_size
    txn_cost = option_round_trip_cost(trade.entry_premium, trade.exit_premium, lot_size, rates)
    return gross_pnl - slippage_cost - txn_cost


@dataclass
class SlippageSensitivityPoint:
    slippage_pct: float
    summary: PerformanceSummary


def slippage_sensitivity_sweep(
    trades: list[Trade],
    lot_size: int,
    slippage_levels_pct: tuple[float, ...] = DEFAULT_SLIPPAGE_LEVELS_PCT,
    rates: TransactionCostRates = TransactionCostRates(),
) -> list[SlippageSensitivityPoint]:
    closed = [t for t in trades if t.pnl is not None]
    points = []
    for pct in slippage_levels_pct:
        pnls = [combined_adjusted_pnl(t, lot_size, pct, rates) for t in closed]
        points.append(SlippageSensitivityPoint(slippage_pct=pct, summary=summarize_pnls(pnls)))
    return points
