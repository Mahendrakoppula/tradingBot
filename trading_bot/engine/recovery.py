"""Restart recovery (spec §54): after a mid-session restart, rebuild local
state from the JOURNAL - never trust what the previous process thought.

    connect broker -> fetch positions/orders (journal for paper) ->
    reconstruct local state -> reconcile -> validate -> resume only when safe

For PAPER the "broker" died with the process, so the journal is the
authority: today's filled entry legs minus filled exit legs give the open
positions; the signal's locked snapshot gives the plan; today's trade
results give the account tallies; today's kill events give the switches.
The paper broker is re-seeded so reconciliation can run against it. For
LIVE (M4b) the same function will take broker positions as the authority
and treat any journal disagreement as a mismatch.
"""
import datetime as dt
from dataclasses import dataclass, field

from trading_bot.engine.clock import session_open_at
from trading_bot.engine.execution.snapshot import SignalSnapshot
from trading_bot.engine.positions import PositionBook, KillSwitches, reconcile
from trading_bot.engine.risk_engine import AccountState
from trading_bot.engine.stops import Plan


@dataclass
class RecoveryReport:
    day: dt.date
    prior_runs: int = 0
    positions_recovered: int = 0
    positions_skipped: list = field(default_factory=list)  # (signal_id, reason)
    trades_today: int = 0
    realized_today: float = 0.0
    realized_week: float = 0.0
    consecutive_losses: int = 0
    kills_reapplied: list = field(default_factory=list)
    reconciled: bool = True
    mismatches: tuple = ()
    safe_to_resume: bool = True
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        head = f"RECOVERY {self.day}: prior_runs={self.prior_runs} positions={self.positions_recovered} trades_today={self.trades_today} " \
               f"realized_today={self.realized_today:+.0f} week={self.realized_week:+.0f} streak={self.consecutive_losses}"
        tail = "" if self.reconciled else f" MISMATCH {list(self.mismatches)}"
        return head + (f" kills={self.kills_reapplied}" if self.kills_reapplied else "") + tail + \
            (" - RESUME" if self.safe_to_resume else " - NOT SAFE, new entries stopped")


def _plan_from_snapshot(s: SignalSnapshot) -> Plan:
    d = s.direction
    sgn = 1 if d == "up" else -1
    reward = abs(s.target1_ref - s.underlying_price)
    t2 = s.underlying_price + sgn * reward * 1.6
    ext = max((s.underlying_price + sgn * 3.0 * max(1.0, reward / 2.0)) * sgn, (t2 + sgn * max(1.0, reward / 3.0)) * sgn) * sgn
    stop_dist = abs(s.underlying_price - s.stop_ref)
    return Plan(d, s.underlying_price, s.stop_ref, s.target1_ref, round(t2, 2), round(ext, 2),
                round(stop_dist / max(1.0, reward / 3.0), 3), s.option_entry, s.option_stop, s.option_target1, s.option_target2,
                round(s.option_entry - s.option_stop, 2), round(s.option_target1 - s.option_entry, 2), notes=["recovered from journal"])


