"""Moneyness-path analysis - investigates Run 008's second flagged risk
(backtesting/BACKTESTS.md): Investigation 001/Run 004 established that
changing STRIKE SELECTION doesn't affect ENTRY TIMING - true by
construction in this codebase (process_bar()'s entry/exit decisions are
computed purely from spot price action; they never consult premium or
strike at all). But that was never really the open question. The real
one: does reusing the SAME spot-price-based stop/target EXIT framework
(risk/dynamic_stops.py, calibrated implicitly around near-ATM
sensitivity, since strategies/strikes weren't considered when it was
built) produce a MEANINGFUL exit for a FAR-OTM contract, whose value
only moves substantially once spot actually approaches or crosses its
own, much more distant, strike?

This checks, for each simulated trade, whether spot ever came
meaningfully close to (or crossed) the SELECTED strike at any point
during the hold - not just the entry/exit snapshot. If "target" exits
are dominated by trades where spot never got anywhere near the strike,
that's real evidence the spot-based exit framework's "target hit" label
doesn't mean what it's implicitly assumed to mean for far-OTM contracts
(a large realized directional/convexity gain) - it may just be
coincidental timing, with the trade's real P&L driven by something else
(theta decay, a small vega/vol move) rather than the "target" event
itself.
"""
from dataclasses import dataclass

import pandas as pd

from backtesting.equity_simulation import SimulatedTrade


@dataclass
class MoneynessPath:
    simulated_trade: SimulatedTrade
    entry_moneyness_pct: float  # signed: (entry_spot - strike) / strike * 100 for CE-style "distance OTM"; see docstring
    exit_moneyness_pct: float
    closest_moneyness_pct: float  # least-OTM point reached during the whole hold (>= 0 means it crossed the strike)
    crossed_strike: bool


def _otm_signed_distance_pct(direction: str, spot: float, strike: float) -> float:
    """Positive = still OTM by this %, 0 = exactly at the strike,
    negative = ITM by this % - a consistent sign convention across CE
    (needs spot to RISE to close the gap) and PE (needs spot to FALL)."""
    if direction.upper() == "CE":
        return (strike - spot) / strike * 100
    return (spot - strike) / strike * 100


def analyze_moneyness_path(simulated_trade: SimulatedTrade, ohlcv: pd.DataFrame) -> MoneynessPath:
    trade = simulated_trade.trade
    strike = simulated_trade.strike
    direction = trade.direction

    entry_dist = _otm_signed_distance_pct(direction, trade.entry_spot, strike)
    exit_dist = _otm_signed_distance_pct(direction, trade.exit_spot, strike)

    window = ohlcv.iloc[trade.entry_index: trade.exit_index + 1]
    if direction.upper() == "CE":
        # closest approach = highest high reached during the hold
        closest_spot = float(window["high"].max())
    else:
        closest_spot = float(window["low"].min())
    closest_dist = _otm_signed_distance_pct(direction, closest_spot, strike)

    return MoneynessPath(
        simulated_trade=simulated_trade, entry_moneyness_pct=entry_dist, exit_moneyness_pct=exit_dist,
        closest_moneyness_pct=closest_dist, crossed_strike=bool(closest_dist <= 0),
    )


def analyze_all(simulated_trades: list[SimulatedTrade], ohlcv: pd.DataFrame) -> list[MoneynessPath]:
    return [analyze_moneyness_path(st, ohlcv) for st in simulated_trades]


@dataclass
class ExitReasonMoneynessSummary:
    exit_reason: str
    n_trades: int
    n_crossed_strike: int
    fraction_crossed_strike: float
    mean_closest_moneyness_pct: float


def summarize_by_exit_reason(paths: list[MoneynessPath]) -> dict[str, ExitReasonMoneynessSummary]:
    """Groups by the ORIGINAL backtest's exit_reason (stop/target/
    end_of_data - determined in spot terms, unrelated to the
    affordability-selected strike) and reports how often the far-OTM
    contract's OWN strike was ever actually approached/crossed during
    that same hold - the direct answer to whether "target hit" means
    anything for these specific trades."""
    by_reason: dict[str, list[MoneynessPath]] = {}
    for p in paths:
        by_reason.setdefault(p.simulated_trade.trade.exit_reason, []).append(p)

    result = {}
    for reason, group in by_reason.items():
        n = len(group)
        n_crossed = sum(1 for p in group if p.crossed_strike)
        result[reason] = ExitReasonMoneynessSummary(
            exit_reason=reason, n_trades=n, n_crossed_strike=n_crossed,
            fraction_crossed_strike=n_crossed / n if n else 0.0,
            mean_closest_moneyness_pct=sum(p.closest_moneyness_pct for p in group) / n if n else 0.0,
        )
    return result
