"""§54 restart recovery: kill a PAPER session with an open position, start a
new loop over the same journal, and check the book, account, kills and
reconciliation come back exactly."""
import dataclasses
import datetime as dt
import importlib.util
import uuid
from pathlib import Path

from trading_bot.engine.db.memory import MemoryDAL
from trading_bot.engine.execution import PaperBroker, PaperParams
from trading_bot.engine.positions import KillSwitches, PositionBook
from trading_bot.engine.recovery import recover
from trading_bot.engine.risk_engine import AccountState
from trading_bot.engine.stops import ThesisMonitor
from trading_bot.costs import CostRates
from trading_bot.timeutil import IST

_spec = importlib.util.spec_from_file_location("pl_t", Path(__file__).resolve().parent / "test_engine_paper_loop.py")
pl_t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pl_t)

DAY = pl_t.DAY


def _session_with_open_position():
    """Run 1: enter a paper trade, close a second one at a loss, trip a strategy kill, then 'die'."""
    loop, clock = pl_t._loop(execute=True, profile="ideal")
    ctx = pl_t.pipe_t._ctx()
    pl_t._trade_ready(loop, ctx)
    assert len(loop.book.open) == 1
    pos = next(iter(loop.book.open.values()))
    # a closed losing trade earlier "today" for the tallies
    loop.dal.insert_trade_result(loop.run_id, "earlier", entry_ts=ctx.ts - dt.timedelta(hours=1), exit_ts=ctx.ts - dt.timedelta(minutes=30),
                                 entry_price=100.0, exit_price=95.0, quantity=75, gross_pnl=-375.0, costs=60.0, net_pnl=-435.0,
                                 r_multiple=-1.0, exit_reason="STOP_LOSS", mae=-400.0, mfe=50.0, details={})
    loop.kills.kill_strategy("ORB", clock.now(), "bad stats")
    loop._journal_kill("strategy", "on", "bad stats", target="ORB")
    return loop, pos, clock


def test_recovery_rebuilds_book_account_and_kills():
    dead, pos, clock = _session_with_open_position()
    dal = dead.dal
    # a fresh process over the same journal
    book = PositionBook(ThesisMonitor(), CostRates())
    broker = PaperBroker(PaperParams.for_profile("ideal"))
    account = AccountState(equity=100000.0)
    kills = KillSwitches()
    rep = recover(dal, day=DAY, now=clock.now() + dt.timedelta(minutes=5), book=book, broker=broker, account=account, kills=kills, mode="PAPER")
    assert rep.prior_runs == 1 and rep.positions_recovered == 1 and rep.positions_skipped == []
    assert rep.trades_today == 1 and rep.realized_today == -435.0 and rep.consecutive_losses == 1 and rep.realized_week == -435.0
    assert account.trades_today == 1 and account.consecutive_losses == 1
    assert rep.kills_reapplied == ["strategy:ORB"] and "ORB" in kills.strategies and not kills.trading
    assert rep.reconciled and rep.safe_to_resume
    new_pos = next(iter(book.open.values()))
    assert new_pos.token == pos.token and new_pos.quantity == pos.quantity and new_pos.entry_price == pos.entry_price
    assert new_pos.plan.stop_ref == pos.plan.stop_ref and new_pos.plan.option_stop == pos.plan.option_stop
    assert new_pos.plan.target1_ref == pos.plan.target1_ref and new_pos.signal_id == pos.signal_id
    assert broker.positions()[0].quantity == pos.quantity
    assert rep.summary().startswith("RECOVERY") and rep.summary().endswith("- RESUME")


