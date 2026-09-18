import datetime as dt

import pandas as pd
import pytest

from backtesting.event_loop import BacktestResult
from backtesting.tie_break_validation import (
    find_convention_dependent_exits,
    validate_tie_breaks,
)
from backtesting.trade_record import Trade


def _daily_df(rows: list[dict]) -> pd.DataFrame:
    ts = pd.bdate_range("2026-01-01", periods=len(rows))
    return pd.DataFrame({
        "timestamp": ts,
        "open": [r.get("open", 100) for r in rows],
        "high": [r["high"] for r in rows],
        "low": [r["low"] for r in rows],
        "close": [r.get("close", 100) for r in rows],
        "volume": [0] * len(rows),
    })


def _trade(direction: str, exit_index: int, exit_timestamp: dt.datetime, stop: float, target: float, exit_reason: str) -> Trade:
    return Trade(
        strategy_name="t", direction=direction, entry_index=0, entry_timestamp=dt.datetime(2026, 1, 1),
        entry_spot=100.0, entry_premium=5.0, strike=100.0, expiry=dt.date(2026, 1, 8),
        stop_price=stop, target_price=target, exit_index=exit_index, exit_timestamp=exit_timestamp,
        exit_spot=100.0, exit_premium=1.0, exit_reason=exit_reason, pnl=-4.0,
    )


def test_find_convention_dependent_exits_includes_ambiguous_stop_exit():
    df = _daily_df([{"high": 105, "low": 95}, {"high": 115, "low": 85}])  # bar 1: breaches both a 90 stop and 110 target
    trade = _trade("CE", exit_index=1, exit_timestamp=dt.datetime(2026, 1, 2), stop=90, target=110, exit_reason="stop")
    result = BacktestResult(trades=[trade])
    dependent = find_convention_dependent_exits(result, df)
    assert dependent == [trade]


def test_find_convention_dependent_exits_excludes_unambiguous_stop_exit():
    df = _daily_df([{"high": 105, "low": 95}, {"high": 108, "low": 85}])  # bar 1: breaches stop (90) but NOT target (110)
    trade = _trade("CE", exit_index=1, exit_timestamp=dt.datetime(2026, 1, 2), stop=90, target=110, exit_reason="stop")
    result = BacktestResult(trades=[trade])
    dependent = find_convention_dependent_exits(result, df)
    assert dependent == []


def test_find_convention_dependent_exits_excludes_target_exits_by_construction():
    """A "target" exit_reason can only occur on an unambiguous bar -
    event_loop.py's own stop-checked-first order guarantees this - so
    this function should never need to (and never does) inspect them."""
    df = _daily_df([{"high": 105, "low": 95}, {"high": 115, "low": 98}])
    trade = _trade("CE", exit_index=1, exit_timestamp=dt.datetime(2026, 1, 2), stop=90, target=110, exit_reason="target")
    result = BacktestResult(trades=[trade])
    dependent = find_convention_dependent_exits(result, df)
    assert dependent == []


def _intraday_df(date: dt.date, bars: list[dict]) -> pd.DataFrame:
    ts = [dt.datetime.combine(date, dt.time(9, 15)) + dt.timedelta(minutes=5 * i) for i in range(len(bars))]
    return pd.DataFrame({
        "timestamp": ts,
        "open": [b.get("open", 100) for b in bars],
        "high": [b["high"] for b in bars],
        "low": [b["low"] for b in bars],
        "close": [b.get("close", 100) for b in bars],
        "volume": [0] * len(bars),
    })


def test_validate_tie_breaks_marks_uncheckable_date_when_no_intraday_coverage():
    trade = _trade("CE", exit_index=1, exit_timestamp=dt.datetime(2026, 1, 2), stop=90, target=110, exit_reason="stop")
    intraday = _intraday_df(dt.date(2026, 1, 5), [{"high": 101, "low": 99}])  # a different date entirely
    cases = validate_tie_breaks([trade], intraday)
    assert len(cases) == 1
    assert cases[0].checked_intraday is False
    assert cases[0].actual_first_hit is None


def test_validate_tie_breaks_detects_target_actually_hit_first():
    """The real point of this module: an early intraday bar breaches
    ONLY target, a later one breaches stop - the daily-bar convention
    (stop assumed first) would have been WRONG for this trade."""
    trade = _trade("CE", exit_index=1, exit_timestamp=dt.datetime(2026, 1, 2), stop=90, target=110, exit_reason="stop")
    intraday = _intraday_df(dt.date(2026, 1, 2), [
        {"high": 111, "low": 99},   # target (110) breached first, stop (90) not yet
        {"high": 105, "low": 89},   # stop breached later
    ])
    cases = validate_tie_breaks([trade], intraday)
    assert cases[0].checked_intraday is True
    assert cases[0].actual_first_hit == "target"


def test_validate_tie_breaks_confirms_stop_actually_hit_first():
    trade = _trade("CE", exit_index=1, exit_timestamp=dt.datetime(2026, 1, 2), stop=90, target=110, exit_reason="stop")
    intraday = _intraday_df(dt.date(2026, 1, 2), [
        {"high": 105, "low": 89},   # stop breached first
        {"high": 111, "low": 95},   # target breached later
    ])
    cases = validate_tie_breaks([trade], intraday)
    assert cases[0].checked_intraday is True
    assert cases[0].actual_first_hit == "stop"


def test_validate_tie_breaks_pe_direction():
    trade = _trade("PE", exit_index=1, exit_timestamp=dt.datetime(2026, 1, 2), stop=110, target=90, exit_reason="stop")
    intraday = _intraday_df(dt.date(2026, 1, 2), [
        {"high": 101, "low": 89},   # target (90) breached first for PE (low <= target), stop (110, high>=110) not yet
        {"high": 112, "low": 95},   # stop breached later
    ])
    cases = validate_tie_breaks([trade], intraday)
    assert cases[0].actual_first_hit == "target"
