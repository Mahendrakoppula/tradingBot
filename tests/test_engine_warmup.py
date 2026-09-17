import datetime as dt

from trading_bot.engine.candles import CandleStore
from trading_bot.engine.warmup import (
    MAX_DAYS,
    Instrument,
    WarmupPlan,
    backfill_today_1m,
    fetch_history,
    warm_up,
)
from trading_bot.rest_client import ApiError
from trading_bot.timeutil import IST

NIFTY = Instrument("NIFTY", "NSE", "99926000")
STEP = {"ONE_MINUTE": 1, "FIVE_MINUTE": 5, "THIRTY_MINUTE": 30}


class FakeRest:
    """Serves synthetic bars for every trading minute in [fromdate, todate)."""

    def __init__(self, fail_intervals=()):
        self.calls = []
        self.fail_intervals = set(fail_intervals)

    def get_candle_data(self, exchange, token, interval, fromdate, todate):
        self.calls.append((exchange, token, interval, fromdate, todate))
        if interval in self.fail_intervals:
            raise ApiError("Something Went Wrong", "AB2001")
        a = dt.datetime.strptime(fromdate, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        b = dt.datetime.strptime(todate, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        rows = []
        if interval == "ONE_DAY":
            d = a.date()
            while d <= b.date():
                if d.weekday() < 5:
                    rows.append([f"{d.isoformat()}T00:00:00+05:30", 100, 101, 99, 100.5, 0])
                d += dt.timedelta(days=1)
            return rows
        step = STEP[interval]
        t = a
        while t < b:
            if t.weekday() < 5 and dt.time(9, 15) <= t.time() < dt.time(15, 30):
                rows.append([t.isoformat(), 100, 101, 99, 100.5, 10])
            t += dt.timedelta(minutes=step)
        return rows


def test_warm_up_fills_all_stores_and_drops_forming_bar():
    rest = FakeRest()
    stores = {}
    now = dt.datetime(2026, 9, 16, 8, 0, tzinfo=IST)  # pre-market warmup
    plan = WarmupPlan({"1m": 2, "5m": 2, "30m": 2, "1d": 10})
    rep = warm_up(rest, [NIFTY], plan, stores, now)
    assert rep.errors == [] and rep.calls == 4
    assert set(stores) == {("99926000", tf) for tf in ("1m", "5m", "30m", "1d")}
    one = stores[("99926000", "1m")]
    assert len(one) > 0 and all(c.complete for c in one) and one.last.ts.date() < now.date()
    # nothing after `now`
    assert all(c.ts < now for s in stores.values() for c in s)
    # the last 1m bar of the previous session is 15:29
    assert one.last.ts.time() == dt.time(15, 29)


def test_warm_up_mid_session_excludes_the_forming_bar():
    rest = FakeRest()
    stores = {}
    now = dt.datetime(2026, 9, 16, 10, 2, 30, tzinfo=IST)
    warm_up(rest, [NIFTY], WarmupPlan({"1m": 1, "5m": 1}), stores, now)
    assert stores[("99926000", "1m")].last.ts.time() == dt.time(10, 1)  # 10:02 still forming
    assert stores[("99926000", "5m")].last.ts.time() == dt.time(9, 55)  # 10:00 bar closes 10:05


def test_chunking_respects_max_days_per_request():
    rest = FakeRest()
    start = dt.datetime(2026, 6, 1, 9, 15, tzinfo=IST)
    end = dt.datetime(2026, 9, 16, 8, 0, tzinfo=IST)  # 107 days of 1m -> 4 chunks of <=28
    candles = fetch_history(rest, NIFTY, "1m", start, end, limiter=None)
    assert len(rest.calls) == 4
    for _, _, _, a, b in rest.calls:
        d = (dt.datetime.strptime(b, "%Y-%m-%d %H:%M") - dt.datetime.strptime(a, "%Y-%m-%d %H:%M")).days
        assert d <= MAX_DAYS["1m"]
    # contiguous, de-duplicated, sorted
    ts = [c.ts for c in candles]
    assert ts == sorted(set(ts))


def test_one_failing_timeframe_does_not_abort_the_rest():
    rest = FakeRest(fail_intervals={"THIRTY_MINUTE"})
    stores = {}
    now = dt.datetime(2026, 9, 16, 8, 0, tzinfo=IST)
    rep = warm_up(rest, [NIFTY], WarmupPlan({"1m": 1, "30m": 1, "1d": 5}), stores, now, sleep=lambda s: None)
    assert [e[:2] for e in rep.errors] == [("NIFTY", "30m")]
    assert len(stores[("99926000", "1m")]) > 0 and len(stores[("99926000", "1d")]) > 0
    assert len(stores[("99926000", "30m")]) == 0


def test_backfill_today_merges_and_rest_wins():
    rest = FakeRest()
    store = CandleStore("1m")
    now = dt.datetime(2026, 9, 16, 9, 30, 10, tzinfo=IST)
    # a WS-built bar for 09:20 with a different close
    from trading_bot.engine.candles import Candle
    ws_bar = Candle(ts=dt.datetime(2026, 9, 16, 9, 20, tzinfo=IST), open=1, high=2, low=0.5, close=1.5,
                    complete=True, source="ws")
    store.upsert(ws_bar)
    n = backfill_today_1m(rest, NIFTY, store, now)
    assert n == 15  # 09:15 .. 09:29
    assert len(store) == 15 and store.gaps(now.date()) == []
    replaced = [c for c in store if c.ts == ws_bar.ts][0]
    assert replaced.source == "rest" and replaced.close == 100.5


def test_backfill_before_open_is_a_noop():
    rest = FakeRest()
    assert backfill_today_1m(rest, NIFTY, CandleStore("1m"), dt.datetime(2026, 9, 16, 8, 0, tzinfo=IST)) == 0
    assert rest.calls == []
