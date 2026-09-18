"""DAL contract tests. Always run against MemoryDAL; also against a real
PostgreSQL when TECH_TEST_DATABASE_URL is set (CI provides postgres:16).
The two must behave identically - that is the point of the shared table.
"""
import datetime as dt
import os
import uuid

import pytest

from trading_bot.engine.candles import Candle
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.db.common import config_hash, plain
from trading_bot.engine.db.memory import MemoryDAL
from trading_bot.engine.db.schema_v1 import MIGRATIONS
from trading_bot.engine.presignal import StageEvent
from trading_bot.timeutil import IST

PG_URL = os.environ.get("TECH_TEST_DATABASE_URL")


def _dals():
    yield "memory", MemoryDAL
    if PG_URL:
        from trading_bot.engine.db.dal import Database
        yield "postgres", lambda: Database(PG_URL)


def _open(factory):
    dal = factory().connect()
    if PG_URL and dal.__class__.__name__ == "Database":
        # fresh schema per test run: drop everything the migration creates
        dal.conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    dal.migrate()
    return dal


def _t(h, m):
    return dt.datetime(2026, 9, 16, h, m, tzinfo=IST)


def _ctx(bar=3):
    return ContextSnapshot(ts=_t(9, 30), underlying="NIFTY", trigger_tf="5m", spot=25012.0, session_phase="open_drive",
                           quality="OK", bar_index=bar, trends={"5m": {"label": "BULL", "score": 0.5}},
                           regime={"primary": "BULL"}, indicators={"atr": 20.0, "rsi": 61.2},
                           structure={"keys": {"b", "a"}, "day": dt.date(2026, 9, 16)})


@pytest.mark.parametrize("name,factory", list(_dals()))
def test_run_lifecycle_and_config_version(name, factory):
    dal = _open(factory)
    try:
        cid = dal.get_or_create_config_version({"risk": 0.005, "underlyings": ["NIFTY"]})
        assert dal.get_or_create_config_version({"underlyings": ["NIFTY"], "risk": 0.005}) == cid  # key order irrelevant
        assert dal.get_or_create_config_version({"risk": 0.01}) != cid
        rid = uuid.uuid4()
        assert dal.insert_run(rid, "SHADOW", _t(8, 0), git_sha="abc", host="ec2", config_version_id=cid) == str(rid)
        dal.end_run(rid, _t(15, 35), status="completed", notes={"snapshots": 74})
        if name == "postgres":
            row = dal.conn.execute("SELECT * FROM runs WHERE run_id = %s", (str(rid),)).fetchone()
        else:
            row = dal.runs[str(rid)]
        assert row["status"] == "completed" and row["notes"]["snapshots"] == 74 and row["config_version_id"] == cid
    finally:
        dal.close()


@pytest.mark.parametrize("name,factory", list(_dals()))
def test_candles_upsert_and_load(name, factory):
    dal = _open(factory)
    try:
        rid = uuid.uuid4()
        dal.insert_run(rid, "SHADOW", _t(8, 0))
        bars = [Candle(ts=_t(9, 15 + i), open=100 + i, high=101 + i, low=99 + i, close=100.5 + i, volume=10 * i,
                       tick_count=5, complete=True, source="ws") for i in range(5)]
        bars.append(Candle(ts=_t(9, 20), open=1, high=2, low=0.5, close=1.5, complete=False, source="ws"))
        assert dal.upsert_candles("99926000", "NSE", "NIFTY", "1m", bars, run_id=rid) == 6
        got = dal.load_candles("99926000", "1m", _t(9, 15))
        assert [c.ts.minute for c in got] == [15, 16, 17, 18, 19]  # complete only
        assert got[1].close == 101.5 and got[1].volume == 10 and got[0].source == "ws"
        assert len(dal.load_candles("99926000", "1m", _t(9, 15), complete_only=False)) == 6
        assert [c.ts.minute for c in dal.load_candles("99926000", "1m", _t(9, 16), until=_t(9, 18))] == [16, 17]
        # upsert replaces by (token, tf, ts)
        dal.upsert_candles("99926000", "NSE", "NIFTY", "1m", [Candle(ts=_t(9, 16), open=1, high=2, low=0.5, close=1.5,
                                                                    complete=True, source="rest")])
        again = dal.load_candles("99926000", "1m", _t(9, 16), until=_t(9, 17))
        assert len(again) == 1 and again[0].source == "rest" and again[0].close == 1.5
    finally:
        dal.close()


