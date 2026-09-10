"""Ties spot history + realized-volatility proxy + Black-Scholes into one
theoretical option snapshot - the concrete, honest realization of "the
same spot-price-proxy convention already established" for codex.

Explicit, permanent limitations of this approach (not deferred to a
later phase - these are ceilings of the data source itself, per spec
Section 81's "identify exactly what the chosen provider does/doesn't
supply, don't pretend otherwise"):

  - No real market-quoted premium or implied volatility exists for any
    HISTORICAL option contract (SmartAPI has no historical data for
    expired contracts). Every price/Greek this module produces is a
    THEORETICAL Black-Scholes value driven by a realized-volatility
    proxy, not a real market quote. It will diverge from what the real
    option actually traded at - real IV also prices in a volatility risk
    premium, skew, and term structure this proxy cannot capture.
  - Time-to-expiry uses a weekday-count approximation (5/7 of calendar
    days), not a real NSE/BSE trading-holiday calendar - a "trading
    calendar engine" accounting for actual market holidays is a
    separate, later phase. This is a small, known source of error here,
    not corrected in this module.
  - Historical futures OI/basis and any real historical option OI are
    NOT available either (SmartAPI's getOIData is documented "for live
    F&O contracts only") - there is no proxy for this at all, live-only,
    and only buildable once live wiring exists (a later phase). This
    module does not attempt one.
"""
import datetime as dt
from dataclasses import dataclass

import pandas as pd

from features.black_scholes import Greeks, bs_greeks, bs_price
from features.realized_volatility import TRADING_DAYS_PER_YEAR, realized_volatility_as_of

WEEKDAY_FRACTION_OF_CALENDAR_DAYS = 5 / 7


@dataclass
class TheoreticalOptionSnapshot:
    as_of_timestamp: pd.Timestamp
    spot: float
    strike: float
    expiry: dt.date
    option_type: str
    time_to_expiry_years: float
    volatility_used: float
    price: float
    greeks: Greeks


def approximate_time_to_expiry_years(as_of: dt.date, expiry: dt.date) -> float:
    """Weekday-count approximation, not a real trading-holiday calendar -
    see module docstring. Returns 0.0 (not negative) if expiry has
    already passed."""
    calendar_days = (expiry - as_of).days
    if calendar_days <= 0:
        return 0.0
    trading_days = calendar_days * WEEKDAY_FRACTION_OF_CALENDAR_DAYS
    return trading_days / TRADING_DAYS_PER_YEAR


def theoretical_option_snapshot(
    ohlcv: pd.DataFrame,
    as_of_index: int,
    strike: float,
    expiry: dt.date,
    option_type: str,
    risk_free_rate: float,
    vol_window: int = 20,
) -> TheoreticalOptionSnapshot | None:
    """`ohlcv` must have "timestamp" and "close" columns (the shape
    data/storage.py produces) sampled at one bar per trading day - vol
    annualization here assumes TRADING_DAYS_PER_YEAR bars/year. Returns
    None if there isn't yet enough history (vol_window+1 bars) as of
    as_of_index to estimate a volatility - never fabricates one."""
    volatility = realized_volatility_as_of(ohlcv["close"], as_of_index, vol_window, TRADING_DAYS_PER_YEAR)
    if volatility is None:
        return None

    as_of_ts = ohlcv["timestamp"].iloc[as_of_index]
    as_of_date = as_of_ts.date() if hasattr(as_of_ts, "date") else as_of_ts
    spot = float(ohlcv["close"].iloc[as_of_index])
    time_to_expiry_years = approximate_time_to_expiry_years(as_of_date, expiry)

    price = bs_price(spot, strike, time_to_expiry_years, risk_free_rate, volatility, option_type)
    greeks = bs_greeks(spot, strike, time_to_expiry_years, risk_free_rate, volatility, option_type)

    return TheoreticalOptionSnapshot(
        as_of_timestamp=as_of_ts, spot=spot, strike=strike, expiry=expiry, option_type=option_type.upper(),
        time_to_expiry_years=time_to_expiry_years, volatility_used=volatility, price=price, greeks=greeks,
    )
