"""§51 parity / determinism: the same 1m candles replayed twice through the
SAME ShadowLoop (MemoryDAL, SimClock) must produce byte-identical journals,
and the bars the aggregator rebuilds from replay ticks must equal the input.
"""
import datetime as dt
import json
import random
from dataclasses import replace

from trading_bot.engine.analysis import EngineParams
from trading_bot.engine.candles import Candle, CandleStore
from trading_bot.engine.config import EngineConfig
from trading_bot.engine.db.memory import MemoryDAL
from trading_bot.engine.presignal import PreSignalConfig
from trading_bot.engine.replay import SimClock, TickReplaySource, candles_to_ticks
from trading_bot.engine.shadow import ShadowLoop
from trading_bot.engine.warmup import Instrument
from trading_bot.timeutil import IST

SPOT = Instrument("NIFTY", "NSE", "99926000", "spot")
FUT = Instrument("NIFTY", "NFO", "12345", "volume_proxy")
DAY = dt.date(2026, 9, 16)


def _cfg(**over) -> EngineConfig:
    base = dict(
        mode="BACKTEST", dry_run=True, live_trading_enabled=False, database_url="", underlyings=("NIFTY",),
        capital=50000.0, risk_per_trade_pct=0.005, daily_loss_cap_pct=0.02, weekly_loss_cap_pct=0.05,
        eod_cutoff=dt.time(15, 20), session_end=dt.time(15, 30), eod_summary_time=dt.time(15, 35),
        warmup_days_1m=7, warmup_days_5m=21, warmup_days_30m=90, warmup_days_1d=400, stale_tick_seconds=15,
        feed_backoff_max_seconds=60, clock_drift_seconds=5, volume_proxy="futures",
        align_w_daily=0.25, align_w_30m=0.30, align_w_5m=0.30, align_w_1m=0.15,
        trend_adx_min=20.0, trend_adx_strong=25.0, regime_atr_pct_high=80.0, regime_atr_pct_low=20.0,
        bb_compression_pct=20.0, presignal_ttl_bars=6, presignal_decay=0.85, presignal_min_conf=0.35,
        level_proximity_atr=0.5, telegram_max_alerts_per_hour=12, holidays=(),
    )
    base.update(over)
    return EngineConfig(**base)


