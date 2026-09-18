"""Sequence-risk validation for the portfolio-level equity curve (Run
014, backtesting/BACKTESTS.md) - the concrete next step Run 014's own
writeup flagged: its pooled headline return carries the same
sequence-risk caveat Run 013 already proved dominates single-instrument
compounding curves, plausibly amplified by pooling three instruments'
trades into one shared trajectory.

Run 013's own reshuffle (backtesting/equity_sequence_risk.py) doesn't
extend directly: it reorders a single, strictly SEQUENTIAL list of
non-overlapping trades, which only makes sense because one instrument
never has two open positions at once. A portfolio has THREE such
sequential streams (one per instrument, each still individually
non-overlapping - event_loop.py's own single-position-per-instrument
rule holds within each) that genuinely overlap IN TIME with each other -
that concurrency is the entire subject of Run 014, so reshuffling
"the order of all trades" without preserving each instrument's own
internal order would silently create impossible same-instrument overlaps
and erase the very thing being tested.

DESIGN CHOICE, stated precisely so the test's scope is clear: this
reshuffles HOW the three instruments' own (real, historically-ordered)
trade sequences INTERLEAVE with each other - like a riffle shuffle of
three already-sorted decks, each deck's internal order is preserved
exactly, but their PACING relative to each other is randomized. Held
fixed: each instrument's own relative trade order, and every trade's
real entry_premium/exit_premium/strike/duration (priced once from real
historical data, exactly as Run 013 held these fixed). Varied: the
LOGICAL arrival time of each instrument's trades relative to the other
two, which determines what overlaps happen and when, and therefore how
capital gets allocated across the shared pool. Real calendar dates are
abandoned for this logical timeline (duration in bars is preserved, but
WHEN in bar-time a sequence starts is what's randomized) - the same
abstraction Run 013's own reshuffle already relies on (it discards real
calendar dates too, once trades are collapsed into an ordered list).
"""
import math
import random
from dataclasses import dataclass

from backtesting.equity_simulation import DEFAULT_BASE_RISK_PCT
from backtesting.portfolio_equity_simulation import PortfolioEquitySimulationResult, PortfolioSimulatedTrade
from backtesting.tick_slippage import tick_slippage_cost
from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates, option_round_trip_cost
from features.black_scholes import Greeks
from features.theoretical_options import theoretical_option_snapshot
from portfolio.greek_aggregation import Position, PortfolioRiskLimits, aggregate_portfolio_greeks, check_portfolio_risk
from risk.equity_protection import classify_equity_tier
from risk.position_sizing import capital_at_risk_for_trade


@dataclass
class _LogicalTradeEvent:
    instrument: str
    trade: Trade
    entry_premium: float
    exit_premium: float
    strike: float
    duration: int  # exit_index - entry_index from the real run, in bars
    greeks: Greeks | None = None  # entry-time Greeks, reshuffle-invariant (see monte_carlo_portfolio_sequence) - only needed when concentration-limit-testing


def _riffle_interleave(sequences: list[list[_LogicalTradeEvent]], rng: random.Random) -> list[_LogicalTradeEvent]:
    """Randomly interleaves several already-ordered sequences into one
    combined sequence, preserving each sequence's OWN internal relative
    order - a riffle shuffle of several sorted decks. This is the
    reshuffle mechanic itself: each instrument's own trades stay in
    their real relative order, only the interleaving across instruments
    is randomized."""
    pointers = [0] * len(sequences)
    remaining = sum(len(s) for s in sequences)
    result = []
    while remaining > 0:
        choices = [i for i, seq in enumerate(sequences) if pointers[i] < len(seq)]
        i = rng.choice(choices)
        result.append(sequences[i][pointers[i]])
        pointers[i] += 1
        remaining -= 1
    return result


