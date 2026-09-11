"""Contract Selector (spec Phase 9): evaluates multiple strikes, not
just ATM, and picks one by a risk-adjusted score - not the deepest ITM
or the cheapest OTM by default.

Honest scope limit: the spec calls for selection "by risk-adjusted EV",
which needs a real probability of profit - that needs Model 2
(direction-probability) or Model 7 (trade-quality meta-model), neither
of which exist yet (Model 1 didn't beat its own baseline - see
models/EXPERIMENTS.md). Real EV is NOT computed here. Instead this uses
delta-per-rupee-of-premium ("capital efficiency") - a standard,
honest options-trading heuristic for a directional bet with a limited
budget: more directional exposure per rupee spent, without needing a
probability estimate this project doesn't have. Upgrade to genuine
risk-adjusted EV once a validated probability model exists - flagged
here so this doesn't quietly get mistaken for that later.
"""
import datetime as dt
from dataclasses import dataclass

import pandas as pd

from features.theoretical_options import TheoreticalOptionSnapshot, theoretical_option_snapshot

DEFAULT_STRIKE_OFFSETS = (-2, -1, 0, 1, 2)  # in units of strike_increment, 0 = ATM


@dataclass
class ContractCandidate:
    strike: float
    snapshot: TheoreticalOptionSnapshot
    capital_efficiency: float  # |delta| per rupee of premium - higher is better, not a real EV


def _capital_efficiency(snapshot: TheoreticalOptionSnapshot) -> float:
    if snapshot.price <= 0:
        return 0.0
    return abs(snapshot.greeks.delta) / snapshot.price


def evaluate_candidate_contracts(
    ohlcv: pd.DataFrame,
    as_of_index: int,
    direction: str,
    expiry: dt.date,
    risk_free_rate: float,
    strike_increment: float,
    vol_window: int = 20,
    strike_offsets: tuple[int, ...] = DEFAULT_STRIKE_OFFSETS,
) -> list[ContractCandidate]:
    """Returns one ContractCandidate per offset that has enough history
    to price (see features/theoretical_options.py - returns None, and
    is skipped here, if there isn't enough realized-vol history yet)."""
    spot = float(ohlcv["close"].iloc[as_of_index])
    atm_strike = round(spot / strike_increment) * strike_increment

    candidates = []
    for offset in strike_offsets:
        strike = atm_strike + offset * strike_increment
        snapshot = theoretical_option_snapshot(ohlcv, as_of_index, strike, expiry, direction, risk_free_rate, vol_window)
        if snapshot is None:
            continue
        candidates.append(ContractCandidate(strike=strike, snapshot=snapshot, capital_efficiency=_capital_efficiency(snapshot)))
    return candidates


def select_contract(
    ohlcv: pd.DataFrame,
    as_of_index: int,
    direction: str,
    expiry: dt.date,
    risk_free_rate: float,
    strike_increment: float,
    vol_window: int = 20,
    strike_offsets: tuple[int, ...] = DEFAULT_STRIKE_OFFSETS,
) -> ContractCandidate | None:
    candidates = evaluate_candidate_contracts(
        ohlcv, as_of_index, direction, expiry, risk_free_rate, strike_increment, vol_window, strike_offsets,
    )
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.capital_efficiency)
