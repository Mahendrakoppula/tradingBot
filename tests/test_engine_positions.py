import datetime as dt

from trading_bot.costs import CostRates
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.execution import OrderStateMachine, PaperBroker, PaperParams, lock_snapshot
from trading_bot.engine.positions import KillSwitches, PositionBook, TradeResult, circuit_breaker_reason, reconcile
from trading_bot.engine.rejections import STAGES, Rejection, summarize
from trading_bot.engine.stops import ExitSignal, Plan, ThesisMonitor
from trading_bot.timeutil import IST

T0 = dt.datetime(2026, 9, 16, 10, 30, tzinfo=IST)
SPOT, ATR = 25000.0, 20.0


def _t(minutes: int) -> dt.datetime:
    return T0 + dt.timedelta(minutes=minutes)


def _snap(**over):
    base = dict(signal_id="sig-1", setup_id="set-1", ts=T0, underlying="NIFTY", underlying_price=SPOT, direction="up",
                strategy="TREND_PULLBACK", strategy_version="0.1", trend="BULL", regime="BULL", structure="bos_up", score=78,
                expected_move_points=40.0, remaining_move_points=35.0, option_token="1131", option_symbol="NIFTY23SEP2625000CE",
                option_exchange="NFO", option_type="CE", strike=25000.0, expiry="2026-09-23", lot_size=75, bid=149.5, ask=150.5,
                spread_pct=0.667, iv=0.14, delta=0.5, gamma=0.002, theta=-8.0, vega=30.0, option_entry=150.5, option_stop=147.5,
                option_target1=160.0, option_target2=166.0, stop_ref=SPOT - 30, target1_ref=SPOT + 60, quantity=75,
                risk_rupees=240.0, fingerprint="FP")
    base.update(over)
    return lock_snapshot(**base)


def _plan() -> Plan:
    return Plan("up", SPOT, SPOT - 30, SPOT + 60, SPOT + 96, SPOT + 116, 1.5, 150.5, 147.5, 160.0, 166.0, 3.0, 9.5)


def _ctx(spot=SPOT, bar=12, **over) -> ContextSnapshot:
    base = dict(ts=_t(5 * (bar - 10)), underlying="NIFTY", trigger_tf="5m", spot=spot, session_phase="10:00-11:30",
                quality="OK", bar_index=bar, trends={"5m": {"label": "BULL"}}, alignment={}, regime={"primary": "BULL"},
                structure={"last_event": None, "swing_low": SPOT - 40, "swing_high": SPOT + 80}, levels={},
                price_action={}, indicators={"atr": ATR, "vwap": SPOT - 30}, volume={})
    base.update(over)
    return ContextSnapshot(**base)


def _book_with_position(qty=75):
    book = PositionBook(ThesisMonitor(), CostRates())
    pos = book.open_position(_snap(quantity=qty), _plan(), "ex-1", qty, 150.75, T0, 10, entry_vwap=SPOT - 30)
    return book, pos


# --- kill switches / circuit breaker --------------------------------------------------------


def test_kill_switches_are_scoped_and_audited():
    k = KillSwitches()
    assert k.blocks_entry("TREND_PULLBACK", "NIFTY") is None
    k.kill_strategy("ORB", T0, "bad stats")
    assert k.blocks_entry("ORB", "NIFTY") == "strategy_kill" and k.blocks_entry("TREND_PULLBACK", "NIFTY") is None
    k.kill_index("SENSEX", T0, "feed unreliable")
    assert k.blocks_entry("TREND_PULLBACK", "SENSEX") == "index_kill"
    k.trip(T0, "data_stale")
    assert k.blocks_entry("TREND_PULLBACK", "NIFTY") == "circuit_breaker:data_stale"
    k.reset_breaker(_t(5))
    assert k.blocks_entry("TREND_PULLBACK", "NIFTY") is None
    k.kill_trading(T0, "manual")
    assert k.blocks_entry("TREND_PULLBACK", "NIFTY") == "trading_kill"
    k.kill_emergency(T0, "broker unstable")
    assert k.blocks_entry("TREND_PULLBACK", "NIFTY") == "emergency_kill"
    assert [e.switch for e in k.log] == ["strategy", "index", "circuit_breaker", "circuit_breaker", "trading", "emergency"]
    assert all(e.reason for e in k.log)


