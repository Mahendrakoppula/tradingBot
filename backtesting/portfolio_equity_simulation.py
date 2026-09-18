"""Portfolio-level equity simulation - closes the biggest remaining
structural gap in this project's backtesting: every Run through 013
treats NIFTY/BANKNIFTY/SENSEX as three INDEPENDENT Rs.50,000 accounts,
each with its own capital, its own equity-protection tiers, its own
affordability search. A real account is ONE pool of capital that all
three instruments' signals compete for simultaneously -
portfolio/greek_aggregation.py's concentration check was built for
exactly this scenario but has never actually been wired into a real,
capital-constrained backtest until now.

Deliberately reuses each instrument's entry/exit TIMING decisions
UNCHANGED from backtesting.event_loop.run_backtest() run independently
per instrument, exactly as every prior Run in this log already does -
Investigation 001/Run 004 established strike choice (and by extension,
capital availability) is orthogonal to entry timing, so there is no
need to re-decide WHEN to trade; only WHETHER a shared, finite pool of
capital can afford a given trade at the moment it wants to enter,
now that OTHER instruments' currently-open positions may already have
real cash locked up in them.

KEY NEW MECHANIC, absent from the single-instrument
equity_simulation.py because it never mattered there (positions never
overlapped): a bought option's premium is paid in CASH at entry and
only returned (plus/minus P&L) at exit - equity_simulation.py's
single-instrument loop never needed to track this explicitly, since
with only one position open at a time, "current capital" and "free
cash" were always the same number. With genuine concurrency across
instruments, they are NOT the same: three simultaneously-open positions
really do lock up real cash that a fourth signal cannot spend twice.
This module tracks free cash explicitly, deducting each position's
entry cost (entry_premium * quantity) at entry and returning it (plus
net P&L) at exit.

HONEST LIMITATION, stated up front: equity-protection tier
classification and risk budgeting here use FREE CASH, not
mark-to-market portfolio equity (free cash + current value of open
positions). This is a conservative simplification - it understates
true equity while positions are open, since the premium already paid
is given no credit for still being worth something - chosen because
repricing every open position at every event would add real complexity
without changing the qualitative question this module exists to
answer (does capital contention across instruments matter at all).
"""
import math
from dataclasses import dataclass, field

import pandas as pd

from backtesting.tick_slippage import tick_slippage_cost
from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates, option_round_trip_cost
from features.theoretical_options import theoretical_option_snapshot
from portfolio.greek_aggregation import Position, PortfolioRiskLimits, aggregate_portfolio_greeks, check_portfolio_risk
from risk.equity_protection import classify_equity_tier
from risk.position_sizing import capital_at_risk_for_trade
from strategies.contract_selection import DEFAULT_MAX_OTM_STEPS, select_affordable_contract

DEFAULT_BASE_RISK_PCT = 0.01


@dataclass
class OpenPortfolioPosition:
    instrument: str
    trade: Trade
    lots: int
    quantity: int
    entry_cost: float  # entry_premium * quantity - real cash locked up until exit
    entry_premium: float
    strike: float


@dataclass
class PortfolioSimulatedTrade:
    instrument: str
    trade: Trade
    lots: int
    quantity: int
    cash_before: float
    capital_at_risk: float
    equity_tier: str
    gross_pnl: float
    cost: float
    net_pnl: float
    cash_after: float
    strike: float
    entry_premium: float
    exit_premium: float


@dataclass
class PortfolioEquitySimulationResult:
    starting_capital: float
    ending_capital: float
    simulated_trades: list[PortfolioSimulatedTrade] = field(default_factory=list)
    skipped_trades: list[tuple[str, Trade]] = field(default_factory=list)
    peak_capital: float = 0.0
    max_drawdown_rupees: float = 0.0
    max_drawdown_pct: float = 0.0
    max_concurrent_positions: int = 0  # observed high-water mark, informational