def test_recovered_position_is_managed_to_exit():
    dead, pos, clock = _session_with_open_position()
    loop2, _ = pl_t._loop(execute=True, profile="ideal")
    loop2.dal = dead.dal  # same journal
    loop2.day = DAY
    from trading_bot.engine.recovery import recover as _r
    rep = _r(dead.dal, day=DAY, now=clock.now(), book=loop2.book, broker=loop2.broker, account=loop2.account, kills=loop2.kills, mode="PAPER")
    assert rep.positions_recovered == 1
    p2 = next(iter(loop2.book.open.values()))
    ctx = pl_t.pipe_t._ctx()
    loop2.last_ctx["NIFTY"] = ctx
    loop2.chains.refresh("NIFTY", ctx.spot, clock.now())
    q = loop2.chains.caches["NIFTY"].quotes[p2.token]
    loop2.chains.caches["NIFTY"].quotes[p2.token] = dataclasses.replace(q, bid=p2.plan.option_stop - 0.5, ask=p2.plan.option_stop + 0.5)
    loop2.broker.set_quote(p2.token, p2.plan.option_stop - 0.5, p2.plan.option_stop + 0.5)
    loop2._manage("NIFTY", dataclasses.replace(ctx, spot=p2.plan.stop_ref - 1, bar_index=30), clock.now())
    assert loop2.stats.trades_closed == 1 and loop2.book.open == {}
    assert dead.dal.trade_results[-1]["exit_reason"] == "STOP_LOSS"


def test_fully_closed_trades_do_not_reappear_and_shadow_has_no_positions():
    dead, pos, clock = _session_with_open_position()
    # close the position in the dead loop before it "dies"
    ctx = pl_t.pipe_t._ctx()
    q = dead.chains.caches["NIFTY"].quotes[pos.token]
    dead.chains.caches["NIFTY"].quotes[pos.token] = dataclasses.replace(q, bid=pos.plan.option_target2 + 1, ask=pos.plan.option_target2 + 2)
    dead.broker.set_quote(pos.token, pos.plan.option_target2 + 1, pos.plan.option_target2 + 2)
    dead._manage("NIFTY", dataclasses.replace(ctx, spot=pos.plan.target1_ref + 1, bar_index=22), clock.now())
    dead._manage("NIFTY", dataclasses.replace(ctx, spot=pos.plan.target2_ref + 1, bar_index=24), clock.now())
    assert dead.book.open == {}
    book = PositionBook(ThesisMonitor(), CostRates())
    rep = recover(dead.dal, day=DAY, now=clock.now(), book=book, broker=PaperBroker(), account=AccountState(equity=1e5), kills=KillSwitches(), mode="PAPER")
    assert rep.positions_recovered == 0 and rep.trades_today == 2 and rep.safe_to_resume
    shadow = recover(dead.dal, day=DAY, now=clock.now(), book=PositionBook(ThesisMonitor(), CostRates()), broker=None,
                     account=AccountState(equity=1e5), kills=KillSwitches(), mode="PAPER")
    assert shadow.positions_recovered == 0 and "shadow" in shadow.notes[0]


def test_missing_snapshot_makes_recovery_unsafe():
    dead, pos, clock = _session_with_open_position()
    for s in dead.dal.signals:
        s["snapshot"] = {"stage_reached": "SNAPSHOT"}  # corrupt: no locked snapshot
    rep = recover(dead.dal, day=DAY, now=clock.now(), book=PositionBook(ThesisMonitor(), CostRates()), broker=PaperBroker(),
                  account=AccountState(equity=1e5), kills=KillSwitches(), mode="PAPER")
    assert rep.positions_recovered == 0 and rep.positions_skipped and not rep.safe_to_resume
    assert rep.summary().endswith("NOT SAFE, new entries stopped")


def test_paper_loop_start_runs_recovery_and_reports():
    dead, pos, clock = _session_with_open_position()
    notes = pl_t._Notes()
    loop2, clock2 = pl_t._loop(execute=True, profile="ideal", notes=notes)
    loop2.dal = dead.dal
    loop2.run_id = uuid.UUID(int=6)
    loop2.start()
    assert len(loop2.book.open) == 1 and "ORB" in loop2.kills.strategies and not loop2.kills.trading
    assert any(m.startswith("RECOVERY") for m in notes.msgs)
    # first start of the day: silent
    fresh, _ = pl_t._loop(execute=True, profile="ideal", notes=pl_t._Notes())
    fresh.dal = MemoryDAL()
    fresh.start()
    assert fresh.book.open == {} and not fresh.kills.trading
