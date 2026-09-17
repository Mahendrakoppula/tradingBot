import datetime as dt
import json
import math
import random

from trading_bot.engine.analysis import AnalysisState, EngineParams, build_context
from trading_bot.engine.context import ContextSnapshot
from trading_bot.timeutil import IST

W = {"1d": 0.25, "30m": 0.30, "5m": 0.30, "1m": 0.15}
PARAMS = EngineParams(align_weights=W)


def _session_1m(day: dt.date, start_px: float, drift: float, seed: int, vol: int = 0) -> list[dict]:
    """A synthetic 1m session with a random walk plus drift."""
    rnd = random.Random(seed)
    out, px = [], start_px
    t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    for _ in range(375):
        o = px
        px += drift + rnd.uniform(-4, 4)
        hi, lo = max(o, px) + rnd.uniform(0, 3), min(o, px) - rnd.uniform(0, 3)
        out.append({"ts": t, "open": o, "high": hi, "low": lo, "close": px, "volume": vol and rnd.randint(vol // 2, vol)})
        t += dt.timedelta(minutes=1)
    return out


def _resample(one_m: list[dict], minutes: int) -> list[dict]:
    out = []
    for k in range(0, len(one_m), minutes):
        chunk = one_m[k:k + minutes]
        out.append({"ts": chunk[0]["ts"], "open": chunk[0]["open"], "high": max(c["high"] for c in chunk),
                    "low": min(c["low"] for c in chunk), "close": chunk[-1]["close"],
                    "volume": sum(c["volume"] for c in chunk)})
    return out


def _daily(one_m_days: list[list[dict]]) -> list[dict]:
    return [{"ts": d[0]["ts"], "open": d[0]["open"], "high": max(c["high"] for c in d), "low": min(c["low"] for c in d),
             "close": d[-1]["close"], "volume": sum(c["volume"] for c in d)} for d in one_m_days]


def _history(days: int = 30, drift: float = 0.3, seed: int = 1, vol: int = 0):
    """Returns candles dict for the spot (or proxy) up to and including the
    last day's full session."""
    day = dt.date(2026, 8, 1)
    sessions, px = [], 25000.0
    while len(sessions) < days:
        if day.weekday() < 5:
            s = _session_1m(day, px, drift, seed + len(sessions), vol)
            sessions.append(s)
            px = s[-1]["close"]
        day += dt.timedelta(days=1)
    one_m = [c for s in sessions for c in s]
    return {"1m": one_m, "5m": _resample(one_m, 5), "30m": _resample(one_m, 30), "1d": _daily(sessions)}


def _cut(candles: dict, upto: dt.datetime) -> dict:
    """Closed bars only, as of `upto` (bar start + length <= upto)."""
    mins = {"1m": 1, "5m": 5, "30m": 30, "1d": 375}
    return {tf: [c for c in cs if c["ts"] + dt.timedelta(minutes=mins[tf]) <= upto] for tf, cs in candles.items()}


def test_build_context_full_shape_on_synthetic_session():
    hist = _history(days=30, drift=0.3)
    proxy = _history(days=30, drift=0.3, seed=99, vol=5000)
    last_day = hist["1d"][-1]["ts"].date()
    now = dt.datetime.combine(last_day, dt.time(11, 0), tzinfo=IST)
    st = AnalysisState()
    ctx = build_context("NIFTY", _cut(hist, now), now=now, quality="OK", state=st, params=PARAMS,
                        volume_candles=_cut(proxy, now))
    assert isinstance(ctx, ContextSnapshot)
    assert ctx.underlying == "NIFTY" and ctx.trigger_tf == "5m" and ctx.session_phase and ctx.quality == "OK"
    assert set(ctx.trends) == {"1d", "30m", "5m", "1m"}
    for tf, t in ctx.trends.items():
        assert t["label"] and -1 <= t["score"] <= 1
    assert ctx.alignment["label"] and ctx.alignment["direction_preference"] in ("up", "down", "none")
    assert ctx.regime["primary"] and ctx.regime["candidate"]
    ind = ctx.indicators
    for k in ("rsi", "macd_hist", "adx", "atr", "ema20", "ema50", "ema200", "bb_width_pct", "atr_percentile"):
        assert ind[k] is not None, k
    assert ind["vwap"] is not None and "vwap_basis" in ctx.volume
    assert ctx.volume["volume_proxy"] == "futures" and ctx.volume["relative_volume"] is not None
    lv = ctx.levels
    for k in ("pdh", "pdl", "pdc", "pwh", "pwl", "session_high", "session_low", "or_high", "or_low"):
        assert k in lv, k
    assert lv["nearest_above"]["price"] > ctx.spot and lv["nearest_below"]["price"] < ctx.spot
    assert set(lv["nearest_above"]) == {"name", "price", "distance", "distance_atr", "touches"}
    assert "labels" in ctx.price_action and "anatomy" in ctx.price_action
    assert "last_event" in ctx.structure and "in_swing_range" in ctx.structure
    # serialisable
    json.loads(ctx.to_json())


def test_state_carries_persistence_and_hysteresis():
    hist = _history(days=30, drift=0.5)
    last_day = hist["1d"][-1]["ts"].date()
    st = AnalysisState()
    t1 = dt.datetime.combine(last_day, dt.time(10, 0), tzinfo=IST)
    t2 = t1 + dt.timedelta(minutes=5)
    c1 = build_context("NIFTY", _cut(hist, t1), now=t1, quality="OK", state=st, params=PARAMS)
    c2 = build_context("NIFTY", _cut(hist, t2), now=t2, quality="OK", state=st, params=PARAMS)
    assert c2.trends["5m"]["persistence_bars"] >= c1.trends["5m"]["persistence_bars"]
    assert st.prev_regime is not None and st.recent_labels["5m"][-2:] == [c1.trends["5m"]["label"], c2.trends["5m"]["label"]]
    assert c1.volume["volume_proxy"] == "none" and c1.indicators["vwap"] is None


def test_bad_quality_forces_no_trade_regime():
    hist = _history(days=30)
    last_day = hist["1d"][-1]["ts"].date()
    now = dt.datetime.combine(last_day, dt.time(10, 0), tzinfo=IST)
    ctx = build_context("NIFTY", _cut(hist, now), now=now, quality="STALE", state=AnalysisState(), params=PARAMS)
    assert ctx.regime["primary"] == "NO_TRADE"


def test_deterministic_for_identical_input():
    hist = _history(days=30, drift=-0.4, seed=7)
    last_day = hist["1d"][-1]["ts"].date()
    now = dt.datetime.combine(last_day, dt.time(12, 30), tzinfo=IST)
    a = build_context("BANKNIFTY", _cut(hist, now), now=now, quality="OK", state=AnalysisState(), params=PARAMS)
    b = build_context("BANKNIFTY", _cut(hist, now), now=now, quality="OK", state=AnalysisState(), params=PARAMS)
    assert a.to_json() == b.to_json()


def test_short_history_degrades_not_crashes():
    hist = _history(days=2)
    last_day = hist["1d"][-1]["ts"].date()
    now = dt.datetime.combine(last_day, dt.time(9, 25), tzinfo=IST)
    ctx = build_context("SENSEX", _cut(hist, now), now=now, quality="OK", state=AnalysisState(), params=PARAMS)
    assert ctx.indicators["ema200"] is None or math.isfinite(ctx.indicators["ema200"])
    assert ctx.trends["5m"]["label"] in ("NEUTRAL", "BULL", "BEAR", "WEAK_BULL", "WEAK_BEAR", "STRONG_BULL", "STRONG_BEAR",
                                         "COUNTER_TREND", "UNSTABLE", "TREND_TRANSITION")
    assert ctx.levels.get("or_high") is None  # opening range not complete at 09:25
