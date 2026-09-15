"""Tick-based slippage - a premium-scale-aware alternative to
slippage_sensitivity.py's flat PERCENTAGE-of-premium sweep, built
specifically to close the risk backtesting/BACKTESTS.md's Run 008
flagged: Run 006's 0-5% sweep was calibrated against ATM-level premiums
(Rs.60-1,200); applying the SAME percentage to a far-OTM contract's tiny
premium (Rs.5-15, per Run 008) understates real execution cost, because
real bid-ask spreads for illiquid contracts are better modeled as a
roughly FIXED number of exchange ticks (an absolute rupee amount per
unit), not a fixed fraction of premium - a few ticks is a small % of an
expensive ATM premium but a huge % of a cheap far-OTM one.

REAL_TICK_SIZE_RUPEES - a data-interpretation catch, not a guess: the
live scrip master's own `tick_size` field reads "5.000000" for every
NIFTY/BANKNIFTY/SENSEX option row (verified live 2026-09-15). Taken at
face value that would be an absurdly coarse Rs.5 tick for options that
trade as low as Rs.0.01-0.05 (confirmed in this same session's far-OTM
snapshots). This schema scales OTHER price-like fields by 100 - directly
confirmed via the `strike` field: a real NIFTY 23000 strike is stored
as "2300000.000000", matching the strike embedded in that same row's
own tradingsymbol text (e.g. "NIFTY29DEC2623000PE"). tick_size sits in
the identical row schema as strike (both price-like, unlike lotsize/
freeze_qty which are plain integer counts) - applying the same x100
convention gives Rs.0.05, a realistic real-world NSE options tick size,
not Rs.5. Using the wrong one would have made every result in this
module 100x too coarse to be useful.
"""
from dataclasses import dataclass

from backtesting.metrics import PerformanceSummary, summarize_pnls
from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates, option_round_trip_cost

REAL_TICK_SIZE_RUPEES = 0.05

DEFAULT_TICK_SPREAD_LEVELS = (0, 1, 2, 5, 10, 20)


def tick_slippage_cost(n_ticks_round_trip: float, quantity: int) -> float:
    """Rupee cost of an assumed N-tick round-trip spread (split as a
    half-spread on entry, half on exit - same convention
    slippage_sensitivity.py uses for its percentage-based model) on
    `quantity` units."""
    return n_ticks_round_trip * REAL_TICK_SIZE_RUPEES * quantity


def tick_adjusted_pnl(trade: Trade, lot_size: int, n_ticks_round_trip: float, rates: TransactionCostRates = TransactionCostRates()) -> float:
    if trade.pnl is None:
        raise ValueError("trade must be closed (pnl is not None) to adjust")
    gross_pnl = (trade.exit_premium - trade.entry_premium) * lot_size
    slippage_cost = tick_slippage_cost(n_ticks_round_trip, lot_size)
    txn_cost = option_round_trip_cost(trade.entry_premium, trade.exit_premium, lot_size, rates)
    return gross_pnl - slippage_cost - txn_cost


@dataclass
class TickSlippagePoint:
    n_ticks: float
    summary: PerformanceSummary


def tick_slippage_sweep(
    trades: list[Trade],
    lot_size: int,
    tick_levels: tuple[float, ...] = DEFAULT_TICK_SPREAD_LEVELS,
    rates: TransactionCostRates = TransactionCostRates(),
) -> list[TickSlippagePoint]:
    """Same stateless, per-trade sweep style as slippage_sensitivity.py -
    for a FIXED lot size applied to every trade (Run 005/006's style).
    For the compounding, affordability-aware equity curve (Run 007/008's
    style), use equity_simulation.py's tick_spread parameter instead -
    that one has to re-run the whole compounding simulation per tick
    level, since realized costs change future position sizing."""
    closed = [t for t in trades if t.pnl is not None]
    points = []
    for n_ticks in tick_levels:
        pnls = [tick_adjusted_pnl(t, lot_size, n_ticks, rates) for t in closed]
        points.append(TickSlippagePoint(n_ticks=n_ticks, summary=summarize_pnls(pnls)))
    return points