def simulate_portfolio_equity_curve(
    trades_by_instrument: dict[str, list[Trade]],
    dfs_by_instrument: dict[str, pd.DataFrame],
    lot_sizes: dict[str, int],
    strike_increments: dict[str, float],
    starting_capital: float,
    risk_free_rate: float,
    base_risk_pct: float = DEFAULT_BASE_RISK_PCT,
    vol_window: int = 20,
    max_otm_steps: int = DEFAULT_MAX_OTM_STEPS,
    max_lots: int | None = None,
    rates: TransactionCostRates = TransactionCostRates(),
    tick_spread: float = 0.0,
    max_single_instrument_delta_share: float | None = None,
) -> PortfolioEquitySimulationResult:
    """`trades_by_instrument` should hold each instrument's own
    backtesting.event_loop.run_backtest() output, computed
    INDEPENDENTLY exactly as every prior Run does - this function only
    changes how those already-decided trades compete for one shared
    capital pool. All instruments must share the same row-index-to-
    calendar-date mapping (true for NIFTY/BANKNIFTY/SENSEX's real
    ONE_DAY data - verified directly, not assumed) so `entry_index`/
    `exit_index` are comparable across instruments without a separate
    date-alignment step.

    `max_single_instrument_delta_share`: if given, wires in
    portfolio/greek_aggregation.py's concentration check - a candidate
    entry is skipped (not just sized down) if it would push one
    instrument's share of TOTAL portfolio delta exposure above this
    fraction, checked against every currently-open position's Greeks
    REPRICED as of the candidate's own entry date (not their own stale
    entry-time Greeks, which is what a real risk check needs - exposure
    changes over a position's life as spot/time move). Only the
    concentration share is checked, deliberately: unlike the absolute
    delta/gamma/vega/theta limits check_portfolio_risk() also supports,
    a relative share needs no arbitrary, unjustified absolute Greek
    threshold to be picked - `None` (the default) preserves every prior
    Run's exact behavior, this check has never been exercised in this
    log before now."""
    events = []
    for instrument, trades in trades_by_instrument.items():
        for trade in trades:
            if trade.pnl is None:
                continue
            events.append((trade.entry_index, 1, instrument, "entry", trade))
            events.append((trade.exit_index, 0, instrument, "exit", trade))
    events.sort(key=lambda e: (e[0], e[1]))  # exits (priority 0) before entries (priority 1) on the same day - frees cash same-day

    cash = starting_capital
    peak = starting_capital
    max_dd_rupees = 0.0
    open_positions: dict[str, OpenPortfolioPosition] = {}
    simulated: list[PortfolioSimulatedTrade] = []
    skipped: list[tuple[str, Trade]] = []
    max_concurrent = 0

    for _, _, instrument, kind, trade in events:
        df = dfs_by_instrument[instrument]

        if kind == "exit":
            position = open_positions.pop(instrument, None)
            if position is None:
                continue  # entry for this trade was skipped as unaffordable - nothing open to exit

            exit_snapshot = theoretical_option_snapshot(
                df, trade.exit_index, position.strike, trade.expiry, trade.direction, risk_free_rate, vol_window,
            )
            exit_premium = exit_snapshot.price if exit_snapshot is not None else position.entry_premium
            gross_pnl = (exit_premium - position.entry_premium) * position.quantity
            txn_cost = option_round_trip_cost(position.entry_premium, exit_premium, position.quantity, rates)
            slippage_cost = tick_slippage_cost(tick_spread, position.quantity)
            cost = txn_cost + slippage_cost
            net_pnl = gross_pnl - cost

            cash_before = cash
            cash += position.entry_cost + net_pnl
            peak = max(peak, cash)
            max_dd_rupees = max(max_dd_rupees, peak - cash)

            simulated.append(PortfolioSimulatedTrade(
                instrument=instrument, trade=trade, lots=position.lots, quantity=position.quantity,
                cash_before=cash_before, capital_at_risk=position.entry_cost, equity_tier=classify_equity_tier(cash_before, starting_capital).tier,
                gross_pnl=gross_pnl, cost=cost, net_pnl=net_pnl, cash_after=cash,
                strike=position.strike, entry_premium=position.entry_premium, exit_premium=exit_premium,
            ))
            continue

        # entry
        if instrument in open_positions:
            skipped.append((instrument, trade))  # defensive - each instrument's own run_backtest() already guarantees this never happens
            continue

        tier = classify_equity_tier(cash, starting_capital)
        if not tier.allow_new_trades:
            skipped.append((instrument, trade))
            continue

        risk_budget = capital_at_risk_for_trade(cash, base_risk_pct, tier.size_multiplier)
        candidate = select_affordable_contract(
            df, trade.entry_index, trade.direction, trade.expiry, risk_free_rate,
            strike_increments[instrument], risk_budget, lot_sizes[instrument], vol_window, max_otm_steps,
        )
        if candidate is None:
            skipped.append((instrument, trade))
            continue

        entry_premium = candidate.snapshot.price
        max_loss_per_lot = entry_premium * lot_sizes[instrument]
        lots = math.floor(risk_budget / max_loss_per_lot) if max_loss_per_lot > 0 else 0
        if max_lots is not None:
            lots = min(lots, max_lots)
        if lots <= 0:
            skipped.append((instrument, trade))
            continue

        quantity = lots * lot_sizes[instrument]
        entry_cost = entry_premium * quantity
        if entry_cost > cash:
            # sized against the RISK BUDGET (a fraction of cash), but other
            # instruments' currently-open positions may have already locked
            # up enough of the REMAINING free cash that even this smaller
            # amount doesn't fit - the concurrency effect this module exists
            # to capture, not possible in the single-instrument case.
            skipped.append((instrument, trade))
            continue

        if max_single_instrument_delta_share is not None and open_positions:
            positions = []
            for open_instr, pos in open_positions.items():
                current_snapshot = theoretical_option_snapshot(
                    dfs_by_instrument[open_instr], trade.entry_index, pos.strike,
                    pos.trade.expiry, pos.trade.direction, risk_free_rate, vol_window,
                )
                if current_snapshot is not None:
                    positions.append(Position(instrument=open_instr, option_type=pos.trade.direction, quantity=pos.quantity, greeks=current_snapshot.greeks))
            positions.append(Position(instrument=instrument, option_type=trade.direction, quantity=quantity, greeks=candidate.snapshot.greeks))

            check = check_portfolio_risk(
                aggregate_portfolio_greeks(positions),
                PortfolioRiskLimits(
                    max_abs_delta=float("inf"), max_abs_gamma=float("inf"),
                    max_abs_vega=float("inf"), max_abs_theta=float("inf"),
                    max_single_instrument_delta_share=max_single_instrument_delta_share,
                ),
            )
            if not check.within_limits:
                skipped.append((instrument, trade))
                continue

        cash -= entry_cost
        open_positions[instrument] = OpenPortfolioPosition(
            instrument=instrument, trade=trade, lots=lots, quantity=quantity,
            entry_cost=entry_cost, entry_premium=entry_premium, strike=candidate.strike,
        )
        max_concurrent = max(max_concurrent, len(open_positions))

    return PortfolioEquitySimulationResult(
        starting_capital=starting_capital, ending_capital=cash, simulated_trades=simulated,
        skipped_trades=skipped, peak_capital=peak, max_drawdown_rupees=max_dd_rupees,
        max_drawdown_pct=(max_dd_rupees / peak * 100) if peak > 0 else 0.0,
        max_concurrent_positions=max_concurrent,
    )


