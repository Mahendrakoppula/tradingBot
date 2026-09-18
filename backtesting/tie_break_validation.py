"""Validates event_loop.py's own disclosed, never-tested assumption
(module docstring, unchanged since Run 001): "If both the stop and
target are breached within the same [daily] bar (a wide-range day), the
STOP is assumed to have been hit first." This has sat as a conservative,
unverified guess in every single run in this log - now finally
checkable, using the real intraday (FIVE_MINUTE) data Runs 011/012
brought into this project.

Method: a daily exit is only "convention-dependent" (the tie-break
assumption actually mattered) when exit_reason == "stop" AND that same
exit bar's daily high/low would ALSO have breached target -
_check_stop_target_hit()'s own stop-checked-first order means a
"target" exit_reason could only ever occur on an UNAMBIGUOUS bar (if
stop had also been breached that bar, the function would have returned
"stop" instead) - so every case where the tie-break rule was actually
invoked shows up as a "stop" exit whose bar ALSO breached target.

For each such case whose calendar date falls inside the intraday
data's available window, replays the real FIVE_MINUTE bars for that day
in chronological order to find which was actually breached FIRST
intrabar - the ground truth event_loop.py's daily-bar view cannot see.

HONEST LIMITATION, disclosed up front: FIVE_MINUTE data only covers a
recent, rolling ~90-day window (see backtesting/BACKTESTS.md's Run
011/012), not the 5-year history every other backtest in this log
draws from - this can only check whichever convention-dependent days
happen to fall inside that recent window, not validate the assumption
across the full historical run. A real, if partial, first look -
exactly the character of every other "first look" finding in this log.
"""
from dataclasses import dataclass

import pandas as pd

from backtesting.event_loop import BacktestResult
from backtesting.trade_record import Trade


@dataclass
class TieBreakCase:
    trade: Trade
    exit_date: object
    checked_intraday: bool  # False if this date falls outside the available intraday window
    actual_first_hit: str | None  # "stop" | "target" | None if checked_intraday is False


def _is_ambiguous_bar(trade: Trade, bar: pd.Series) -> bool:
    """True if this daily bar breaches BOTH stop and target - the exact
    condition under which event_loop.py's tie-break assumption was
    actually invoked (not just a bar that happens to touch one of
    them)."""
    if trade.direction == "CE":
        return bool(bar["low"] <= trade.stop_price and bar["high"] >= trade.target_price)
    return bool(bar["high"] >= trade.stop_price and bar["low"] <= trade.target_price)


def find_convention_dependent_exits(result: BacktestResult, daily_df: pd.DataFrame) -> list[Trade]:
    """Every closed trade whose exit_reason=="stop" AND whose exit bar
    ALSO breached target that same day - the tie-break assumption
    changed the outcome for exactly these trades, and no others."""
    dependent = []
    for trade in result.trades:
        if trade.exit_reason != "stop" or trade.exit_index is None:
            continue
        bar = daily_df.iloc[trade.exit_index]
        if _is_ambiguous_bar(trade, bar):
            dependent.append(trade)
    return dependent


def _first_hit_intraday(trade: Trade, intraday_bars: pd.DataFrame) -> str | None:
    """Scans intraday bars for this ONE calendar day in chronological
    order, returning whichever of stop/target is breached first, or
    None if the day's intraday bars (which can have slightly different
    high/low extremes than the daily bar, due to data-source
    differences) don't actually confirm either breach - reported as
    genuinely inconclusive rather than guessed."""
    for _, bar in intraday_bars.iterrows():
        hit = _check_stop_target_hit_bar(trade, bar)
        if hit is not None:
            return hit
    return None


def _check_stop_target_hit_bar(trade: Trade, bar: pd.Series) -> str | None:
    """Same breach logic as event_loop._check_stop_target_hit, but
    deliberately re-implemented here (not imported) so this validation
    module has no dependency on the very function it exists to
    scrutinize - an independent check, not a circular one."""
    if trade.direction == "CE":
        stop_hit = bar["low"] <= trade.stop_price
        target_hit = bar["high"] >= trade.target_price
    else:
        stop_hit = bar["high"] >= trade.stop_price
        target_hit = bar["low"] <= trade.target_price
    if stop_hit and target_hit:
        return "stop"  # both breached within this single intraday bar too - genuinely still ambiguous even intraday
    if stop_hit:
        return "stop"
    if target_hit:
        return "target"
    return None


def validate_tie_breaks(
    dependent_trades: list[Trade], intraday_df: pd.DataFrame
) -> list[TieBreakCase]:
    """For each convention-dependent trade, checks the real intraday
    bars for its exit date (if available) to see what actually happened
    first. intraday_df must have a "timestamp" column at intraday
    granularity (e.g. FIVE_MINUTE)."""
    intraday_dates = intraday_df["timestamp"].dt.date
    cases = []
    for trade in dependent_trades:
        exit_date = trade.exit_timestamp.date() if hasattr(trade.exit_timestamp, "date") else trade.exit_timestamp
        day_bars = intraday_df[intraday_dates == exit_date]
        if day_bars.empty:
            cases.append(TieBreakCase(trade=trade, exit_date=exit_date, checked_intraday=False, actual_first_hit=None))
            continue
        actual = _first_hit_intraday(trade, day_bars)
        cases.append(TieBreakCase(trade=trade, exit_date=exit_date, checked_intraday=True, actual_first_hit=actual))
    return cases
