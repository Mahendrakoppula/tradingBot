import datetime as dt

from research.framework.mtf import aligned_view, resample_daily, resample_to_minutes


def _daily(date, o, h, l, c):
    ts = dt.datetime.combine(date, dt.time(0, 0))
    return {"ts": ts, "date": date, "open": o, "high": h, "low": l, "close": c, "volume": 0}


def _minute(ts, o, h, l, c, v=100):
    return {
        "ts": ts, "date": ts.date(), "time": ts.time(),
        "open": o, "high": h, "low": l, "close": c, "volume": v,
    }


def test_resample_daily_to_weekly_groups_by_iso_week():
    # Mon 2026-01-05 .. Sun 2026-01-11 is one ISO week; Mon 2026-01-12 starts the next.
    candles = [
        _daily(dt.date(2026, 1, 5), 100, 105, 99, 102),
        _daily(dt.date(2026, 1, 6), 102, 106, 101, 104),
        _daily(dt.date(2026, 1, 12), 104, 108, 103, 107),
    ]
    weekly = resample_daily(candles, "week")
    assert len(weekly) == 2
    assert weekly[0]["open"] == 100
    assert weekly[0]["high"] == 106
    assert weekly[0]["low"] == 99
    assert weekly[0]["close"] == 104
    assert weekly[1]["open"] == 104


def test_resample_daily_to_monthly_groups_by_month():
    candles = [
        _daily(dt.date(2026, 1, 30), 100, 101, 99, 100),
        _daily(dt.date(2026, 2, 2), 100, 102, 98, 101),
    ]
    monthly = resample_daily(candles, "month")
    assert len(monthly) == 2


def test_resample_daily_invalid_unit_raises():
    try:
        resample_daily([], "day")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_resample_to_minutes_delegates_to_backtest_technical_resample():
    base = dt.datetime(2026, 1, 5, 9, 15)
    candles = [_minute(base + dt.timedelta(minutes=i), 100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)]
    bars = resample_to_minutes(candles, 5)
    assert len(bars) == 2
    assert bars[0]["open"] == candles[0]["open"]
    assert bars[0]["close"] == candles[4]["close"]


def test_aligned_view_never_uses_the_final_or_still_forming_htf_bar():
    htf = [
        _daily(dt.date(2026, 1, 1), 1, 2, 0, 1),
        _daily(dt.date(2026, 1, 2), 1, 2, 0, 1),
        _daily(dt.date(2026, 1, 3), 1, 2, 0, 1),
    ]
    # LTF timestamps chosen well after every HTF bar's own start, including the last.
    ltf = [
        _minute(dt.datetime(2026, 1, 1, 12, 0), 1, 2, 0, 1),
        _minute(dt.datetime(2026, 1, 2, 12, 0), 1, 2, 0, 1),
        _minute(dt.datetime(2026, 1, 3, 12, 0), 1, 2, 0, 1),
        _minute(dt.datetime(2026, 1, 5, 12, 0), 1, 2, 0, 1),  # well after all 3 htf starts
    ]
    ctx = aligned_view(htf, ltf)
    # First LTF candle (same day as htf[0], before htf[1] exists as proof) -> no confirmed bar yet.
    assert ctx[0].htf_index is None
    # Even the very last LTF candle (long after htf[2] started) must never
    # select htf[2] itself - there's no bar after it to prove closure.
    assert ctx[-1].htf_index == 1
    assert ctx[-1].htf_candle is htf[1]
