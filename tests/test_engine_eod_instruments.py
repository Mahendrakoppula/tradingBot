import datetime as dt
import uuid

import pytest

from trading_bot.engine.db.memory import MemoryDAL
from trading_bot.engine.eod import eod_stats, eod_summary
from trading_bot.engine.instruments import near_month_future, resolve_instruments, ws_type
from trading_bot.engine.presignal import StageEvent
from trading_bot.timeutil import IST

T = dt.datetime(2026, 9, 16, 10, 0, tzinfo=IST)


def _dal_with_activity():
    dal = MemoryDAL()
    rid = uuid.UUID(int=7)
    dal.insert_run(rid, "SHADOW", T)
    for k, (frm, to) in enumerate([("NO_SETUP", "EARLY_DEVELOPMENT"), ("EARLY_DEVELOPMENT", "PRE_SIGNAL"),
                                   ("PRE_SIGNAL", "CONFIRMING"), ("CONFIRMING", "TRADE_READY"),
                                   ("NO_SETUP", "EARLY_DEVELOPMENT"), ("EARLY_DEVELOPMENT", "EXPIRED")]):
        dal.insert_presignal_event(rid, T + dt.timedelta(minutes=5 * k),
                                   StageEvent("s1" if k < 4 else "s2", "NIFTY", "up", frm, to, 0.5, "r", k))
    dal.insert_signal(rid, signal_id=uuid.UUID(int=9), setup_id="s1", ts=T, mode="SHADOW", underlying="NIFTY",
                      direction="up", stage="TRADE_READY", status="would_be",
                      explanation={"Regime": "BREAKOUT (transition COMPRESSION->BREAKOUT)"}, snapshot={})
    return dal, rid


def test_eod_stats_and_summary():
    dal, rid = _dal_with_activity()
    s = eod_stats(dal, rid)
    assert s["would_be_signals"] == 1 and s["setups_opened"] == 2 and s["setups_expired"] == 1
    assert s["stage_counts"]["TRADE_READY"] == 1 and s["signals_by_regime"] == {"BREAKOUT": 1}
    text = eod_summary(dal, rid, dt.date(2026, 9, 16), "SHADOW", feed={"ticks": 10})
    lines = text.splitlines()
    assert lines[0] == "EOD 2026-09-16 | SHADOW | zero orders"
    assert "would_be_signals=1" in lines[1] and "by regime: BREAKOUT=1" in text and "feed: ticks=10" in text


def test_eod_summary_for_quiet_day():
    dal = MemoryDAL()
    rid = uuid.UUID(int=3)
    dal.insert_run(rid, "SHADOW", T)
    text = eod_summary(dal, rid, dt.date(2026, 9, 16), "SHADOW")
    assert "would_be_signals=0" in text and "by regime" not in text


# --- instruments ---------------------------------------------------------------------

ROWS = [
    {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY", "instrumenttype": "AMXIDX", "exch_seg": "NSE", "expiry": ""},
    {"token": "99919000", "symbol": "SENSEX", "name": "SENSEX", "instrumenttype": "AMXIDX", "exch_seg": "BSE", "expiry": ""},
    {"token": "50001", "symbol": "NIFTY25SEP26FUT", "name": "NIFTY", "instrumenttype": "FUTIDX", "exch_seg": "NFO", "expiry": "25SEP2026"},
    {"token": "50002", "symbol": "NIFTY29OCT26FUT", "name": "NIFTY", "instrumenttype": "FUTIDX", "exch_seg": "NFO", "expiry": "29OCT2026"},
    {"token": "50000", "symbol": "NIFTY27AUG26FUT", "name": "NIFTY", "instrumenttype": "FUTIDX", "exch_seg": "NFO", "expiry": "27AUG2026"},
    {"token": "60001", "symbol": "SENSEX25SEP26FUT", "name": "SENSEX", "instrumenttype": "FUTIDX", "exch_seg": "BFO", "expiry": "25SEP2026"},
    {"token": "70001", "symbol": "NIFTY25SEP2625000CE", "name": "NIFTY", "instrumenttype": "OPTIDX", "exch_seg": "NFO", "expiry": "25SEP2026"},
]


def test_resolve_spot_and_near_month_future():
    today = dt.date(2026, 9, 16)
    insts = resolve_instruments(ROWS, ("NIFTY", "SENSEX"), today)
    assert [(i.underlying, i.exchange, i.token, i.role) for i in insts] == [
        ("NIFTY", "NSE", "99926000", "spot"), ("NIFTY", "NFO", "50001", "volume_proxy"),
        ("SENSEX", "BSE", "99919000", "spot"), ("SENSEX", "BFO", "60001", "volume_proxy"),
    ]
    # expired contract is skipped, next month chosen after expiry day
    assert near_month_future(ROWS, "NIFTY", "NFO", dt.date(2026, 9, 26))["token"] == "50002"
    assert near_month_future(ROWS, "NIFTY", "NFO", dt.date(2027, 1, 1)) is None
    assert ws_type("NSE") == "nse_cm" and ws_type("BFO") == "bse_fo"


def test_resolve_without_proxy_and_wrong_token_is_loud():
    insts = resolve_instruments(ROWS, ("NIFTY",), dt.date(2026, 9, 16), volume_proxy="none")
    assert [i.role for i in insts] == ["spot"]
    bad = [dict(r, token="1") if r["name"] == "NIFTY" and r["instrumenttype"] == "AMXIDX" else r for r in ROWS]
    with pytest.raises(RuntimeError):
        resolve_instruments(bad, ("NIFTY",), dt.date(2026, 9, 16))
