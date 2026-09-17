import datetime as dt

from trading_bot.engine.candles import BarAggregator, Candle, CandleStore
from trading_bot.timeutil import IST

DAY = dt.date(2026, 9, 16)


def _ms(h, m, s=0, ms=0) -> int:
    t = dt.datetime(DAY.year, DAY.month, DAY.day, h, m, s, ms * 1000, tzinfo=IST)
    return int(t.timestamp() * 1000)


def _t(h, m, s=0) -> dt.datetime:
    return dt.datetime(DAY.year, DAY.month, DAY.day, h, m, s, tzinfo=IST)


def _agg(tfs=("1m", "5m")):
    closed = []
    agg = BarAggregator("99926000", tfs, on_close=lambda tf, c: closed.append((tf, c)))
    return agg, closed


# --- aggregator ----------------------------------------------------------------


def test_ticks_build_ohlc_in_rupees_and_close_on_boundary():
    agg, closed = _agg(("1m",))
    agg.on_tick(2500000, _ms(9, 15, 1))
    agg.on_tick(2500500, _ms(9, 15, 20))
    agg.on_tick(2499000, _ms(9, 15, 40))
    agg.on_tick(2500200, _ms(9, 15, 59))
    assert closed == []
    cur = agg.current("1m")
    assert (cur.open, cur.high, cur.low, cur.close) == (25000.0, 25005.0, 24990.0, 25002.0)
    assert cur.tick_count == 4 and cur.complete is False
    # the first tick of 09:16 closes the 09:15 bar
    out = agg.on_tick(2500300, _ms(9, 16, 0, 500))
    assert [tf for tf, _ in out] == ["1m"] and closed == out
    bar = out[0][1]
    assert bar.ts == _t(9, 15) and bar.complete is True and bar.close == 25002.0
    assert bar.max_gap_ms == 20000
    assert agg.current("1m").ts == _t(9, 16) and agg.current("1m").open == 25003.0


def test_five_minute_bar_built_from_ticks_not_from_1m():
    agg, closed = _agg(("1m", "5m"))
    for minute in range(15, 20):
        agg.on_tick(2500000 + minute * 100, _ms(9, minute, 10))
    assert len([c for c in closed if c[0] == "1m"]) == 4 and not [c for c in closed if c[0] == "5m"]
    agg.on_tick(2600000, _ms(9, 20, 0))
    five = [c for tf, c in closed if tf == "5m"]
    assert len(five) == 1
    assert five[0].ts == _t(9, 15) and five[0].tick_count == 5
    assert five[0].open == 25015.0 and five[0].close == 25019.0


def test_late_tick_is_counted_not_applied():
    agg, closed = _agg(("1m",))
    agg.on_tick(2500000, _ms(9, 15, 30))
    agg.on_tick(2500100, _ms(9, 16, 5))
    assert len(closed) == 1
    agg.on_tick(1000000, _ms(9, 15, 59))  # arrives after the 09:15 bar closed
    assert agg.late_ticks == 1
    assert closed[0][1].low == 25000.0 and agg.current("1m").low == 25001.0


def test_out_of_session_ticks_are_ignored():
    agg, closed = _agg(("1m",))
    agg.on_tick(2400000, _ms(9, 7, 0))   # pre-open auction print
    agg.on_tick(2500000, _ms(9, 15, 0))
    agg.on_tick(2600000, _ms(15, 30, 1))  # post-close
    assert agg.out_of_session_ticks == 2
    assert agg.current("1m").open == 25000.0 and agg.current("1m").tick_count == 1


def test_flush_timers_closes_quiet_bar_after_grace():
    agg, closed = _agg(("1m",))
    agg.on_tick(2500000, _ms(9, 15, 10))
    assert agg.flush_timers(_t(9, 16, 1)) == []  # boundary passed but inside the 2s grace
    out = agg.flush_timers(_t(9, 16, 3))
    assert [tf for tf, _ in out] == ["1m"] and out[0][1].complete and agg.current("1m") is None
    # next tick opens a fresh bar; the gap bar 09:16 simply doesn't exist
    agg.on_tick(2500000, _ms(9, 17, 0))
    assert agg.current("1m").ts == _t(9, 17)


