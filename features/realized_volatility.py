"""Annualized realized (historical) volatility from real spot price
history - the only honest volatility input available for
features/black_scholes.py, since no real market-quoted option prices
exist to back out a true implied volatility (SmartAPI has no historical
premium data for expired contracts - confirmed limitation). This is a
PROXY, not real IV: real IV also prices in a volatility risk premium,
skew, and term-structure effects that a simple realized-vol estimate
cannot capture. Every caller of this module's output must be labeled as
using a proxy, not real market IV - never presented as if it were.
"""
import math

import pandas as pd

TRADING_DAYS_PER_YEAR = 252
TRADING_MINUTES_PER_DAY = 375  # NSE/BSE equity/index session: 09:15-15:30 IST


def log_returns(closes: pd.Series) -> pd.Series:
    return (closes / closes.shift(1)).apply(math.log)


def realized_volatility(
    closes: pd.Series,
    window: int,
    bars_per_year: float = TRADING_DAYS_PER_YEAR,
) -> pd.Series:
    """Rolling annualized stdev of log returns. `bars_per_year` must
    match the bar interval `closes` was sampled at - e.g.
    TRADING_DAYS_PER_YEAR for daily closes, or
    TRADING_DAYS_PER_YEAR * TRADING_MINUTES_PER_DAY for 1-minute closes -
    getting this wrong silently mis-scales every downstream Greek."""
    returns = log_returns(closes)
    return returns.rolling(window=window, min_periods=window).std() * math.sqrt(bars_per_year)


def realized_volatility_as_of(
    closes: pd.Series,
    as_of_index: int,
    window: int,
    bars_per_year: float = TRADING_DAYS_PER_YEAR,
) -> float | None:
    """Volatility estimate using only bars up to and including
    `as_of_index` - never bars after it. This is the lookahead-safe
    entrypoint later phases (theoretical_options.py, backtesting) should
    use rather than slicing realized_volatility()'s full-series output
    themselves, where an off-by-one would leak future data into a
    historical decision."""
    if as_of_index < 0 or as_of_index >= len(closes):
        raise IndexError(f"as_of_index {as_of_index} out of range for series of length {len(closes)}")
    history = closes.iloc[: as_of_index + 1]
    if len(history) < window + 1:
        return None
    series = realized_volatility(history, window, bars_per_year)
    value = series.iloc[-1]
    return None if pd.isna(value) else float(value)