def test_worst_quality_orders_statuses():
    from trading_bot.engine.positions import worst_quality
    assert worst_quality([]) == "OK" and worst_quality(["OK", "OK"]) == "OK"
    assert worst_quality(["OK", "GAP", "STALE"]) == "GAP" and worst_quality(["GAP", "DISCONNECTED"]) == "DISCONNECTED"


def test_circuit_breaker_reasons():
    ok = dict(quality="OK", feed_connected=True, reconciled=True, api_errors_recent=0, clock_drift_seconds=1.0)
    assert circuit_breaker_reason(**ok) is None
    assert circuit_breaker_reason(**{**ok, "feed_connected": False}) == "feed_disconnected"
    assert circuit_breaker_reason(**{**ok, "quality": "GAP"}) == "data_gap"
    assert circuit_breaker_reason(**{**ok, "reconciled": False}) == "reconciliation_mismatch"
    assert circuit_breaker_reason(**{**ok, "api_errors_recent": 5}) == "repeated_api_errors"
    assert circuit_breaker_reason(**{**ok, "clock_drift_seconds": 9.0}) == "clock_drift"
    assert circuit_breaker_reason(**ok, spread_pct=12.0) == "abnormal_spread"


# --- position book ---------------------------------------------------------------------------


def test_open_manage_and_close_produces_a_full_trade_result():
    book, pos = _book_with_position()
    assert book.open_directions() == {"NIFTY": "up"} and book.open_risk() == round((150.75 - 147.5) * 75, 2)
    kills = KillSwitches()
    # bar 12: in profit, no exit
    assert book.manage(pos, _ctx(spot=SPOT + 10, bar=12), option_bid=155.0, option_spread_pct=0.7, eod=False, risk_limit_hit=False, kills=kills) is None
    assert pos.mfe == round((155.0 - 150.75) * 75, 2)
    # bar 13: stop hit on the underlying
    sig = book.manage(pos, _ctx(spot=SPOT - 31, bar=13), option_bid=146.0, option_spread_pct=0.7, eod=False, risk_limit_hit=False, kills=kills)
    assert sig.reason == "STOP_LOSS" and sig.quantity == 75
    req = book.exit_request(pos, sig, bid=146.0)
    assert req.side == "SELL" and req.quantity == 75 and req.purpose == "EXIT" and req.limit_price == round(146.0 * 0.995, 2)
    assert pos.pending_exit is req
    # while the exit is in flight the monitor does not stack another one
    assert book.manage(pos, _ctx(spot=SPOT - 35, bar=14), option_bid=145.0, option_spread_pct=0.7, eod=False, risk_limit_hit=False, kills=kills) is None
    res = book.on_exit_filled(pos, 75, 145.8, _t(20), spot_now=SPOT - 33)
    assert isinstance(res, TradeResult) and book.open == {} and book.closed == [res]
    assert res.exit_reason == "STOP_LOSS" and res.quantity == 75 and res.exit_price == 145.8
    assert res.gross_pnl == round((145.8 - 150.75) * 75, 2)
    assert res.total_cost > 0 and res.net_pnl == round(res.gross_pnl - res.total_cost, 2)
    assert res.brokerage == 40.0 and res.stt > 0 and res.gst > 0 and res.spread_cost > 0
    assert res.mfe > 0 > res.mae and res.holding_minutes == 20.0
    assert res.actual_underlying_move == -33.0 and res.expected_underlying_move == 35.0
    assert res.actual_option_move == round(145.8 - 150.75, 2) and res.fingerprint == "FP"
    d = res.as_dict()
    assert d["entry_ts"] == T0.isoformat() and d["status"] == "closed"


def test_partial_exit_then_final_exit_costs_extra_leg():
    book, pos = _book_with_position(qty=150)
    kills = KillSwitches()
    sig = book.manage(pos, _ctx(spot=SPOT + 61, bar=12), option_bid=161.0, option_spread_pct=0.7, eod=False, risk_limit_hit=False, kills=kills)
    assert sig.reason == "TARGET_1" and sig.quantity == 75
    req = book.exit_request(pos, sig, bid=161.0)
    assert req.purpose == "PARTIAL_EXIT"
    assert book.on_exit_filled(pos, 75, 160.8, _t(10), spot_now=SPOT + 61) is None
    assert pos.quantity == 75 and pos.pending_exit is None
    sig2 = book.manage(pos, _ctx(spot=SPOT + 97, bar=14), option_bid=170.0, option_spread_pct=0.7, eod=False, risk_limit_hit=False, kills=kills)
    assert sig2.reason in ("TARGET_2", "EXTENDED_TARGET")
    book.exit_request(pos, sig2, bid=170.0)
    res = book.on_exit_filled(pos, 75, 169.5, _t(20), spot_now=SPOT + 97)
    assert res.quantity == 150 and len(res.exits) == 2 and res.exit_reason == sig2.reason
    assert res.brokerage == 60.0  # buy + two sells
    assert res.exit_price == round((160.8 * 75 + 169.5 * 75) / 150, 2)


