import datetime as dt

import pytest

from trading_bot.engine.clock import (
    bar_start,
    in_session,
    is_trading_day,
    next_boundary,
    session_phase,
)
from trading_bot.timeutil import IST

D = dt.date(2026, 9, 16)  # a Wednesday


def _t(h, m, s=0):
    return dt.datetime(D.year, D.month, D.day, h, m, s, tzinfo=IST)


@pytest.mark.parametrize("ts,tf,expected", [
    (_t(9, 15), "1m", _t(9, 15)),
    (_t(9, 15, 59), "1m", _t(9, 15)),
    (_t(9, 16), "1m", _t(9, 16)),
    (_t(9, 19, 30), "5m", _t(9, 15)),
    (_t(9, 20), "5m", _t(9, 20)),
    (_t(10, 3), "5m", _t(10, 0)),
    (_t(9, 44, 59), "30m", _t(9, 15)),
    (_t(9, 45), "30m", _t(9, 45)),
    (_t(15, 20), "30m", _t(15, 15)),  # the 15:15-15:30 stub, matching SmartAPI
    (_t(12, 0), "1d", _t(9, 15)),
])
def test_bar_start_is_anchored_at_0915(ts, tf, expected):
    assert bar_start(ts, tf) == expected


def test_pre_open_and_post_close_ticks_clamp_to_first_and_last_bar():
    assert bar_start(_t(9, 0), "5m") == _t(9, 15)
    assert bar_start(_t(15, 45), "5m") == _t(15, 25)
    assert bar_start(_t(15, 45), "30m") == _t(15, 15)


@pytest.mark.parametrize("ts,tf,expected", [
    (_t(9, 17), "1m", _t(9, 18)),
    (_t(9, 17), "5m", _t(9, 20)),
    (_t(9, 17), "30m", _t(9, 45)),
    (_t(15, 20), "30m", _t(15, 30)),  # stub bar closes at session close, not 15:45
    (_t(15, 27), "5m", _t(15, 30)),
    (_t(11, 0), "1d", _t(15, 30)),
])
def test_next_boundary(ts, tf, expected):
    assert next_boundary(ts, tf) == expected


def test_bar_start_requires_aware_datetime():
    with pytest.raises(ValueError):
        bar_start(dt.datetime(2026, 9, 16, 10, 0), "5m")


def test_utc_input_is_converted_to_ist():
    utc = dt.datetime(2026, 9, 16, 4, 32, tzinfo=dt.timezone.utc)  # 10:02 IST
    assert bar_start(utc, "5m") == _t(10, 0)


def test_is_trading_day():
    assert is_trading_day(D) is True
    assert is_trading_day(dt.date(2026, 9, 19)) is False  # Saturday
    assert is_trading_day(D, holidays=("2026-09-16",)) is False


@pytest.mark.parametrize("t,expected", [
    (dt.time(9, 0), "PRE_MARKET"),
    (dt.time(9, 15), "09:15-10:00"),
    (dt.time(9, 59, 59), "09:15-10:00"),
    (dt.time(10, 0), "10:00-11:30"),
    (dt.time(13, 30), "13:00-14:00"),
    (dt.time(15, 10), "15:00-15:30"),
    (dt.time(15, 30), "POST_MARKET"),
])
def test_session_phase(t, expected):
    assert session_phase(t) == expected


def test_in_session_bounds():
    assert in_session(dt.time(9, 15)) is True
    assert in_session(dt.time(15, 29, 59)) is True
    assert in_session(dt.time(15, 30)) is False
    assert in_session(dt.time(9, 14)) is False