def replay_portfolio_in_logical_order(
    ordered_events: list[_LogicalTradeEvent],
    lot_sizes: dict[str, int],
    starting_capital: float,
    base_risk_pct: float = DEFAULT_BASE_RISK_PCT,
    max_lots: int | None = None,
    rates: TransactionCostRates = TransactionCostRates(),
    tick_spread: float = 0.0,
    max_single_instrument_delta_share: float | None = None,
) -> PortfolioEquitySimulationResult:
    """Assigns each event a LOGICAL entry slot (its position in
    `ordered_events`) and a logical exit slot (entry slot + the event's
    own real duration in bars), then replays the same capital mechanics
    as portfolio_equity_simulation.py's real-calendar version - tier
    classification, risk-based lot sizing, cash locked up at entry and
    refunded at exit - over this synthetic timeline instead of real
    calendar dates.

    `max_single_instrument_delta_share`: mirrors
    portfolio_equity_simulation.py's own Run 017 concentration check,
    with ONE necessary difference stated plainly: that version reprices
    every open position's CURRENT Greeks as of the new candidate's real
    calendar date. There is no real calendar date on this logical
    timeline to reprice against, so this uses each event's own
    ENTRY-TIME Greeks instead (requires `ordered_events` to have
    `.greeks` populated - see monte_carlo_portfolio_sequence, which
    precomputes them once since they don't depend on the reshuffle at
    all). An approximation, not a re-derivation of "current" exposure -
    stated once here rather than re-asserted at every call site."""
    scheduled = [(t, t + ev.duration, ev) for t, ev in enumerate(ordered_events)]
    events = []
    for entry_t, exit_t, ev in scheduled:
        events.append((entry_t, 1, ev, "entry"))
        events.append((exit_t, 0, ev, "exit"))
    events.sort(key=lambda e: (e[0], e[1]))

    cash = starting_capital
    peak = starting_capital
    max_dd_rupees = 0.0
    open_positions: dict[str, dict] = {}
    simulated: list[PortfolioSimulatedTrade] = []
    skipped: list[tuple[str, Trade]] = []
    max_concurrent = 0

    for _, _, ev, kind in events:
        if kind == "exit":
            position = open_positions.pop(ev.instrument, None)
            if position is None:
                continue
            gross_pnl = (ev.exit_premium - ev.entry_premium) * position["quantity"]
            txn_cost = option_round_trip_cost(ev.entry_premium, ev.exit_premium, position["quantity"], rates)
            slippage_cost = tick_slippage_cost(tick_spread, position["quantity"])
            cost = txn_cost + slippage_cost
            net_pnl = gross_pnl - cost
            cash_before = cash
            cash += position["entry_cost"] + net_pnl
            peak = max(peak, cash)
            max_dd_rupees = max(max_dd_rupees, peak - cash)
            simulated.append(PortfolioSimulatedTrade(
                instrument=ev.instrument, trade=ev.trade, lots=position["lots"], quantity=position["quantity"],
                cash_before=cash_before, capital_at_risk=position["entry_cost"],
                equity_tier=classify_equity_tier(cash_before, starting_capital).tier,
                gross_pnl=gross_pnl, cost=cost, net_pnl=net_pnl, cash_after=cash,
                strike=ev.strike, entry_premium=ev.entry_premium, exit_premium=ev.exit_premium,
            ))
            continue

        # entry
        if ev.instrument in open_positions:
            skipped.append((ev.instrument, ev.trade))
            continue
        tier = classify_equity_tier(cash, starting_capital)
        if not tier.allow_new_trades:
            skipped.append((ev.instrument, ev.trade))
            continue
        risk_budget = capital_at_risk_for_trade(cash, base_risk_pct, tier.size_multiplier)
        max_loss_per_lot = ev.entry_premium * lot_sizes[ev.instrument]
        lots = math.floor(risk_budget / max_loss_per_lot) if max_loss_per_lot > 0 else 0
        if max_lots is not None:
            lots = min(lots, max_lots)
        if lots <= 0:
            skipped.append((ev.instrument, ev.trade))
            continue
        quantity = lots * lot_sizes[ev.instrument]
        entry_cost = ev.entry_premium * quantity
        if entry_cost > cash:
            skipped.append((ev.instrument, ev.trade))
            continue
        if max_single_instrument_delta_share is not None and open_positions and ev.greeks is not None:
            positions = [
                Position(instrument=open_instr, option_type=pos["direction"], quantity=pos["quantity"], greeks=pos["greeks"])
                for open_instr, pos in open_positions.items()
                if pos["greeks"] is not None
            ]
            positions.append(Position(instrument=ev.instrument, option_type=ev.trade.direction, quantity=quantity, greeks=ev.greeks))
            check = check_portfolio_risk(
                aggregate_portfolio_greeks(positions),
                PortfolioRiskLimits(
                    max_abs_delta=float("inf"), max_abs_gamma=float("inf"),
                    max_abs_vega=float("inf"), max_abs_theta=float("inf"),
                    max_single_instrument_delta_share=max_single_instrument_delta_share,
                ),
            )
            if not check.within_limits:
                skipped.append((ev.instrument, ev.trade))
                continue

        cash -= entry_cost
        open_positions[ev.instrument] = {
            "lots": lots, "quantity": quantity, "entry_cost": entry_cost,
            "direction": ev.trade.direction, "greeks": ev.greeks,
        }
        max_concurrent = max(max_concurrent, len(open_positions))

    return PortfolioEquitySimulationResult(
        starting_capital=starting_capital, ending_capital=cash, simulated_trades=simulated,
        skipped_trades=skipped, peak_capital=peak, max_drawdown_rupees=max_dd_rupees,
        max_drawdown_pct=(max_dd_rupees / peak * 100) if peak > 0 else 0.0,
        max_concurrent_positions=max_concurrent,
    )


