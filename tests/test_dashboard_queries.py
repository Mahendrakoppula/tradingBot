"""requires_real_data tests need data/raw/ pulled (see
tests/test_feature_engineering.py's module docstring for why)."""
import datetime as dt

import pytest

from dashboard.queries import INSTRUMENTS, INTERVALS, current_market_state, data_health_report, regime_history
from data.storage import load_ohlcv

NIFTY_DAILY = load_ohlcv("NIFTY", "ONE_DAY")
requires_real_data = pytest.mark.skipif(len(NIFTY_DAILY) == 0, reason="real NIFTY daily data not pulled locally")


def test_data_health_report_covers_every_instrument_and_interval():
    rows = data_health_report()
    assert len(rows) == len(INSTRUMENTS) * len(INTERVALS)
    pairs = {(r.instrument, r.interval) for r in rows}
    assert pairs == {(i, iv) for i in INSTRUMENTS for iv in INTERVALS}


def test_data_health_report_handles_missing_data_without_crashing():
    rows = data_health_report()
    # whatever data exists or not locally, every row must be well-formed
    for r in rows:
        if r.n_rows == 0:
            assert r.last_timestamp is None
            assert r.days_since_last is None
        else:
            assert r.last_timestamp is not None
            assert r.days_since_last is not None


@requires_real_data
def test_data_health_report_days_since_last_is_computed_correctly():
    as_of = dt.date(2026, 9, 20)
    rows = data_health_report(as_of=as_of)
    nifty_daily_row = next(r for r in rows if r.instrument == "NIFTY" and r.interval == "ONE_DAY")
    last_date = NIFTY_DAILY["timestamp"].iloc[-1].date()
    assert nifty_daily_row.days_since_last == (as_of - last_date).days


@requires_real_data
def test_current_market_state_returns_a_real_state_for_pulled_data():
    state = current_market_state("NIFTY", "ONE_DAY")
    assert state is not None
    assert state.regime in ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE", "UNKNOWN")


def test_current_market_state_returns_none_for_unpulled_instrument():
    state = current_market_state("NOT_A_REAL_INSTRUMENT", "ONE_DAY")
    assert state is None


@requires_real_data
def test_regime_history_has_one_row_per_bar():
    history = regime_history("NIFTY", "ONE_DAY")
    assert len(history) == len(NIFTY_DAILY)
    assert list(history.columns) == ["timestamp", "regime"]


def test_regime_history_empty_for_unpulled_instrument():
    history = regime_history("NOT_A_REAL_INSTRUMENT", "ONE_DAY")
    assert len(history) == 0