def simulate_portfolio_equity_curve_per_instrument_tiers(
    trades_by_instrument: dict[str, list[Trade]],
    dfs_by_instrument: dict[str, pd.DataFrame],
    lot_sizes: dict[str, int],
    strike_increments: dict[str, float],
    starting_capital: float,
    risk_free_rate: float,
    base_risk_pct: float = DEFAULT_BASE_RISK_PCT,
    vol_window: int = 20,
    max_otm_steps: int = DEFAULT_MAX_OTM_STEPS,
    max_lots: int | None = None,
    rates: TransactionCostRates = TransactionCostRates(),
    tick_spread: float = 0.0,
    max_single_instrument_delta_share: float | None = None,
) -> PortfolioEquitySimulationResult:
    """The alternative pooling design Run 016 named as genuinely
    different, unbuilt future work (backtesting/BACKTESTS.md): the
    baseline simulate_portfolio_equity_curve() gates ALL instruments'
    sizing off ONE shared drawdown tier - Run 016 proved this transmits
    any single instrument's bad luck to the other two's position
    sizing, and Run 018 confirmed a Greek-concentration limit does not
    fix that specific problem (it only tightens the outcome
    distribution, not its typical value).

    This function keeps the SAME real, shared CASH pool for
    affordability (the genuine, hard constraint this whole portfolio
    line of work exists to model - you cannot spend cash you don't
    have, no matter how sizing is decided) but tracks a separate
    NOTIONAL capital balance per instrument for TIER CLASSIFICATION and
    RISK-BUDGET SIZING specifically - so a bad stretch in one
    instrument's own notional track record no longer drags down the
    RISK BUDGET computed for the other two, even though they still
    genuinely compete for the same real cash.

    DESIGN CHOICE, stated precisely: each instrument's notional starting
    sub-capital is an EQUAL split of `starting_capital` across however
    many instruments are present (simplest, most defensible default -
    no instrument-specific weighting is assumed or justified here).
    Realized P&L updates BOTH the real shared `cash` (the actual
    account's real money, still what ending_capital/drawdown are
    computed from) AND that instrument's own notional balance (used
    only to decide ITS OWN future tier/risk-budget) - two parallel
    bookkeeping systems serving different purposes, not two different
    claims about real money."""
    events = []
    for instrument, trades in trades_by_instrument.items():
        for trade in trades:
            if trade.pnl is None:
                continue
            events.append((trade.entry_index, 1, instrument, "entry", trade))
            events.append((trade.exit_index, 0, instrument, "exit", trade))
    events.sort(key=lambda e: (e[0], e[1]))

    n_instruments = len(trades_by_instrument)
    notional_starting = starting_capital / n_instruments
    notional_capital = {instrument: notional_starting for instrument in trades_by_instrument}

    cash = starting_capital
    peak = starting_capital
    max_dd_rupees = 0.0
    open_positions: dict[str, OpenPortfolioPosition] = {}
    simulated: list[PortfolioSimulatedTrade] = []
    skipped: list[tuple[str, Trade]] = []
    max_concurrent = 0

    for _, _, instrument, kind, trade in events:
        df = dfs_by_instrument[instrument]

        if kind == "exit":
            position = open_positions.pop(instrument, None)
            if position is None:
                continue

            exit_snapshot = theoretical_option_snapshot(
                df, trade.exit_index, position.strike, trade.expiry, trade.direction, risk_free_rate, vol_window,
            )
            exit_premium = exit_snapshot.price if exit_snapshot is not None else position.entry_premium
            gross_pnl = (exit_premium - position.entry_premium) * position.quantity
            txn_cost = option_round_trip_cost(position.entry_premium, exit_premium, position.quantity, rates)
            slippage_cost = tick_slippage_cost(tick_spread, position.quantity)
            cost = txn_cost + slippage_cost
            net_pnl = gross_pnl - cost

            cash_before = cash
            cash += position.entry_cost + net_pnl
            notional_capital[instrument] += net_pnl
            peak = max(peak, cash)
            max_dd_rupees = max(max_dd_rupees, peak - cash)

            simulated.append(PortfolioSimulatedTrade(
                instrument=instrument, trade=trade, lots=position.lots, quantity=position.quantity,
                cash_before=cash_before, capital_at_risk=position.entry_cost,
                equity_tier=classify_equity_tier(notional_capital[instrument] - net_pnl, notional_starting).tier,
                gross_pnl=gross_pnl, cost=cost, net_pnl=net_pnl, cash_after=cash,
                strike=position.strike, entry_premium=position.entry_premium, exit_premium=exit_premium,
            ))
            continue

        # entry
        if instrument in open_positions:
            skipped.append((instrument, trade))
            continue

        tier = classify_equity_tier(notional_capital[instrument], notional_starting)
        if not tier.allow_new_trades:
            skipped.append((instrument, trade))
            continue

        risk_budget = capital_at_risk_for_trade(notional_capital[instrument], base_risk_pct, tier.size_multiplier)
        candidate = select_affordable_contract(
            df, trade.entry_index, trade.direction, trade.expiry, risk_free_rate,
            strike_increments[instrument], risk_budget, lot_sizes[instrument], vol_window, max_otm_steps,
        )
        if candidate is None:
            skipped.append((instrument, trade))
            continue

        entry_premium = candidate.snapshot.price
        max_loss_per_lot = entry_premium * lot_sizes[instrument]
        lots = math.floor(risk_budget / max_loss_per_lot) if max_loss_per_lot > 0 else 0
        if max_lots is not None:
            lots = min(lots, max_lots)
        if lots <= 0:
            skipped.append((instrument, trade))
            continue

        quantity = lots * lot_sizes[instrument]
        entry_cost = entry_premium * quantity
        if entry_cost > cash:
            # the real, shared cash constraint still applies regardless of
            # notional-tier bookkeeping - a trade sized fine against its
            # own instrument's notional budget can still be rejected if
            # OTHER instruments' open positions have locked up the actual
            # cash it would need. This is the one place real concurrency
            # still bites under this alternative design.
            skipped.append((instrument, trade))
            continue

        if max_single_instrument_delta_share is not None and open_positions:
            positions = []
            for open_instr, pos in open_positions.items():
                current_snapshot = theoretical_option_snapshot(
                    dfs_by_instrument[open_instr], trade.entry_index, pos.strike,
                    pos.trade.expiry, pos.trade.direction, risk_free_rate, vol_window,
                )
                if current_snapshot is not None:
                    positions.append(Position(instrument=open_instr, option_type=pos.trade.direction, quantity=pos.quantity, greeks=current_snapshot.greeks))
            positions.append(Position(instrument=instrument, option_type=trade.direction, quantity=quantity, greeks=candidate.snapshot.greeks))

            check = check_portfolio_risk(
                aggregate_portfolio_greeks(positions),
                PortfolioRiskLimits(
                    max_abs_delta=float("inf"), max_abs_gamma=float("inf"),
                    max_abs_vega=float("inf"), max_abs_theta=float("inf"),
                    max_single_instrument_delta_share=max_single_instrument_delta_share,
                ),
            )
            if not check.within_limits:
                skipped.append((instrument, trade))
                continue

        cash -= entry_cost
        open_positions[instrument] = OpenPortfolioPosition(
            instrument=instrument, trade=trade, lots=lots, quantity=quantity,
            entry_cost=entry_cost, entry_premium=entry_premium, strike=candidate.strike,
        )
        max_concurrent = max(max_concurrent, len(open_positions))

    return PortfolioEquitySimulationResult(
        starting_capital=starting_capital, ending_capital=cash, simulated_trades=simulated,
        skipped_trades=skipped, peak_capital=peak, max_drawdown_rupees=max_dd_rupees,
        max_drawdown_pct=(max_dd_rupees / peak * 100) if peak > 0 else 0.0,
        max_concurrent_positions=max_concurrent,
    )