def recover(dal, *, day: dt.date, now: dt.datetime, book: PositionBook, broker, account: AccountState, kills: KillSwitches,
            mode: str) -> RecoveryReport:
    rep = RecoveryReport(day)
    start = session_open_at(day)
    end = start + dt.timedelta(hours=8)
    runs = [r for r in dal.runs_between(start - dt.timedelta(hours=2), end) if r.get("mode") == mode]
    rep.prior_runs = len(runs)

    # --- account tallies from today's / this week's trade results (§34) -------------------
    week_start = start - dt.timedelta(days=start.weekday())
    week_rows = dal.trade_results_between(week_start, end)
    today_rows = sorted([r for r in week_rows if r["exit_ts"] >= start], key=lambda r: r["exit_ts"])
    account.realized_week = round(sum(float(r["net_pnl"]) for r in week_rows), 2)
    account.realized_today = round(sum(float(r["net_pnl"]) for r in today_rows), 2)
    account.trades_today = len(today_rows)
    streak = 0
    for r in today_rows:
        streak = streak + 1 if float(r["net_pnl"]) < 0 else 0
    account.consecutive_losses = streak
    rep.trades_today, rep.realized_today, rep.realized_week, rep.consecutive_losses = len(today_rows), account.realized_today, account.realized_week, streak

    # --- kill switches that were on when the previous process died (§56) ---------------------
    state: dict[tuple, dict] = {}
    for k in dal.kill_events_between(start, end):
        key = (k["switch"], (k.get("details") or {}).get("target"))
        state[key] = k
    for (switch, target), k in state.items():
        if k["action"] != "on":
            continue
        if switch == "trading":
            kills.kill_trading(now, f"recovered: {k['reason']}")
            account.daily_lock = "daily_loss" in str(k["reason"])
        elif switch == "emergency":
            kills.kill_emergency(now, f"recovered: {k['reason']}")
        elif switch == "strategy" and target:
            kills.kill_strategy(target, now, f"recovered: {k['reason']}")
        elif switch == "index" and target:
            kills.kill_index(target, now, f"recovered: {k['reason']}")
        elif switch == "circuit_breaker":
            continue  # re-evaluated live from current data, never carried over
        rep.kills_reapplied.append(switch if not target else f"{switch}:{target}")

    # --- open positions from today's executions (§44: filled quantities only) ---------------------
    if broker is None:
        rep.notes.append("no broker: positions not recovered (shadow)")
    else:
        execs = dal.executions_between(start, end)
        signals = {s["signal_id"]: s for s in dal.signals_between(start, end)}
        by_sig: dict[str, dict] = {}
        for e in execs:
            if e.get("state") not in ("POSITION_ACTIVE",) or not e.get("signal_id"):
                continue
            g = by_sig.setdefault(e["signal_id"], {"buy_qty": 0, "buy_notional": 0.0, "sell_qty": 0, "entry_ts": None, "exec_id": None})
            q = int(e.get("filled_quantity") or 0)
            if e.get("side") == "BUY":
                g["buy_qty"] += q
                g["buy_notional"] += q * float(e.get("fill_price") or 0)
                g["entry_ts"] = g["entry_ts"] or e["ts"]
                g["exec_id"] = (e.get("details") or {}).get("execution_id") or g["exec_id"]
            else:
                g["sell_qty"] += q
        for sig_id, g in by_sig.items():
            open_qty = g["buy_qty"] - g["sell_qty"]
            if open_qty <= 0:
                continue
            srow = signals.get(sig_id)
            snap_d = (srow or {}).get("snapshot") or {}
            if not srow or "option_token" not in snap_d:
                rep.positions_skipped.append((sig_id, "no locked snapshot in journal"))
                rep.safe_to_resume = False
                continue
            snap_d = dict(snap_d)
            snap_d["ts"] = dt.datetime.fromisoformat(snap_d["ts"]) if isinstance(snap_d.get("ts"), str) else snap_d.get("ts")
            try:
                snap = SignalSnapshot(**{k: v for k, v in snap_d.items() if k in SignalSnapshot.__dataclass_fields__})
            except TypeError as exc:
                rep.positions_skipped.append((sig_id, f"snapshot unreadable: {exc}"))
                rep.safe_to_resume = False
                continue
            avg = round(g["buy_notional"] / g["buy_qty"], 2) if g["buy_qty"] else snap.option_entry
            plan = _plan_from_snapshot(snap)
            pos = book.open_position(snap, plan, g["exec_id"] or f"recovered/{sig_id}", open_qty, avg, g["entry_ts"], 0, entry_vwap=None)
            pos.exits = []  # partial exits before the restart are already in the journal; the book restarts from the open qty
            if hasattr(broker, "seed_position"):
                broker.seed_position(snap.option_token, snap.option_symbol, open_qty, avg)
            rep.positions_recovered += 1
        r = reconcile(book, broker, now)
        rep.reconciled, rep.mismatches = r.ok, r.mismatches
        if not r.ok:
            rep.safe_to_resume = False
    return rep
