"""Portfolio-level Greek aggregation across instruments (spec: prevent
hidden concentration - NIFTY/BANKNIFTY/SENSEX positions can each look
individually fine while collectively pushing the account's real
delta/gamma/vega/theta exposure far beyond what any single position
implies).

A position's `quantity` is the SIGNED number of underlying units
(long positive, short negative) - already lots*lot_size, resolved by
the caller (risk/position_sizing.py handles lot sizing separately, kept
out of this module on purpose). `greeks` are PER-UNIT
(features/black_scholes.py's own convention) - position-level exposure
is quantity * per-unit Greek.
"""
from dataclasses import dataclass, field

from features.black_scholes import Greeks


@dataclass
class Position:
    instrument: str
    option_type: str
    quantity: int
    greeks: Greeks


@dataclass
class PortfolioGreeks:
    total_delta: float = 0.0
    total_gamma: float = 0.0
    total_theta_per_day: float = 0.0
    total_vega_per_1pct_vol: float = 0.0
    delta_by_instrument: dict[str, float] = field(default_factory=dict)


def aggregate_portfolio_greeks(positions: list[Position]) -> PortfolioGreeks:
    result = PortfolioGreeks()
    for pos in positions:
        result.total_delta += pos.quantity * pos.greeks.delta
        result.total_gamma += pos.quantity * pos.greeks.gamma
        result.total_theta_per_day += pos.quantity * pos.greeks.theta_per_day
        result.total_vega_per_1pct_vol += pos.quantity * pos.greeks.vega_per_1pct_vol
        result.delta_by_instrument[pos.instrument] = (
            result.delta_by_instrument.get(pos.instrument, 0.0) + pos.quantity * pos.greeks.delta
        )
    return result


@dataclass
class PortfolioRiskLimits:
    max_abs_delta: float
    max_abs_gamma: float
    max_abs_vega: float
    max_abs_theta: float
    max_single_instrument_delta_share: float = 1.0  # 1.0 = no concentration limit enforced


@dataclass
class PortfolioRiskCheck:
    within_limits: bool
    breaches: list[str] = field(default_factory=list)


def check_portfolio_risk(portfolio: PortfolioGreeks, limits: PortfolioRiskLimits) -> PortfolioRiskCheck:
    breaches: list[str] = []

    if abs(portfolio.total_delta) > limits.max_abs_delta:
        breaches.append(f"Total delta {portfolio.total_delta:.2f} exceeds limit {limits.max_abs_delta:.2f}")
    if abs(portfolio.total_gamma) > limits.max_abs_gamma:
        breaches.append(f"Total gamma {portfolio.total_gamma:.4f} exceeds limit {limits.max_abs_gamma:.4f}")
    if abs(portfolio.total_vega_per_1pct_vol) > limits.max_abs_vega:
        breaches.append(f"Total vega {portfolio.total_vega_per_1pct_vol:.2f} exceeds limit {limits.max_abs_vega:.2f}")
    if abs(portfolio.total_theta_per_day) > limits.max_abs_theta:
        breaches.append(f"Total theta/day {portfolio.total_theta_per_day:.2f} exceeds limit {limits.max_abs_theta:.2f}")

    total_abs_delta_by_instrument = sum(abs(d) for d in portfolio.delta_by_instrument.values())
    if total_abs_delta_by_instrument > 0:
        for instrument, delta in portfolio.delta_by_instrument.items():
            share = abs(delta) / total_abs_delta_by_instrument
            if share > limits.max_single_instrument_delta_share:
                breaches.append(
                    f"{instrument} accounts for {share:.0%} of total delta exposure "
                    f"(limit {limits.max_single_instrument_delta_share:.0%}) - hidden concentration risk"
                )

    return PortfolioRiskCheck(within_limits=len(breaches) == 0, breaches=breaches)