@dataclass
class PortfolioSequenceRiskResult:
    n_simulations: int
    observed_ending_capital: float
    mean_simulated_ending_capital: float
    ci_low: float
    ci_high: float
    fraction_worse_than_observed: float


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = (pct / 100.0) * (len(sorted_values) - 1)
    lower = int(idx)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = idx - lower
    return sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac


def monte_carlo_portfolio_sequence(
    base_result: PortfolioEquitySimulationResult,
    lot_sizes: dict[str, int],
    n_simulations: int = 5000,
    base_risk_pct: float = DEFAULT_BASE_RISK_PCT,
    max_lots: int | None = None,
    rates: TransactionCostRates = TransactionCostRates(),
    tick_spread: float = 0.0,
    max_single_instrument_delta_share: float | None = None,
    dfs_by_instrument: dict | None = None,
    risk_free_rate: float = 0.07,
    vol_window: int = 20,
    rng: random.Random | None = None,
) -> PortfolioSequenceRiskResult:
    """Riffle-shuffles the interleaving of base_result's own three
    instrument trade streams (each stream's own internal order held
    fixed) and replays the logical-timeline capital mechanics under
    each new interleaving.

    `max_single_instrument_delta_share`: extends Run 017's concentration
    check to this reshuffle test - answers whether it helps under
    TYPICAL (not just the one observed, already-known-lucky) ordering.
    Requires `dfs_by_instrument` (real OHLCV per instrument) to price
    each trade's entry-time Greeks ONCE up front - these are
    reshuffle-invariant (a trade's own real entry_index/strike/expiry
    never changes across reshuffles), so pricing them once and reusing
    across all `n_simulations` replays avoids repricing the same fixed
    value millions of times."""
    rng = rng or random.Random()

    by_instrument: dict[str, list[PortfolioSimulatedTrade]] = {}
    for st in base_result.simulated_trades:
        by_instrument.setdefault(st.instrument, []).append(st)
    for instr in by_instrument:
        by_instrument[instr].sort(key=lambda st: st.trade.entry_index)

    def _entry_greeks(instr: str, st: PortfolioSimulatedTrade) -> Greeks | None:
        if dfs_by_instrument is None:
            return None
        snapshot = theoretical_option_snapshot(
            dfs_by_instrument[instr], st.trade.entry_index, st.strike, st.trade.expiry,
            st.trade.direction, risk_free_rate, vol_window,
        )
        return snapshot.greeks if snapshot is not None else None

    sequences = [
        [
            _LogicalTradeEvent(
                instrument=instr, trade=st.trade, entry_premium=st.entry_premium, exit_premium=st.exit_premium,
                strike=st.strike, duration=(st.trade.exit_index - st.trade.entry_index),
                greeks=_entry_greeks(instr, st),
            )
            for st in trades
        ]
        for instr, trades in by_instrument.items()
    ]

    observed = base_result.ending_capital
    simulated_endings = []
    for _ in range(n_simulations):
        ordered = _riffle_interleave(sequences, rng)
        result = replay_portfolio_in_logical_order(
            ordered, lot_sizes, base_result.starting_capital, base_risk_pct, max_lots, rates, tick_spread,
            max_single_instrument_delta_share,
        )
        simulated_endings.append(result.ending_capital)
    simulated_endings.sort()

    return PortfolioSequenceRiskResult(
        n_simulations=n_simulations,
        observed_ending_capital=observed,
        mean_simulated_ending_capital=sum(simulated_endings) / n_simulations,
        ci_low=_percentile(simulated_endings, 5.0),
        ci_high=_percentile(simulated_endings, 95.0),
        fraction_worse_than_observed=sum(1 for e in simulated_endings if e < observed) / n_simulations,
    )
