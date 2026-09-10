import datetime as dt

from data.historical import _chunk_ranges, fetch_history, last_completed_trading_date


def test_chunk_ranges_splits_by_max_days():
    chunks = list(_chunk_ranges(dt.date(2026, 1, 1), dt.date(2026, 1, 10), max_days=3))
    assert chunks == [
        (dt.date(2026, 1, 1), dt.date(2026, 1, 3)),
        (dt.date(2026, 1, 4), dt.date(2026, 1, 6)),
        (dt.date(2026, 1, 7), dt.date(2026, 1, 9)),
        (dt.date(2026, 1, 10), dt.date(2026, 1, 10)),
    ]


def test_chunk_ranges_single_chunk_when_range_within_max_days():
    chunks = list(_chunk_ranges(dt.date(2026, 1, 1), dt.date(2026, 1, 2), max_days=30))
    assert chunks == [(dt.date(2026, 1, 1), dt.date(2026, 1, 2))]


def test_last_completed_trading_date_before_market_open_is_yesterday():
    now = dt.datetime(2026, 3, 5, 8, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    assert last_completed_trading_date(now) == dt.date(2026, 3, 4)


def test_last_completed_trading_date_after_market_open_is_today():
    now = dt.datetime(2026, 3, 5, 9, 30, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    assert last_completed_trading_date(now) == dt.date(2026, 3, 5)


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_candle_data(self, exchange, symboltoken, interval, fromdate, todate):
        self.calls.append((exchange, symboltoken, interval, fromdate, todate))
        return self.responses.pop(0)


def test_fetch_history_flattens_chunks_into_row_dicts(monkeypatch):
    monkeypatch.setattr("data.historical.time.sleep", lambda *_: None)
    client = _FakeClient([
        [["2026-01-01T09:15:00+05:30", 100, 101, 99, 100.5, 1000]],
        [["2026-01-02T09:15:00+05:30", 100.5, 102, 100, 101, 1200]],
    ])
    rows = fetch_history(client, "NSE", "99926000", "ONE_DAY", dt.date(2026, 1, 1), dt.date(2026, 1, 2))
    # ONE_DAY has a 2000-day max chunk, so this 2-day range is one chunk,
    # but two responses is fine - simulates the client being called once.
    assert len(client.calls) == 1
    assert rows == [
        {"timestamp": "2026-01-01T09:15:00+05:30", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
    ]


def test_fetch_history_rejects_unknown_interval():
    import pytest
    with pytest.raises(ValueError):
        fetch_history(_FakeClient([]), "NSE", "99926000", "NOT_AN_INTERVAL", dt.date(2026, 1, 1), dt.date(2026, 1, 2))


def test_fetch_history_continues_past_a_chunk_that_exhausts_retries(monkeypatch):
    monkeypatch.setattr("data.historical.time.sleep", lambda *_: None)
    monkeypatch.setattr("data.historical.MAX_RETRIES_PER_CHUNK", 1)

    class _AlwaysFailsClient:
        def get_candle_data(self, *a, **k):
            raise ValueError("simulated rate-limit text response")

    rows = fetch_history(_AlwaysFailsClient(), "NSE", "99926000", "ONE_DAY", dt.date(2026, 1, 1), dt.date(2026, 1, 2))
    assert rows == []
