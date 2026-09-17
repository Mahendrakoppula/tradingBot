import datetime as dt

from trading_bot.engine.candles import Candle, CandleStore
from trading_bot.engine.quality import DataQuality, FeedHealth, assess, validate_candle
from trading_bot.timeutil import IST


def _t(h, m, s=0):
    return dt.datetime(2026, 9, 16, h, m, s, tzinfo=IST)


def _bar(h, m, **over):
    kw = dict(ts=_t(h, m), open=100.0, high=101.0, low=99.0, close=100.5, complete=True, tick_count=5)
    kw.update(over)
    return Candle(**kw)


def _store(*minutes):
    s = CandleStore("1m")
    for m in minutes:
        s.upsert(_bar(9, m))
    return s


def _feed(**over):
    kw = dict(connected=True, last_tick_at=_t(9, 20, 5))
    kw.update(over)
    return FeedHealth(**kw)


def test_ok_when_everything_is_fresh_and_contiguous():
    q = assess(_store(15, 16, 17, 18, 19), _feed(), now=_t(9, 20, 10))
    assert q == DataQuality("OK", ()) and q.ok


def test_disconnected_beats_everything():
    q = assess(_store(15, 17), _feed(connected=False, last_error="closed 1006"), now=_t(9, 20, 10))
    assert q.status == "DISCONNECTED" and "closed 1006" in q.reasons


def test_stale_when_no_tick_for_too_long_during_session():
    q = assess(_store(15, 16), _feed(last_tick_at=_t(9, 19, 30)), now=_t(9, 20, 10), stale_tick_seconds=15)
    assert q.status == "STALE" and q.reasons[0].startswith("last_tick_40s")
    # outside the session, silence is normal
    q2 = assess(_store(15, 16), _feed(last_tick_at=_t(9, 0)), now=_t(9, 10))
    assert q2.status == "OK"
    q3 = assess(_store(), _feed(last_tick_at=None), now=_t(9, 16))
    assert q3.status == "STALE" and q3.reasons == ("no_tick_yet",)


def test_gap_when_a_minute_is_missing():
    q = assess(_store(15, 16, 18), _feed(), now=_t(9, 20, 10))
    assert q.status == "GAP" and q.reasons == ("missing_1_1m_bars", "09:17")
    assert assess(_store(15, 16, 18), _feed(), now=_t(9, 20, 10), max_gap_bars=1).status == "OK"


def test_invalid_candle_detected():
    s = CandleStore("1m")
    s.upsert(_bar(9, 15, high=98.0))  # high < low
    q = assess(s, _feed(), now=_t(9, 20, 10))
    assert q.status == "INVALID" and q.reasons == ("high<low",)
    assert validate_candle(_bar(9, 15, close=0)) == "non_positive_price"
    assert validate_candle(_bar(9, 15, open=105.0)) == "open_close_outside_range"
    assert validate_candle(_bar(9, 15, tick_count=0)) == "zero_ticks"
    assert validate_candle(_bar(9, 15, tick_count=0, source="rest")) is None
    assert validate_candle(_bar(9, 15)) is None


def test_clock_drift_is_a_reason_not_a_downgrade():
    q = assess(_store(15, 16), _feed(clock_drift_seconds=7.5), now=_t(9, 20, 10))
    assert q.status == "OK" and q.reasons == ("clock_drift_7.5s",)
