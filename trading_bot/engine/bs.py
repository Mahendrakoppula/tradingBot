"""Black-Scholes pricing, Greeks and implied volatility for European index
options - the fallback when the broker's Greeks endpoint has no data
(it is NSE-only; SENSEX/BFO needs this) and the cross-check when it
does. Pure math, stdlib only.

Time is in YEARS of calendar time; theta is returned PER CALENDAR DAY in
premium points (negative for a long option), which is the unit the decay
filter (§24) and the risk engine compare against costs.
"""
import math
from dataclasses import dataclass

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _n(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    theta_per_day: float
    vega: float  # per 1.00 (100 points) of vol; divide by 100 for per-vol-point
    iv: float


def price(spot: float, strike: float, t_years: float, iv: float, option_type: str, rate: float = 0.065) -> float:
    if t_years <= 0 or iv <= 0:
        intrinsic = max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)
        return intrinsic
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_years) / (iv * math.sqrt(t_years))
    d2 = d1 - iv * math.sqrt(t_years)
    disc = math.exp(-rate * t_years)
    if option_type == "CE":
        return spot * _N(d1) - strike * disc * _N(d2)
    return strike * disc * _N(-d2) - spot * _N(-d1)


def greeks(spot: float, strike: float, t_years: float, iv: float, option_type: str, rate: float = 0.065) -> Greeks:
    if t_years <= 0 or iv <= 0:
        p = price(spot, strike, t_years, iv, option_type, rate)
        itm = (spot > strike) if option_type == "CE" else (spot < strike)
        d = (1.0 if itm else 0.0) * (1 if option_type == "CE" else -1)
        return Greeks(p, d, 0.0, 0.0, 0.0, iv)
    sq = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_years) / (iv * sq)
    d2 = d1 - iv * sq
    disc = math.exp(-rate * t_years)
    pdf = _n(d1)
    gamma = pdf / (spot * iv * sq)
    vega = spot * pdf * sq
    if option_type == "CE":
        delta = _N(d1)
        theta = (-(spot * pdf * iv) / (2 * sq) - rate * strike * disc * _N(d2))
    else:
        delta = _N(d1) - 1.0
        theta = (-(spot * pdf * iv) / (2 * sq) + rate * strike * disc * _N(-d2))
    return Greeks(price(spot, strike, t_years, iv, option_type, rate), delta, gamma, theta / 365.0, vega, iv)


def implied_vol(market_price: float, spot: float, strike: float, t_years: float, option_type: str,
                rate: float = 0.065, lo: float = 0.01, hi: float = 3.0, tol: float = 1e-4) -> float | None:
    """Bisection on price; None when the price is outside the no-arbitrage
    band (below intrinsic or above the vol ceiling)."""
    if t_years <= 0 or market_price <= 0:
        return None
    if price(spot, strike, t_years, lo, option_type, rate) > market_price:
        return None
    if price(spot, strike, t_years, hi, option_type, rate) < market_price:
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        p = price(spot, strike, t_years, mid, option_type, rate)
        if abs(p - market_price) < tol:
            return mid
        if p > market_price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


SESSION_DAY_FRACTION = 6.25 / 24.0  # a 09:15-15:30 session as a share of a calendar day


def years_to_expiry(days_to_expiry: int, session_elapsed: float) -> float:
    """Calendar years until the expiry session close. `days_to_expiry` is
    the calendar-day difference (0 on expiry day); `session_elapsed` is the
    share of today's session already gone (0-1). On expiry day this decays
    to zero at the close, which is what theta/IV need."""
    remaining_days = max(0.0, float(days_to_expiry) + (1.0 - max(0.0, min(1.0, session_elapsed))) * SESSION_DAY_FRACTION)
    return remaining_days / 365.0
