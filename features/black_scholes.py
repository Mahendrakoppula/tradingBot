"""European option pricing + Greeks (Black-Scholes-Merton, optional
continuous dividend yield). Pure math, stdlib only (math.erf for the
normal CDF - no scipy dependency needed for this).

Why this exists at all (see features/theoretical_options.py for the
full explanation): SmartAPI has no historical premium data for expired
option contracts (confirmed limitation, established earlier this
project). There is therefore no real market-quoted option price or
implied volatility to work from for anything historical - only real
SPOT price history exists. This module is the pricing engine that lets
a later phase compute a THEORETICAL premium/Greeks from real spot data
plus a volatility estimate (realized vol, see
features/realized_volatility.py) as an honest, clearly-labeled proxy for
the real market Greeks that can't actually be reconstructed from this
data source.
"""
import math
from dataclasses import dataclass

_SQRT_2PI = math.sqrt(2 * math.pi)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta_per_day: float
    vega_per_1pct_vol: float
    rho_per_1pct_rate: float


def _d1_d2(spot: float, strike: float, time_to_expiry_years: float, risk_free_rate: float,
           volatility: float, dividend_yield: float) -> tuple[float, float]:
    d1 = (
        math.log(spot / strike) + (risk_free_rate - dividend_yield + 0.5 * volatility ** 2) * time_to_expiry_years
    ) / (volatility * math.sqrt(time_to_expiry_years))
    d2 = d1 - volatility * math.sqrt(time_to_expiry_years)
    return d1, d2


def bs_price(spot: float, strike: float, time_to_expiry_years: float, risk_free_rate: float,
             volatility: float, option_type: str, dividend_yield: float = 0.0) -> float:
    """option_type: "CE" or "PE". At/past expiry (time_to_expiry_years<=0)
    or degenerate volatility (<=0) returns pure intrinsic value - no time
    value left to price with Black-Scholes at that point."""
    option_type = option_type.upper()
    if option_type not in ("CE", "PE"):
        raise ValueError(f"option_type must be 'CE' or 'PE', got {option_type!r}")

    if time_to_expiry_years <= 0 or volatility <= 0:
        intrinsic = (spot - strike) if option_type == "CE" else (strike - spot)
        return max(intrinsic, 0.0)

    d1, d2 = _d1_d2(spot, strike, time_to_expiry_years, risk_free_rate, volatility, dividend_yield)
    disc_q = math.exp(-dividend_yield * time_to_expiry_years)
    disc_r = math.exp(-risk_free_rate * time_to_expiry_years)
    if option_type == "CE":
        return spot * disc_q * _norm_cdf(d1) - strike * disc_r * _norm_cdf(d2)
    return strike * disc_r * _norm_cdf(-d2) - spot * disc_q * _norm_cdf(-d1)


def bs_greeks(spot: float, strike: float, time_to_expiry_years: float, risk_free_rate: float,
              volatility: float, option_type: str, dividend_yield: float = 0.0) -> Greeks:
    """theta_per_day: already converted from the raw per-year formula
    (divided by 365) since that's what's actually useful for trading
    decisions. vega/rho are per 1 percentage-point (i.e. per 0.01) change
    in volatility/rate, the conventional trader-facing units, not the raw
    per-unit formula output."""
    option_type = option_type.upper()
    if option_type not in ("CE", "PE"):
        raise ValueError(f"option_type must be 'CE' or 'PE', got {option_type!r}")

    if time_to_expiry_years <= 0 or volatility <= 0:
        # At/past expiry: delta is a step function, everything else is zero.
        intrinsic = (spot - strike) if option_type == "CE" else (strike - spot)
        delta = (1.0 if option_type == "CE" else -1.0) if intrinsic > 0 else 0.0
        return Greeks(delta=delta, gamma=0.0, theta_per_day=0.0, vega_per_1pct_vol=0.0, rho_per_1pct_rate=0.0)

    d1, d2 = _d1_d2(spot, strike, time_to_expiry_years, risk_free_rate, volatility, dividend_yield)
    disc_q = math.exp(-dividend_yield * time_to_expiry_years)
    disc_r = math.exp(-risk_free_rate * time_to_expiry_years)
    sqrt_t = math.sqrt(time_to_expiry_years)
    pdf_d1 = _norm_pdf(d1)

    gamma = disc_q * pdf_d1 / (spot * volatility * sqrt_t)
    vega_raw = spot * disc_q * pdf_d1 * sqrt_t  # per unit (100%) vol change

    if option_type == "CE":
        delta = disc_q * _norm_cdf(d1)
        theta_raw = (
            -spot * disc_q * pdf_d1 * volatility / (2 * sqrt_t)
            - risk_free_rate * strike * disc_r * _norm_cdf(d2)
            + dividend_yield * spot * disc_q * _norm_cdf(d1)
        )
        rho_raw = strike * time_to_expiry_years * disc_r * _norm_cdf(d2)
    else:
        delta = disc_q * (_norm_cdf(d1) - 1.0)
        theta_raw = (
            -spot * disc_q * pdf_d1 * volatility / (2 * sqrt_t)
            + risk_free_rate * strike * disc_r * _norm_cdf(-d2)
            - dividend_yield * spot * disc_q * _norm_cdf(-d1)
        )
        rho_raw = -strike * time_to_expiry_years * disc_r * _norm_cdf(-d2)

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta_per_day=theta_raw / 365.0,
        vega_per_1pct_vol=vega_raw / 100.0,
        rho_per_1pct_rate=rho_raw / 100.0,
    )
