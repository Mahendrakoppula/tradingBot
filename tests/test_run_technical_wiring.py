"""run_technical.run_live end to end with every external edge faked:
login, scrip master, REST candles, WebSocket, Telegram, clock. Proves the
entrypoint composes warmup -> feed -> loop -> EOD and journals a run."""
import datetime as dt
import random

from trading_bot import run_technical as rt
from trading_bot.engine.config import EngineConfig
from trading_bot.timeutil import IST

DAY = dt.date(2026, 9, 16)  # a Wednesday


def _cfg(**over) -> EngineConfig:
    base = dict(
        mode="SHADOW", dry_run=True, live_trading_enabled=False, database_url="", underlyings=("NIFTY",),
        capital=50000.0, risk_per_trade_pct=0.005, daily_loss_cap_pct=0.02, weekly_loss_cap_pct=0.05,
        eod_cutoff=dt.time(15, 20), session_end=dt.time(15, 30), eod_summary_time=dt.time(15, 35),
        warmup_days_1m=2, warmup_days_5m=2, warmup_days_30m=2, warmup_days_1d=5, stale_tick_seconds=15,
        feed_backoff_max_seconds=60, clock_drift_seconds=5, volume_proxy="none",
        align_w_daily=0.25, align_w_30m=0.30, align_w_5m=0.30, align_w_1m=0.15,
        trend_adx_min=20.0, trend_adx_strong=25.0, regime_atr_pct_high=80.0, regime_atr_pct_low=20.0,
        bb_compression_pct=20.0, presignal_ttl_bars=6, presignal_decay=0.85, presignal_min_conf=0.35,
        level_proximity_atr=0.5, telegram_max_alerts_per_hour=12, holidays=(),
    )
    base.update(over)
    return EngineConfig(**base)


class FakeSession:
    def __init__(self, cfg):
        self.cfg = cfg
        self.logged_in = False

    def login(self):
        self.logged_in = True


