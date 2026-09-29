"""Historical backfill + range replay: the tooling that turns stored candles
into a backtestable sample."""
import datetime as dt

import pytest

from trading_bot.backfill_history import backfill
from trading_bot.replay_range import replay_days, trading_days
from trading_bot.engine.warmup import Instrument


def test_trading_days_skips_weekends_and_holidays():
    days = trading_days(dt.date(2026, 9, 21), dt.date(2026, 9, 30))
    assert days[0] == dt.date(2026, 9, 21) and days[-1] == dt.date(2026, 9, 30)
    assert dt.date(2026, 9, 26) not in days and dt.date(2026, 9, 27) not in days  # Sat/Sun
    assert len(days) == 8
    with_holiday = trading_days(dt.date(2026, 9, 21), dt.date(2026, 9, 25), holidays=("2026-09-23",))
    assert dt.date(2026, 9, 23) not in with_holiday and len(with_holiday) == 4


class _Dal:
    def __init__(self):
        self.rows = []

    def upsert_candles(self, token, exchange, underlying, tf, candles):
        self.rows.append((underlying, tf, len(candles)))
        return len(candles)


def test_backfill_covers_every_instrument_timeframe_and_survives_one_failure(monkeypatch):
    import trading_bot.backfill_history as bf
    calls = []

    def fake_fetch(rest, inst, tf, start, end, limiter, report=None, sleep=None):
        calls.append((inst.underlying, tf, start.date(), end.date()))
        if inst.underlying == "SENSEX" and tf == "1m":
            raise RuntimeError("HTTP_500")
        return [object()] * 10

    monkeypatch.setattr(bf, "fetch_history", fake_fetch)
    dal = _Dal()
    insts = [Instrument("NIFTY", "NSE", "99926000", "spot"), Instrument("SENSEX", "BSE", "99919000", "spot")]
    written = backfill(None, dal, insts, dt.date(2026, 6, 1), dt.date(2026, 9, 29), ("1m", "5m"), None)
    assert written[("NIFTY", "1m")] == 10 and written[("NIFTY", "5m")] == 10
    assert written[("SENSEX", "1m")] == -1          # the failure is recorded, not raised
    assert written[("SENSEX", "5m")] == 10          # and the rest still runs
    assert len(calls) == 4
    assert calls[0][2] == dt.date(2026, 6, 1) and calls[0][3] == dt.date(2026, 9, 29)


def test_replay_days_counts_outcomes_and_never_aborts_the_range(monkeypatch):
    seen = []

    def run_one(day):
        seen.append(day)
        if day.day == 22:
            return 2          # no stored candles
        if day.day == 23:
            raise RuntimeError("bad day")
        return 0

    days = [dt.date(2026, 9, d) for d in (21, 22, 23, 24)]
    stats = replay_days(days, run_one)
    assert seen == days                                   # every day attempted
    assert stats == {"days": 4, "ok": 2, "skipped": 1, "failed": 1}


def test_replay_sets_the_date_each_iteration(monkeypatch):
    import os
    seen = []
    replay_days([dt.date(2026, 9, 21), dt.date(2026, 9, 22)],
                lambda d: seen.append(os.environ["TECH_REPLAY_DATE"]) or 0)
    assert seen == ["2026-09-21", "2026-09-22"]