def test_last_bar_of_day_closes_at_1530_and_flush_all_marks_partial():
    agg, closed = _agg(("30m",))
    agg.on_tick(2500000, _ms(15, 16, 0))
    assert agg.current("30m").ts == _t(15, 15)
    assert agg.flush_timers(_t(15, 29, 59)) == []
    out = agg.flush_timers(_t(15, 30, 3))  # 15:15 stub bar closes at 15:30, not 15:45
    assert out and out[0][1].complete
    agg.on_tick(2500000, _ms(15, 29, 0))  # a late print re-opens nothing new... it's late
    assert agg.late_ticks == 1
    agg2, _ = _agg(("1m",))
    agg2.on_tick(2500000, _ms(10, 0, 0))
    partial = agg2.flush_all()
    assert partial[0][1].complete is False and agg2.current("1m") is None


def test_volume_from_cumulative_and_oi():
    agg, closed = _agg(("1m",))
    agg.on_tick(2500000, _ms(9, 15, 1), cum_volume=1000, oi=500)
    agg.on_tick(2500000, _ms(9, 15, 30), cum_volume=1600, oi=520)
    agg.on_tick(2500000, _ms(9, 16, 1), cum_volume=1900)
    bar0 = closed[0][1]
    assert bar0.volume == 1600 and bar0.oi == 520  # session's first bar owns the day-cumulative so far
    assert agg.current("1m").volume == 300  # 1900 - 1600 carried from the previous bar
    # mid-session start: the first bar seen must NOT inherit the day's cumulative
    agg2, closed2 = _agg(("1m",))
    agg2.on_tick(2500000, _ms(11, 0, 1), cum_volume=500000)
    agg2.on_tick(2500000, _ms(11, 0, 30), cum_volume=500400)
    agg2.on_tick(2500000, _ms(11, 1, 1), cum_volume=500900)
    assert closed2[0][1].volume == 400 and agg2.current("1m").volume == 500


# --- store -------------------------------------------------------------------------


def _c(h, m, tf="1m", close=100.0, complete=True):
    return Candle(ts=_t(h, m), open=close, high=close + 1, low=close - 1, close=close, complete=complete)


def test_store_orders_dedups_and_bounds():
    s = CandleStore("1m", max_len=3)
    assert s.upsert(_c(9, 15)) and s.upsert(_c(9, 17)) and s.upsert(_c(9, 16))
    assert [c.ts.minute for c in s] == [15, 16, 17]
    assert s.upsert(_c(9, 16, close=200.0)) is False  # replaced in place
    assert [c.close for c in s][1] == 200.0
    s.upsert(_c(9, 18))
    assert len(s) == 3 and s.last.ts.minute == 18 and [c.ts.minute for c in s][0] == 16
    try:
        s.upsert(_c(9, 19, complete=False))
        assert False
    except ValueError:
        pass


def test_store_gaps_and_dicts():
    s = CandleStore("1m")
    for m in (15, 16, 18, 20):
        s.upsert(_c(9, m))
    assert [g.minute for g in s.gaps(DAY)] == [17, 19]
    assert s.gaps(dt.date(2026, 9, 15)) == []
    d = s.dicts()[0]
    assert set(d) == {"ts", "open", "high", "low", "close", "volume", "oi"} and d["ts"] == _t(9, 15)
    assert len(s.since(_t(9, 18))) == 2 and len(s.today(DAY)) == 4


def test_candle_from_rest_row():
    c = Candle.from_rest(["2026-09-16T09:20:00+05:30", 25000.5, 25010, 24990, 25005, 12345], "5m")
    assert c.ts == _t(9, 20) and c.complete and c.source == "rest" and c.volume == 12345
    daily = Candle.from_rest(["2026-09-16T00:00:00+05:30", 1, 2, 0.5, 1.5, 0], "1d")
    assert daily.ts == _t(9, 15)