class FakeRest:
    def __init__(self, session):
        self.calls = 0

    def get_candle_data(self, exchange, token, interval, fromdate, todate):
        self.calls += 1
        a = dt.datetime.strptime(fromdate, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        b = dt.datetime.strptime(todate, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        step = {"ONE_MINUTE": 1, "FIVE_MINUTE": 5, "THIRTY_MINUTE": 30, "ONE_DAY": 1440}[interval]
        rnd = random.Random(1)
        rows, t = [], a
        while t < b:
            if t.weekday() < 5 and (interval == "ONE_DAY" or dt.time(9, 15) <= t.time() < dt.time(15, 30)):
                px = 25000 + rnd.uniform(-50, 50)
                rows.append([t.isoformat(), px, px + 5, px - 5, px + 1, 0])
            t += dt.timedelta(minutes=step)
        return rows


class FakeLookup:
    def __init__(self, url):
        self.instruments = []
        self._by_symbol = {}

    def load(self):
        self.instruments = [{"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY", "instrumenttype": "AMXIDX",
                             "exch_seg": "NSE", "expiry": ""}]


class _Tick:
    def __init__(self, token, ltp, ms, volume=None):
        self.token, self.ltp, self.exchange_timestamp, self.volume, self.open_interest = token, ltp, ms, volume, None


class FakeSource:
    """A scripted feed: 30 minutes of ticks, then silence; the fake clock
    then runs past the close so the loop ends on its own."""

    def __init__(self, stream, q):
        self.stream = stream
        self.started = self.closed = False
        t = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
        self.ticks = [_Tick("99926000", 2500000 + k * 100, int((t + dt.timedelta(seconds=30 * k)).timestamp() * 1000))
                      for k in range(60)]
        self.last = None

    def start(self):
        self.started = True

    def next(self, timeout=1.0):
        if not self.ticks:
            CLOCK["now"] = dt.datetime.combine(DAY, dt.time(15, 30, 10), tzinfo=IST)
            return None
        t = self.ticks.pop(0)
        CLOCK["now"] = dt.datetime.fromtimestamp(t.exchange_timestamp / 1000, tz=IST)
        self.last = CLOCK["now"]
        return t

    def done(self):
        return False

    def health(self):
        from trading_bot.engine.quality import FeedHealth
        return FeedHealth(connected=True, last_tick_at=self.last or CLOCK["now"], reconnects=0)

    def close(self):
        self.closed = True


CLOCK = {"now": dt.datetime.combine(DAY, dt.time(8, 0), tzinfo=IST)}
NOTES: list[str] = []


def test_run_live_wires_everything(monkeypatch):
    monkeypatch.setattr(rt, "Session", FakeSession)
    monkeypatch.setattr(rt, "RestClient", FakeRest)
    monkeypatch.setattr(rt, "InstrumentLookup", FakeLookup)
    monkeypatch.setattr(rt, "ResilientMarketStream", lambda *a, **k: object())
    monkeypatch.setattr(rt, "smartapi_stream_factory", lambda session: None)
    monkeypatch.setattr(rt, "LiveTickSource", FakeSource)
    monkeypatch.setattr(rt, "now_ist", lambda: CLOCK["now"])
    monkeypatch.setattr(rt, "today_ist", lambda: DAY)
    monkeypatch.setattr(rt.technical_notifier, "notify", lambda m, html=False: NOTES.append(m))
    monkeypatch.setattr(rt, "_git_sha", lambda: "abc1234")
    monkeypatch.setattr(rt.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(rt, "broker_config", lambda cfg: type("B", (), {"dry_run": True, "scrip_master_url": "x"})())

    dals = []
    orig = rt._open_dal

    def _dal(cfg):
        d = orig(cfg)
        dals.append(d)
        return d

    monkeypatch.setattr(rt, "_open_dal", _dal)

    rc = rt.run_live(_cfg())
    assert rc == 0
    dal = dals[0]
    run = next(iter(dal.runs.values()))
    assert run["status"] == "completed" and run["mode"] == "SHADOW" and run["git_sha"] == "abc1234"
    # warmup history + today's bars landed in the journal
    assert any(k[1] == "1d" for k in dal.candles) and any(k[1] == "1m" and k[2].date() == DAY for k in dal.candles)
    # 30 minutes of ticks -> six 5m closes -> six snapshots
    assert dal.context_count(run["run_id"]) == 6
    # banner + EOD went to Telegram, and the banner says orders are disabled
    assert NOTES[0].startswith("SHADOW MODE | LIVE ORDER PLACEMENT DISABLED")
    assert NOTES[-1].startswith("EOD 2026-09-16 | SHADOW | zero orders")


def test_non_trading_day_exits_cleanly(monkeypatch):
    monkeypatch.setattr(rt, "today_ist", lambda: dt.date(2026, 9, 19))  # Saturday
    assert rt.run_live(_cfg()) == 0
    monkeypatch.setattr(rt, "today_ist", lambda: DAY)
    assert rt.run_live(_cfg(holidays=(DAY.isoformat(),))) == 0


def test_main_refuses_when_invariant_broken(monkeypatch):
    monkeypatch.setattr(rt.EngineConfig, "from_env", classmethod(lambda cls: _cfg()))
    monkeypatch.setattr(rt, "broker_config", lambda cfg: type("B", (), {"dry_run": False})())
    assert rt.main() == 2


def test_backfill_failure_degrades_to_a_gap_not_a_crash(monkeypatch):
    class BrokenRest(FakeRest):
        def get_candle_data(self, exchange, token, interval, fromdate, todate):
            a = dt.datetime.strptime(fromdate, "%Y-%m-%d %H:%M")
            if a.date() == DAY and interval == "ONE_MINUTE":
                from trading_bot.rest_client import ApiError
                raise ApiError("Access denied because of exceeding access rate", "HTTP_403")
            return super().get_candle_data(exchange, token, interval, fromdate, todate)

    monkeypatch.setattr(rt, "Session", FakeSession)
    monkeypatch.setattr(rt, "RestClient", BrokenRest)
    monkeypatch.setattr(rt, "InstrumentLookup", FakeLookup)
    monkeypatch.setattr(rt, "ResilientMarketStream", lambda *a, **k: object())
    monkeypatch.setattr(rt, "smartapi_stream_factory", lambda session: None)
    monkeypatch.setattr(rt, "LiveTickSource", FakeSource)
    monkeypatch.setattr(rt, "today_ist", lambda: DAY)
    monkeypatch.setattr(rt.technical_notifier, "notify", lambda m, html=False: None)
    monkeypatch.setattr(rt, "_git_sha", lambda: None)
    monkeypatch.setattr(rt.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(rt, "broker_config", lambda cfg: type("B", (), {"dry_run": True, "scrip_master_url": "x"})())
    import trading_bot.engine.ratelimit as rl
    monkeypatch.setattr(rl.time, "sleep", lambda s: None)  # paced_call retry backoff
    # start mid-session so the backfill actually runs
    CLOCK["now"] = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
    monkeypatch.setattr(rt, "now_ist", lambda: CLOCK["now"])
    dals = []
    orig = rt._open_dal
    monkeypatch.setattr(rt, "_open_dal", lambda cfg: dals.append(orig(cfg)) or dals[-1])
    assert rt.run_live(_cfg()) == 0
    run = next(iter(dals[0].runs.values()))
    assert run["status"] == "completed"  # the session ran to its end despite the failed backfill
