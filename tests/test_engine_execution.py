import datetime as dt

import pytest

from trading_bot.engine.execution import (
    ORDER_STATES,
    GateParams,
    OrderRequest,
    OrderStateMachine,
    PaperBroker,
    PaperParams,
    drift_check,
    execution_gate,
    lock_snapshot,
)
from trading_bot.engine.execution.orders import DuplicateOrderError
from trading_bot.engine.execution.snapshot import DriftParams
from trading_bot.engine.risk_engine import RiskDecision
from trading_bot.timeutil import IST

T0 = dt.datetime(2026, 9, 16, 10, 30, tzinfo=IST)


def _t(ms: int) -> dt.datetime:
    return T0 + dt.timedelta(milliseconds=ms)


def _snap(**over):
    base = dict(signal_id="sig-1", setup_id="set-1", ts=T0, underlying="NIFTY", underlying_price=25000.0, direction="up",
                strategy="TREND_PULLBACK", strategy_version="0.1", trend="BULL", regime="BULL", structure="bos_up", score=78,
                expected_move_points=40.0, remaining_move_points=35.0, option_token="1131", option_symbol="NIFTY23SEP2625000CE",
                option_exchange="NFO", option_type="CE", strike=25000.0, expiry="2026-09-23", lot_size=75, bid=149.5, ask=150.5,
                spread_pct=0.667, iv=0.14, delta=0.5, gamma=0.002, theta=-8.0, vega=30.0, option_entry=150.5, option_stop=147.5,
                option_target1=160.0, option_target2=166.0, stop_ref=24970.0, target1_ref=25060.0, quantity=75,
                risk_rupees=240.0, fingerprint="NIFTY|TREND_PULLBACK|BULL|DAILY+30M+5M|EMA|NO_SWEEP|BOS|REJECTION|HIGH_VOLUME|CE|MORNING")
    base.update(over)
    return lock_snapshot(**base)


def _req(qty=75, side="BUY", tag="sig-1:ENTRY", purpose="ENTRY", limit=None, max_price=None, token="1131", timeout=20):
    return OrderRequest(tag, "sig-1", "set-1", token, "NFO", "NIFTY23SEP2625000CE", side, qty, "LIMIT" if limit else "MARKET",
                        limit, max_price, timeout_seconds=timeout, purpose=purpose)


