"""Sequence-risk validation for the REAL, capital-compounding equity
curve - closes a real gap between Run 003 (backtesting/BACKTESTS.md)
and Run 008. Run 003's monte_carlo_trade_sequence() reshuffles raw
one-conceptual-unit P&Ls and recomputes a simple cumulative-sum
drawdown - a valid check for that idealized view, but it has never been
applied to equity_simulation.py's REAL account curve, where reordering
trades genuinely changes the outcome, not just the drawdown path: lot
sizing, equity-protection tier gating, and even whether a trade is
affordable at all all depend on CURRENT capital, which is itself
path-dependent under compounding. Run 008's own dramatic headline
numbers (+583% to +2144%) were explicitly flagged as possibly an
artifact of continuous compounding - this module tests that directly:
would a DIFFERENT realistic ordering of the exact same set of trades
have produced a wildly different ending capital, or is the result
reasonably robust to sequencing?

Deliberately does NOT reshuffle backtesting.event_loop.Trade objects
and re-run simulate_equity_curve_with_affordable_contracts() from
scratch: that function always re-sorts its input by entry_index
(chronological order is a real invariant elsewhere in this project,
not something to silently break), and re-running the affordability
search per reshuffle would be needlessly slow. Instead, replay_equity_curve_in_order()
reuses each trade's ALREADY-COMPUTED, order-independent real values
(entry_premium, exit_premium, strike - priced once from real historical
spot/volatility at that trade's own real entry date) and only
recomputes what legitimately depends on the capital PATH: tier
classification, risk-based lot sizing, affordability at that point in
the sequence, cost, and the resulting P&L/capital trajectory.
"""
import math
import random
from dataclasses import dataclass

from backtesting.equity_simulation import DEFAULT_BASE_RISK_PCT, EquitySimulationResult, SimulatedTrade
from backtesting.tick_slippage import tick_slippage_cost
from backtesting.trade_record import Trade
from execution.transaction_costs import TransactionCostRates, option_round_trip_cost
from risk.equity_protection import classify_equity_tier
from risk.position_sizing import capital_at_risk_for_trade


def replay_equity_curve_in_order(
    ordered_simulated_trades: list[SimulatedTrade],
    lot_size: int,
    starting_capital: float,
    base_risk_pct: float = DEFAULT_BASE_RISK_PCT,
    max_lots: int | None = None,
    rates: TransactionCostRates = TransactionCostRates(),
    tick_spread: float = 0.0,
) -> EquitySimulationResult:
    """Re-runs only the capital-compounding arithmetic for a GIVEN
    order of already-priced trades. `ordered_simulated_trades` supplies
    each trade's real entry_premium/exit_premium/strike (and the
    underlying Trade for reference) - this function does not touch or
    re-derive those; it only decides, in the given order, how much
    capital is at risk, how many lots that affords, and what the
    resulting P&L and running capital are."""
    capital = starting_capital
    peak = starting_capital
    max_dd_rupees = 0.0
    simulated: list[SimulatedTrade] = []
    skipped: list[Trade] = []

    for st in ordered_simulated_trades:
        trade = st.trade
        tier = classify_equity_tier(capital, starting_capital)
        if not tier.allow_new_trades:
            skipped.append(trade)
            continue

        risk_budget = capital_at_risk_for_trade(capital, base_risk_pct, tier.size_multiplier)
        entry_premium = st.entry_premium
        exit_premium = st.exit_premium
        max_loss_per_lot = entry_premium * lot_size
        lots = math.floor(risk_budget / max_loss_per_lot) if max_loss_per_lot > 0 else 0
        if max_lots is not None:
            lots = min(lots, max_lots)
        if lots <= 0:
            skipped.append(trade)
            continue

        quantity = lots * lot_size
        gross_pnl = (exit_premium - entry_premium) * quantity
        txn_cost = option_round_trip_cost(entry_premium, exit_premium, quantity, rates)
        slippage_cost = tick_slippage_cost(tick_spread, quantity)
        cost = txn_cost + slippage_cost
        net_pnl = gross_pnl - cost

        capital_before = capital
        capital += net_pnl
        peak = max(peak, capital)
        max_dd_rupees = max(max_dd_rupees, peak - capital)

        simulated.append(SimulatedTrade(
            trade=trade, lots=lots, quantity=quantity, capital_before=capital_before,
            capital_at_risk=risk_budget, equity_tier=tier.tier, gross_pnl=gross_pnl,
            cost=cost, net_pnl=net_pnl, capital_after=capital,
            strike=st.strike, entry_premium=entry_premium, exit_premium=exit_premium,
        ))

    return EquitySimulationResult(
        starting_capital=starting_capital, ending_capital=capital, simulated_trades=simulated,
        skipped_trades=skipped, peak_capital=peak, max_drawdown_rupees=max_dd_rupees,
        max_drawdown_pct=(max_dd_rupees / peak * 100) if peak > 0 else 0.0,
    )


@dataclass
class EquitySequenceRiskResult:
    n_simulations: int
    observed_ending_capital: float
    mean_simulated_ending_capital: float
    ci_low: float  # 5th percentile of simulated ending capitals
    ci_high: float  # 95th percentile
    fraction_worse_than_observed: float  # fraction of reshuffles ending with LESS capital than observed


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = (pct / 100.0) * (len(sorted_values) - 1)
    lower = int(idx)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = idx - lower
    return sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac


def monte_carlo_equity_sequence(
    base_result: EquitySimulationResult,
    lot_size: int,
    n_simulations: int = 10_000,
    base_risk_pct: float = DEFAULT_BASE_RISK_PCT,
    max_lots: int | None = None,
    rates: TransactionCostRates = TransactionCostRates(),
    tick_spread: float = 0.0,
    rng: random.Random | None = None,
) -> EquitySequenceRiskResult:
    """Reshuffles the ORDER of base_result's own simulated trades (never
    their values - each trade's real entry/exit premium and strike stay
    fixed) and replays the compounding arithmetic under each new order,
    holding the SET of trades that were ever priced fixed. Answers: was
    the observed ending capital a lucky/unlucky ordering of a fixed set
    of real, historically-priced trades, or does it hold up regardless
    of sequence?"""
    rng = rng or random.Random()
    trades = list(base_result.simulated_trades)
    observed = base_result.ending_capital

    simulated_endings = []
    for _ in range(n_simulations):
        rng.shuffle(trades)
        result = replay_equity_curve_in_order(
            trades, lot_size, base_result.starting_capital, base_risk_pct, max_lots, rates, tick_spread,
        )
        simulated_endings.append(result.ending_capital)
    simulated_endings.sort()

    return EquitySequenceRiskResult(
        n_simulations=n_simulations,
        observed_ending_capital=observed,
        mean_simulated_ending_capital=sum(simulated_endings) / n_simulations,
        ci_low=_percentile(simulated_endings, 5.0),
        ci_high=_percentile(simulated_endings, 95.0),
        fraction_worse_than_observed=sum(1 for e in simulated_endings if e < observed) / n_simulations,
    )