def _session(day: dt.date, start_px: float, seed: int, vol: int) -> list[Candle]:
    """Synthetic 1m session: compression in the morning, a breakout, then a
    grind - enough shape for the pre-signal engine to have something to say."""
    rnd = random.Random(seed)
    out, px = [], start_px
    t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    for k in range(375):
        drift = 0.0 if k < 120 else (1.2 if k < 180 else 0.2)
        wob = 2.0 if k < 120 else 4.0
        o = px
        px = round(px + drift + rnd.uniform(-wob, wob), 2)
        hi = round(max(o, px) + rnd.uniform(0, 2), 2)
        lo = round(min(o, px) - rnd.uniform(0, 2), 2)
        out.append(Candle(ts=t, open=o, high=hi, low=lo, close=px, volume=(rnd.randint(vol // 2, vol) if vol else 0),
                          tick_count=4, complete=True, source="replay"))
        t += dt.timedelta(minutes=1)
    return out


def _resample(one_m: list[Candle], tf: str, minutes: int) -> list[Candle]:
    out = []
    for k in range(0, len(one_m), minutes):
        ch = one_m[k:k + minutes]
        out.append(Candle(ts=ch[0].ts, open=ch[0].open, high=max(c.high for c in ch), low=min(c.low for c in ch),
                          close=ch[-1].close, volume=sum(c.volume for c in ch), complete=True, source="replay"))
    return out


def _history(days: int, seed: int, vol: int) -> tuple[dict[str, list[Candle]], float]:
    day, px, sessions = dt.date(2026, 8, 3), 25000.0, []
    while len(sessions) < days:
        if day.weekday() < 5:
            s = _session(day, px, seed + len(sessions), vol)
            sessions.append(s)
            px = s[-1].close
        day += dt.timedelta(days=1)
    one_m = [c for s in sessions for c in s]
    daily = [Candle(ts=s[0].ts, open=s[0].open, high=max(c.high for c in s), low=min(c.low for c in s), close=s[-1].close,
                    volume=sum(c.volume for c in s), complete=True, source="replay") for s in sessions]
    return {"1m": one_m, "5m": _resample(one_m, "5m", 5), "30m": _resample(one_m, "30m", 30), "1d": daily}, px


def _stores(hist: dict[str, list[Candle]], token: str) -> dict:
    stores = {}
    for tf, cs in hist.items():
        st = CandleStore(tf)
        st.extend(cs)
        stores[(token, tf)] = st
    return stores


class _Notes:
    def __init__(self):
        self.msgs = []

    def notify(self, message, *, html=False):
        self.msgs.append(message)


_CACHE: dict = {}


def _run(fresh: bool = False):
    """One full replayed session. Cached (the journal is read-only for the
    tests) except when `fresh` - the determinism test needs two real runs."""
    if not fresh and "run" in _CACHE:
        return _CACHE["run"]
    out = _run_uncached()
    if not fresh:
        _CACHE["run"] = out
    return out


def _run_uncached():
    spot_hist, px = _history(12, 100, 0)
    fut_hist, fpx = _history(12, 200, 4000)
    stores = {**_stores(spot_hist, SPOT.token), **_stores(fut_hist, FUT.token)}
    today_spot = _session(DAY, px, 999, 0)
    today_fut = _session(DAY, fpx, 998, 4000)
    clock = SimClock(dt.datetime.combine(DAY, dt.time(9, 0), tzinfo=IST))
    src = TickReplaySource({SPOT.token: today_spot, FUT.token: today_fut}, clock, DAY)
    dal = MemoryDAL()
    notes = _Notes()
    import uuid
    loop = ShadowLoop(_cfg(), dal, [SPOT, FUT], src, clock.now, notes, stores,
                      run_id=uuid.UUID(int=1), git_sha="test")
    stats = loop.run()
    return loop, dal, notes, stats, today_spot, today_fut


def _journal(dal: MemoryDAL) -> str:
    def norm(rows):
        return json.dumps(rows, sort_keys=True, default=str)
    return "\n".join([
        norm(dal.context_snapshots), norm(dal.presignal_events), norm(dal.signals),
        norm(sorted(({**r, "ts": r["ts"].isoformat()} for r in dal.candles.values()), key=lambda r: (r["token"], r["tf"], r["ts"]))),
    ])


def test_replay_rebuilds_input_bars_exactly():
    loop, dal, notes, stats, today_spot, today_fut = _run()
    rebuilt = dal.load_candles(SPOT.token, "1m", today_spot[0].ts)
    assert len(rebuilt) == 375
    for a, b in zip(rebuilt, today_spot):
        assert (a.ts, a.open, a.high, a.low, a.close) == (b.ts, b.open, b.high, b.low, b.close)
        assert a.tick_count == 4 and a.source == "ws"
    fut = dal.load_candles(FUT.token, "1m", today_fut[0].ts)
    assert [c.volume for c in fut] == [c.volume for c in today_fut]
    five = dal.load_candles(SPOT.token, "5m", today_spot[0].ts)
    assert len(five) == 75 and five[-1].ts.time() == dt.time(15, 25)
    thirty = dal.load_candles(SPOT.token, "30m", today_spot[0].ts)
    assert len(thirty) == 13 and thirty[-1].ts.time() == dt.time(15, 15)
    daily = dal.load_candles(SPOT.token, "1d", today_spot[0].ts)
    assert len(daily) == 1 and daily[0].high == max(c.high for c in today_spot)


def test_loop_journals_every_five_minute_close_and_ends_run():
    loop, dal, notes, stats, *_ = _run()
    assert stats.snapshots == 75 == dal.context_count(loop.run_id)
    assert stats.ticks == 375 * 4 * 2 and stats.bars_closed > 0
    run = dal.runs[str(loop.run_id)]
    assert run["status"] == "completed" and run["ended_at"] is not None and run["mode"] == "BACKTEST"
    assert run["config_version_id"] == 1
    # the EOD summary went out and says zero orders
    assert notes.msgs and notes.msgs[-1].startswith("EOD 2026-09-16 | BACKTEST | zero orders")
    # every snapshot carries the full engine read
    snap = dal.context_snapshots[-1]
    assert snap["regime"]["primary"] and snap["alignment"]["label"] and snap["indicators"]["atr"] is not None
    assert snap["volume"]["volume_proxy"] == "futures"
    # a would-be signal, if any, is fully explained and linked
    for s in dal.signals:
        assert s["status"] == "would_be" and s["mode"] == "BACKTEST" and s["context_snapshot_id"] >= 1
        assert len(s["explanation"]) == 20 and s["snapshot"]["option"] is None
        assert s["option_type"] in ("CE", "PE")


def test_two_replays_are_byte_identical():
    a = _journal(_run(fresh=True)[1])
    b = _journal(_run(fresh=True)[1])
    assert a == b
    assert len(a) > 10000


def test_presignal_activity_is_observed_on_the_synthetic_breakout():
    loop, dal, *_ = _run()
    assert len(dal.presignal_events) > 0
    stages = {e["to_stage"] for e in dal.presignal_events}
    assert "EARLY_DEVELOPMENT" in stages
    assert all(e["context_snapshot_id"] >= 1 for e in dal.presignal_events)


def test_alert_rate_limit_applies_per_hour():
    spot_hist, px = _history(5, 1, 0)
    stores = _stores(spot_hist, SPOT.token)
    clock = SimClock(dt.datetime.combine(DAY, dt.time(9, 0), tzinfo=IST))
    src = TickReplaySource({SPOT.token: []}, clock, DAY)
    notes = _Notes()
    loop = ShadowLoop(_cfg(telegram_max_alerts_per_hour=2), MemoryDAL(), [SPOT], src, clock.now, notes, stores)
    t = dt.datetime.combine(DAY, dt.time(10, 0), tzinfo=IST)
    for k in range(4):
        loop._alert(f"m{k}", t + dt.timedelta(minutes=k))
    assert notes.msgs == ["m0", "m1"] and loop.stats.alerts_suppressed == 2
    loop._alert("later", t + dt.timedelta(hours=1, minutes=1))
    assert notes.msgs[-1] == "later"


def test_candles_to_ticks_shape():
    c = Candle(ts=dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST), open=100.0, high=101.0, low=99.0, close=100.5,
               volume=400, complete=True)
    ticks = candles_to_ticks("t", [c, replace(c, ts=c.ts + dt.timedelta(minutes=1), volume=100)])
    assert [t.ltp for t in ticks[:4]] == [10000, 10100, 9900, 10050]
    assert [t.volume for t in ticks] == [100, 200, 300, 400, 425, 450, 475, 500]
    assert ticks[3].exchange_timestamp - ticks[0].exchange_timestamp == 59_000