def _approved(qty=75):
    return RiskDecision("APPROVED", None, qty, qty // 75, 75, 3.3, 240.0, 250.0, 700.0, 600.0, 120.0, 0.45, 2.5, "B", None)


# --- snapshot + drift ---------------------------------------------------------------------


def test_snapshot_is_immutable_and_serialisable():
    s = _snap()
    with pytest.raises(Exception):
        s.quantity = 150  # type: ignore[misc]
    d = s.as_dict()
    assert d["ts"] == T0.isoformat() and d["quantity"] == 75 and len(d) >= 36


def test_drift_verdicts():
    s = _snap()
    ok = drift_check(s, now=_t(5000), spot_now=25002.0, bid_now=149.6, ask_now=150.6, atr=20.0)
    assert ok.action == "PROCEED" and ok.reason_code is None
    ran = drift_check(s, now=_t(5000), spot_now=25010.0, bid_now=155.0, ask_now=156.0, atr=20.0)
    assert ran.action == "REJECT" and ran.reason_code == "underlying_ran_away"
    repriced = drift_check(s, now=_t(5000), spot_now=25004.0, bid_now=156.0, ask_now=157.5, atr=20.0)
    assert repriced.action == "REJECT" and repriced.reason_code == "option_repriced_up"
    stale = drift_check(s, now=_t(120_000), spot_now=25000.0, bid_now=149.5, ask_now=150.5, atr=20.0)
    assert stale.action == "REJECT" and stale.reason_code == "snapshot_stale"
    wide = drift_check(s, now=_t(5000), spot_now=25000.0, bid_now=147.0, ask_now=153.0, atr=20.0)
    assert wide.reason_code == "spread_widened"
    adverse = drift_check(s, now=_t(5000), spot_now=24990.0, bid_now=143.0, ask_now=144.0, atr=20.0)
    assert adverse.action == "REPRICE" and adverse.reason_code == "adverse_move_since_snapshot"
    assert drift_check(s, now=_t(5000), spot_now=25000.0, bid_now=0.0, ask_now=150.5, atr=20.0).reason_code == "no_two_sided_quote"


# --- paper broker + state machine ---------------------------------------------------------------


def test_realistic_paper_fill_walks_the_state_machine_with_latency():
    pb = PaperBroker(PaperParams(profile="realistic", reject_probability=0.0, timeout_probability=0.0))
    pb.set_quote("1131", 149.5, 150.5)
    osm = OrderStateMachine(pb)
    rec = osm.new("ex-1", "sig-1", "set-1", confirmation_at=T0)
    osm.risk_approved(rec, _t(100))
    osm.send(rec, _req(), _t(200))
    assert rec.state == "ORDER_SENT" and rec.broker_order_id == "P000001"
    osm.poll(rec, _t(400))  # ack lands at +550 (send +200, latency 350): not yet
    assert rec.state == "ORDER_SENT" and rec.filled_quantity == 0
    osm.poll(rec, _t(600))
    assert rec.state == "ORDER_ACKNOWLEDGED" and rec.filled_quantity == 0
    osm.poll(rec, _t(1000))
    assert rec.state == "POSITION_ACTIVE" and rec.filled_quantity == 75
    assert rec.average_price == 150.75  # ask + half of (slippage share x spread)
    lat = rec.latency.as_dict()
    assert lat["request_to_ack_ms"] == 350 and lat["signal_to_fill_ms"] == 1000 and lat["decision_to_request_ms"] == 100
    assert [h[1] for h in rec.history] == ["SIGNAL_READY", "RISK_APPROVED", "EXECUTION_PENDING", "ORDER_SENT",
                                           "ORDER_ACKNOWLEDGED", "FILLED", "POSITION_ACTIVE"]
    assert pb.positions()[0].quantity == 75 and not osm.circuit_breaker_tripped


def test_ideal_profile_fills_at_mid_instantly_and_is_labelled():
    pb = PaperBroker(PaperParams.for_profile("ideal"))
    pb.set_quote("1131", 149.5, 150.5)
    osm = OrderStateMachine(pb)
    rec = osm.new("ex-1", "sig-1", None, T0)
    osm.risk_approved(rec, T0)
    osm.send(rec, _req(), T0)
    osm.poll(rec, T0)
    assert rec.state == "POSITION_ACTIVE" and rec.average_price == 150.0
    # exit and check the trade log carries the profile label
    osm2 = osm
    ex = osm2.new("ex-2", "sig-1", None, T0)
    osm2.risk_approved(ex, T0)
    osm2.send(ex, _req(side="SELL", tag="sig-1:EXIT", purpose="EXIT"), T0)
    osm2.poll(ex, T0)
    assert pb.trade_log[0]["profile"] == "ideal" and pb.positions() == []


def test_partial_fill_then_completion_and_actual_exposure():
    pb = PaperBroker(PaperParams(profile="realistic", partial_fill_threshold_qty=100, reject_probability=0.0, timeout_probability=0.0))
    pb.set_quote("1131", 149.5, 150.5)
    osm = OrderStateMachine(pb)
    rec = osm.new("ex-1", "sig-1", "set-1", T0)
    osm.risk_approved(rec, T0)
    osm.send(rec, _req(qty=300), T0)
    osm.poll(rec, _t(800))
    assert rec.state == "PARTIALLY_FILLED" and rec.filled_quantity == 150 and rec.actual_exposure == 150
    osm.poll(rec, _t(1600))
    assert rec.state == "POSITION_ACTIVE" and rec.filled_quantity == 300


def test_timeout_cancels_and_partial_becomes_position():
    pb = PaperBroker(PaperParams(profile="realistic", reject_probability=0.0, timeout_probability=0.0))
    pb.set_quote("1131", 149.5, 150.5)
    osm = OrderStateMachine(pb)
    rec = osm.new("ex-1", "sig-1", "set-1", T0)
    osm.risk_approved(rec, T0)
    osm.send(rec, _req(limit=149.0, timeout=5), T0)  # limit below the ask: rests
    osm.poll(rec, _t(2000))
    assert rec.state == "ORDER_ACKNOWLEDGED" and rec.filled_quantity == 0
    osm.poll(rec, _t(6000))
    assert rec.state == "TIMEOUT" and rec.reason == "timeout" and pb.open_orders() == []
    # partial then timeout -> live position of the filled part
    pb2 = PaperBroker(PaperParams(profile="realistic", partial_fill_threshold_qty=100, reject_probability=0.0, timeout_probability=0.0))
    pb2.set_quote("1131", 149.5, 150.5)
    osm2 = OrderStateMachine(pb2)
    r2 = osm2.new("ex-2", "sig-2", "set-2", T0)
    osm2.risk_approved(r2, T0)
    osm2.send(r2, _req(qty=300, timeout=1, max_price=160.0), T0)
    osm2.poll(r2, _t(800))
    assert r2.state == "PARTIALLY_FILLED"
    pb2.set_quote("1131", 190.0, 191.0)  # price gone: the rest cannot fill under max_price
    osm2.poll(r2, _t(3000))
    assert r2.state == "POSITION_ACTIVE" and r2.filled_quantity == 150 and r2.history[-1][2] == "partial"


def test_broker_reject_and_place_failure_are_terminal_without_retry():
    pb = PaperBroker(PaperParams(profile="realistic", reject_probability=1.0))
    pb.set_quote("1131", 149.5, 150.5)
    osm = OrderStateMachine(pb)
    rec = osm.new("ex-1", "sig-1", "set-1", T0)
    osm.risk_approved(rec, T0)
    osm.send(rec, _req(), T0)
    assert rec.state == "REJECTED" and rec.reason == "simulated_broker_reject"

    class Boom(PaperBroker):
        def place(self, req, now):
            raise ConnectionError("broker down")

    b = Boom()
    b.set_quote("1131", 149.5, 150.5)
    osm2 = OrderStateMachine(b)
    r = osm2.new("ex-2", "sig-2", None, T0)
    osm2.risk_approved(r, T0)
    osm2.send(r, _req(tag="sig-2:ENTRY"), T0)
    assert r.state == "FAILED" and "broker down" in r.reason and len(b.orders()) == 0


def test_duplicate_protection_across_signal_setup_token_and_broker():
    pb = PaperBroker(PaperParams(profile="realistic", reject_probability=0.0, timeout_probability=0.0))
    pb.set_quote("1131", 149.5, 150.5)
    osm = OrderStateMachine(pb)
    rec = osm.new("ex-1", "sig-1", "set-1", T0)
    osm.risk_approved(rec, T0)
    osm.send(rec, _req(), T0)
    with pytest.raises(DuplicateOrderError):
        osm.new("ex-1", "sig-9", None, T0)
    assert osm.duplicate_check("sig-1", None, "9999") == "duplicate_signal_or_setup"
    assert osm.duplicate_check("sig-2", "set-1", "9999") == "duplicate_signal_or_setup"
    assert osm.duplicate_check("sig-3", "set-3", "1131") == "order_already_open_for_token"
    osm.poll(rec, _t(1000))  # filled -> broker long
    assert osm.duplicate_check("sig-3", "set-3", "1131") == "broker_already_long_token"
    assert osm.duplicate_check("sig-3", "set-3", "2222") is None
    # a second send for the same signal is rejected, not placed
    r2 = osm.new("ex-2", "sig-1", "set-1", T0)
    osm.risk_approved(r2, T0)
    osm.send(r2, _req(tag="sig-1:ENTRY-again"), T0)
    assert r2.state == "REJECTED" and r2.reason == "duplicate_signal_or_setup" and len(pb.orders()) == 1
    # same client tag -> broker returns the existing order (idempotent)
    assert pb.place(_req(), T0).broker_order_id == "P000001" and len(pb.orders()) == 1


def test_latency_circuit_breaker():
    pb = PaperBroker(PaperParams(profile="conservative", latency_ms=4000, reject_probability=0.0, timeout_probability=0.0))
    pb.set_quote("1131", 149.5, 150.5)
    osm = OrderStateMachine(pb, latency_threshold_ms=5000)
    rec = osm.new("ex-1", "sig-1", None, T0)
    osm.risk_approved(rec, T0)
    osm.send(rec, _req(), T0)
    osm.poll(rec, _t(9000))
    assert rec.state == "POSITION_ACTIVE" and osm.circuit_breaker_tripped


def test_state_vocabulary_and_illegal_transition():
    assert len(ORDER_STATES) == 12
    pb = PaperBroker()
    osm = OrderStateMachine(pb)
    rec = osm.new("ex-1", "sig-1", None, T0)
    with pytest.raises(ValueError):
        osm._move(rec, "FILLED", T0)


# --- gate -----------------------------------------------------------------------------------


def _gate(**over):
    pb = over.pop("broker", None) or PaperBroker(PaperParams(profile="realistic", reject_probability=0.0, timeout_probability=0.0))
    if pb.quote("1131") is None and not over.pop("no_broker_quote", False):
        pb.set_quote("1131", 149.5, 150.5)
    osm = over.pop("osm", None) or OrderStateMachine(pb)
    kw = dict(now=_t(3000), spot_now=25001.0, atr=20.0, bid=149.5, ask=150.5, bid_qty=500, ask_qty=400, quality="OK",
              feed_connected=True, reconciled=True, broker=pb, osm=osm, kill_new_entries=False, eod_cutoff_passed=False)
    kw.update(over)
    return execution_gate(_snap(), _approved(), **kw)


def test_gate_passes_and_prices_a_capped_limit():
    g = _gate()
    assert g.passed and g.reason_code is None and g.quantity == 75
    assert g.limit_price == round(150.5 * 1.0025, 2) and g.max_price == g.limit_price
    assert [c[0] for c in g.checks] == ["kill_switches", "risk_approved", "data_and_connectivity",
                                         "snapshot_fresh_and_price_ok", "liquidity", "duplicate_protection", "broker_connectivity"]


def test_gate_fails_in_spec_order():
    assert _gate(kill_new_entries=True).reason_code == "new_entries_killed"
    assert _gate(eod_cutoff_passed=True).reason_code == "after_eod_cutoff"
    assert _gate(quality="STALE").reason_code == "data_quality"
    assert _gate(feed_connected=False).reason_code == "feed_disconnected"
    assert _gate(reconciled=False).reason_code == "reconciliation_mismatch"
    assert _gate(spot_now=25015.0).reason_code == "drift_reject"
    assert _gate(bid=146.0, ask=152.0).reason_code in ("drift_reject", "spread_too_wide")
    assert _gate(ask_qty=0).reason_code == "no_depth"
    pb = PaperBroker(PaperParams(profile="realistic", reject_probability=0.0, timeout_probability=0.0))
    assert _gate(broker=pb, no_broker_quote=True).reason_code == "broker_no_quote"
    rejected = execution_gate(_snap(), RiskDecision("REJECTED", "daily_loss_lock", 0, 0, 75, 0, 0, 0, 0, 0, 0, 0, 0, "C", None),
                              now=_t(1000), spot_now=25000.0, atr=20.0, bid=149.5, ask=150.5, bid_qty=1, ask_qty=1, quality="OK",
                              feed_connected=True, reconciled=True, broker=pb, osm=OrderStateMachine(pb), kill_new_entries=False,
                              eod_cutoff_passed=False)
    assert rejected.reason_code == "risk_not_approved"


def test_gate_blocks_duplicates_and_tripped_breaker():
    pb = PaperBroker(PaperParams(profile="realistic", reject_probability=0.0, timeout_probability=0.0))
    pb.set_quote("1131", 149.5, 150.5)
    osm = OrderStateMachine(pb)
    rec = osm.new("ex-1", "sig-1", "set-1", T0)
    osm.risk_approved(rec, T0)
    osm.send(rec, _req(), T0)
    assert _gate(broker=pb, osm=osm).reason_code == "duplicate"
    osm2 = OrderStateMachine(pb)
    osm2.circuit_breaker_tripped = True
    assert _gate(broker=pb, osm=osm2).reason_code == "execution_circuit_breaker"
