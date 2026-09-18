import datetime as dt
import importlib.util
import json
from pathlib import Path

from trading_bot.engine.candles import Candle
from trading_bot.engine.option_select import SelectParams
from trading_bot.engine.pipeline import PipelineParams
from trading_bot.engine.research.backtest import (
    DEFAULT_SPECS,
    BacktestReport,
    CandleSource,
    ModelChainParams,
    ModelChainService,
    run_backtest,
)
from trading_bot.engine.risk_engine import RiskLimits
from trading_bot.engine.warmup import Instrument
from trading_bot.timeutil import IST

_spec = importlib.util.spec_from_file_location("replay_t", Path(__file__).resolve().parent / "test_engine_replay.py")
replay_t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(replay_t)

SPOT_INST = Instrument("NIFTY", "NSE", "99926000", "spot")


def _data(days_hist=8, days_test=3, seed=100) -> tuple[dict, dt.date, dt.date]:
    hist, px = replay_t._history(days_hist, seed, 0)
    last = hist["1d"][-1].ts.date()
    day = last + dt.timedelta(days=1)
    sessions = []
    while len(sessions) < days_test:
        if day.weekday() < 5:
            s = replay_t._session(day, px, seed + 1000 + len(sessions), 0)
            sessions.append(s)
            px = s[-1].close
        day += dt.timedelta(days=1)
    one_m = hist["1m"] + [c for s in sessions for c in s]
    data = {SPOT_INST.token: {"1m": one_m, "5m": replay_t._resample(one_m, "5m", 5), "30m": replay_t._resample(one_m, "30m", 30),
                              "1d": hist["1d"] + [Candle(ts=s[0].ts, open=s[0].open, high=max(c.high for c in s), low=min(c.low for c in s),
                                                          close=s[-1].close, complete=True, source="replay") for s in sessions]}}
    return data, sessions[0][0].ts.date(), sessions[-1][0].ts.date()


def _cfg():
    return replay_t._cfg(mode="BACKTEST", capital=100000.0, risk_per_trade_pct=0.01)


def _pipeline():
    return PipelineParams(limits=RiskLimits(capital=100000.0, risk_per_trade_pct=0.01), select=SelectParams(min_premium=5.0))


def test_model_chain_prices_from_spot_only():
    svc = ModelChainService({"NIFTY": DEFAULT_SPECS["NIFTY"]}, dt.date(2026, 9, 16), ModelChainParams(strikes_each_side=3, expiries=2))
    now = dt.datetime(2026, 9, 16, 10, 0, tzinfo=IST)
    c = svc.refresh("NIFTY", 25012.0, now)
    assert len(c.quotes) == 2 * 2 * 7 and c.fresh(now)
    q = next(q for q in c.quotes.values() if q.contract.strike == 25000 and q.contract.option_type == "CE" and q.contract.expiry == dt.date(2026, 9, 22))
    assert q.greeks_source == "model" and 0.45 < q.delta < 0.65 and q.spread_pct <= 1.05 and q.oi == 50000
    assert q.contract.expiry.weekday() == 1 and q.contract.lotsize == 75
    # deterministic and spot-only
    c2 = ModelChainService({"NIFTY": DEFAULT_SPECS["NIFTY"]}, dt.date(2026, 9, 16), ModelChainParams(strikes_each_side=3, expiries=2)).refresh("NIFTY", 25012.0, now)
    assert {k: v.as_dict() for k, v in c.quotes.items()} == {k: v.as_dict() for k, v in c2.quotes.items()}


def test_backtest_runs_days_and_reports():
    data, start, end = _data()
    rep = run_backtest(_cfg(), [SPOT_INST], CandleSource(data=data), start, end, pipeline=_pipeline(), history_days=30)
    assert isinstance(rep, BacktestReport) and len(rep.days) == 3
    assert all(d.snapshots == 75 for d in rep.days)
    assert sum(d.decisions for d in rep.days) == len(rep.signals)
    assert rep.rejections["total"] + sum(d.approved for d in rep.days) >= sum(d.decisions for d in rep.days) - len(rep.trade_rows)
    assert rep.metrics.trades == len(rep.trade_rows)
    text = rep.summary()
    assert text.startswith("backtest") and "days=3" in text and "rejections:" in text
    # a run id per day, all statuses legal
    assert len({d.run_id for d in rep.days}) == 3
    assert all(s["status"] in ("valid", "rejected", "expired", "invalidated", "risk_rejected", "option_rejected") for s in rep.signals)


def _journal(rep: BacktestReport, upto_day: dt.date) -> str:
    sig = [s for s in rep.signals if s["ts"].date() <= upto_day]
    tr = [t for t in rep.trade_rows if t["exit_ts"].date() <= upto_day]
    return json.dumps([sig, tr, [d.__dict__ for d in rep.days if d.day <= upto_day]], sort_keys=True, default=str)


def test_backtest_is_deterministic_and_has_no_lookahead():
    data, start, end = _data()
    a = run_backtest(_cfg(), [SPOT_INST], CandleSource(data=data), start, end, pipeline=_pipeline(), history_days=30)
    b = run_backtest(_cfg(), [SPOT_INST], CandleSource(data=data), start, end, pipeline=_pipeline(), history_days=30)
    assert _journal(a, end) == _journal(b, end)
    # mutate the LAST day's candles: nothing before it may change (§84)
    mutated = {SPOT_INST.token: {tf: list(cs) for tf, cs in data[SPOT_INST.token].items()}}
    for tf in ("1m", "5m", "30m", "1d"):
        mutated[SPOT_INST.token][tf] = [
            Candle(ts=c.ts, open=c.open + 300, high=c.high + 300, low=c.low + 300, close=c.close + 300, volume=c.volume,
                   complete=True, source="replay") if c.ts.date() == end else c for c in mutated[SPOT_INST.token][tf]]
    c = run_backtest(_cfg(), [SPOT_INST], CandleSource(data=mutated), start, end, pipeline=_pipeline(), history_days=30)
    penultimate = a.days[-2].day
    assert _journal(a, penultimate) == _journal(c, penultimate)
    assert _journal(a, end) != _journal(c, end)


def test_backtest_skips_non_trading_days_and_carries_account():
    data, start, end = _data(days_test=2)
    rep = run_backtest(_cfg(), [SPOT_INST], CandleSource(data=data), start, end + dt.timedelta(days=3),
                       pipeline=_pipeline(), history_days=30, holidays=(start.isoformat(),))
    assert [d.day for d in rep.days] == [end]  # the first test day is a declared holiday; the padding days have no bars