def test_scoped_kills_close_positions_and_eod_sweep():
    book, pos = _book_with_position()
    kills = KillSwitches()
    kills.kill_strategy("TREND_PULLBACK", T0, "stats")
    sig = book.manage(pos, _ctx(spot=SPOT + 5, bar=12), option_bid=151.0, option_spread_pct=0.7, eod=False, risk_limit_hit=False, kills=kills)
    assert sig.reason == "MANUAL_KILL_SWITCH"
    book2, pos2 = _book_with_position()
    kills2 = KillSwitches()
    kills2.kill_trading(T0, "manual")  # trading kill: entries only, positions keep managing
    assert book2.manage(pos2, _ctx(spot=SPOT + 5, bar=12), option_bid=151.0, option_spread_pct=0.7, eod=False, risk_limit_hit=False, kills=kills2) is None
    reqs = book2.eod_exit_requests({"1131": 151.0})
    assert len(reqs) == 1 and reqs[0][1].purpose == "EXIT" and reqs[0][1].timeout_seconds == 8 and pos2.pending_reason == "EOD_EXIT"
    assert book2.eod_exit_requests({"1131": 151.0}) == []  # already pending


# --- reconciliation --------------------------------------------------------------------------------


def test_reconcile_clean_and_mismatches():
    pb = PaperBroker(PaperParams.for_profile("ideal"))
    pb.set_quote("1131", 149.5, 150.5)
    osm = OrderStateMachine(pb)
    from trading_bot.engine.execution import OrderRequest
    rec = osm.new("ex-1", "sig-1", "set-1", T0)
    osm.risk_approved(rec, T0)
    osm.send(rec, OrderRequest("sig-1:ENTRY", "sig-1", "set-1", "1131", "NFO", "X", "BUY", 75, "MARKET", None, None), T0)
    osm.poll(rec, T0)
    book = PositionBook(ThesisMonitor(), CostRates())
    pos = book.open_position(_snap(), _plan(), "ex-1", rec.filled_quantity, rec.average_price, T0, 10, None)
    r = reconcile(book, pb, T0)
    assert r.ok and r.mismatches == ()
    # quantity mismatch: book thinks 75, broker got flat
    pb._positions.clear()
    r2 = reconcile(book, pb, T0)
    assert not r2.ok and "book 75 vs broker 0" in r2.mismatches[0]
    # broker holds something the book does not know
    book.open.clear()
    pb.set_quote("2222", 10.0, 11.0)
    pb.place(OrderRequest("x:ENTRY", "x", None, "2222", "NFO", "Y", "BUY", 75, "MARKET", None, None), T0)
    pb.poll("P000002", T0)
    r3 = reconcile(book, pb, T0)
    assert not r3.ok and "broker holds 75, book has none" in r3.mismatches[0]


# --- rejected-signal dataset -------------------------------------------------------------------------


def test_rejections_map_to_signal_statuses_and_summarise():
    assert len(STAGES) == 14
    rs = [Rejection("s1", "u1", "NIFTY", "up", "RISK_ENGINE", "one_lot_exceeds_max_risk", strategy="ORB", score=70),
          Rejection("s2", "u2", "NIFTY", "down", "OPTION_SELECTION", "no_acceptable_option"),
          Rejection("s3", "u3", "SENSEX", "up", "PRE_SIGNAL", "ttl_exceeded"),
          Rejection("s4", "u4", "SENSEX", "up", "EXECUTION_GATE", "drift_reject", counter_trend=True),
          Rejection("s5", "u5", "BANKNIFTY", "up", "RISK_ENGINE", "one_lot_exceeds_max_risk")]
    assert [r.status for r in rs] == ["risk_rejected", "option_rejected", "expired", "invalidated", "risk_rejected"]
    s = summarize(rs)
    assert s["total"] == 5 and s["by_stage"]["RISK_ENGINE"] == 2
    assert list(s["by_reason"])[0] == "RISK_ENGINE:one_lot_exceeds_max_risk"
    assert rs[0].as_dict()["status"] == "risk_rejected" and rs[3].as_dict()["counter_trend"] is True