@pytest.mark.parametrize("name,factory", list(_dals()))
def test_context_presignal_signal_chain(name, factory):
    dal = _open(factory)
    try:
        rid = uuid.uuid4()
        dal.insert_run(rid, "SHADOW", _t(8, 0))
        ctx_id = dal.insert_context_snapshot(rid, _ctx())
        assert ctx_id >= 1 and dal.context_count(rid) == 1
        ev = StageEvent(str(uuid.uuid4()), "NIFTY", "up", "CONFIRMING", "TRADE_READY", 0.7, "confirmation_closed", 3,
                        {"trigger_level": 25008.0, "evidence": ["a", "b"]})
        pe_id = dal.insert_presignal_event(rid, _t(9, 30), ev, context_snapshot_id=ctx_id)
        assert pe_id >= 1
        sid = uuid.uuid4()
        dal.insert_signal(rid, signal_id=sid, setup_id=ev.setup_id, ts=_t(9, 30), mode="SHADOW", underlying="NIFTY",
                          direction="up", stage="TRADE_READY", status="would_be",
                          explanation={"Direction": "up", "Decision": "WOULD-BE SIGNAL"},
                          snapshot={"spot": 25012.0, "ts": _t(9, 30)}, context_snapshot_id=ctx_id)
        sigs = dal.signals_for_run(rid)
        assert len(sigs) == 1
        s = sigs[0]
        assert s["signal_id"] == str(sid) and s["status"] == "would_be" and s["context_snapshot_id"] == ctx_id
        assert s["explanation"]["Decision"] == "WOULD-BE SIGNAL" and s["snapshot"]["ts"] == "2026-09-16T09:30:00+05:30"
        pes = dal.presignal_events_for_run(rid)
        assert len(pes) == 1 and pes[0]["to_stage"] == "TRADE_READY" and pes[0]["details"]["evidence"] == ["a", "b"]
        assert float(pes[0]["confidence"]) == 0.7
        assert dal.signals_for_run(uuid.uuid4()) == []
    finally:
        dal.close()


@pytest.mark.parametrize("name,factory", list(_dals()))
def test_bad_signal_status_rejected(name, factory):
    dal = _open(factory)
    try:
        rid = uuid.uuid4()
        dal.insert_run(rid, "SHADOW", _t(8, 0))
        if name == "postgres":
            with pytest.raises(Exception):
                dal.insert_signal(rid, signal_id=uuid.uuid4(), setup_id=None, ts=_t(9, 30), mode="SHADOW",
                                  underlying="NIFTY", direction="up", stage="TRADE_READY", status="bogus",
                                  explanation={}, snapshot={})
        else:
            pytest.skip("CHECK constraint is a Postgres guarantee")
    finally:
        dal.close()


def test_migrations_are_versioned_and_idempotent_sql():
    versions = [v for v, _ in MIGRATIONS]
    assert versions == sorted(versions) == list(range(1, len(versions) + 1))
    assert all("CREATE TABLE IF NOT EXISTS" in sql for _, sql in MIGRATIONS)


@pytest.mark.skipif(not PG_URL, reason="TECH_TEST_DATABASE_URL not set")
def test_postgres_migrate_is_idempotent():
    from trading_bot.engine.db.dal import Database
    dal = Database(PG_URL).connect()
    try:
        dal.conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        assert dal.migrate() == [1]
        assert dal.migrate() == []
        assert dal.ping()
        tables = {r["table_name"] for r in dal.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()}
        assert {"runs", "candles", "context_snapshots", "presignal_events", "signals", "executions",
                "trade_results", "kill_switch_events", "option_chain_snapshots", "risk_decisions",
                "strategy_versions", "config_versions", "schema_migrations"} <= tables
    finally:
        dal.close()


def test_plain_and_hash_helpers():
    assert plain({"a": {1, 2}, "d": dt.date(2026, 9, 16)}) == {"a": [1, 2], "d": "2026-09-16"}
    assert config_hash({"x": 1, "y": 2}) == config_hash({"y": 2, "x": 1})
    assert len(config_hash({})) == 32


@pytest.mark.parametrize("name,factory", list(_dals()))
def test_m3_range_reads_and_strategy_versions(name, factory):
    dal = _open(factory)
    try:
        rid = uuid.uuid4()
        dal.insert_run(rid, "PAPER", _t(8, 0))
        dal.insert_signal(rid, signal_id=uuid.uuid4(), setup_id=None, ts=_t(9, 30), mode="PAPER", underlying="NIFTY", direction="up",
                          stage="RISK_ENGINE", status="risk_rejected", explanation={}, snapshot={}, reason_code="one_lot_exceeds_max_risk")
        dal.insert_trade_result(rid, None, entry_ts=_t(10, 0), exit_ts=_t(10, 30), entry_price=150.0, exit_price=155.0, quantity=75,
                                gross_pnl=375.0, costs=60.0, net_pnl=315.0, r_multiple=1.5, exit_reason="TARGET_1", mae=-50.0, mfe=400.0,
                                details={"strategy": "ORB"})
        dal.insert_execution(rid, None, _t(10, 0), mode="PAPER", side="BUY", state="POSITION_ACTIVE", broker_order_id="P1", ordertag="t",
                             requested_price=150.5, fill_price=150.7, quantity=75, filled_quantity=75, latency_ms=700, slippage=0.2, details={})
        dal.insert_kill_switch_event(rid, _t(11, 0), switch="trading", action="on", reason="daily_loss_cap", details={})
        day0, day1 = _t(0, 0), _t(23, 59)
        assert len(dal.runs_between(day0, day1)) == 1 and dal.runs_between(day1, day1) == []
        assert dal.signals_between(day0, day1)[0]["status"] == "risk_rejected"
        tr = dal.trade_results_between(day0, day1)
        assert len(tr) == 1 and float(tr[0]["net_pnl"]) == 315.0 and tr[0]["details"]["strategy"] == "ORB"
        assert dal.executions_between(day0, day1)[0]["state"] == "POSITION_ACTIVE"
        assert dal.kill_events_between(day0, day1)[0]["reason"] == "daily_loss_cap"
        vid = dal.insert_strategy_version("ORB", "0.1", {"decision": "shadow_only"})
        assert dal.insert_strategy_version("ORB", "0.1", {"decision": "paper"}) == vid
        assert dal.strategy_versions()[0]["params"]["decision"] == "paper"
    finally:
        dal.close()
