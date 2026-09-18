"""In-process DAL with the same interface as `dal.Database`, for tests,
BACKTEST replay and the §51 parity check (diff two MemoryDAL journals).

Rows are stored as plain dicts in the same shape Postgres returns them,
so a consumer written against Database works unchanged.
"""
import copy
import datetime as dt

from trading_bot.engine.candles import Candle
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.db.common import ENGINE_VERSION, config_hash
from trading_bot.engine.db.common import plain as _plain
from trading_bot.engine.presignal import StageEvent


class MemoryDAL:
    def __init__(self):
        self.config_versions: list[dict] = []
        self.runs: dict[str, dict] = {}
        self.candles: dict[tuple[str, str, dt.datetime], dict] = {}
        self.context_snapshots: list[dict] = []
        self.presignal_events: list[dict] = []
        self.signals: list[dict] = []
        self.risk_decisions: list[dict] = []
        self.executions: list[dict] = []
        self.trade_results: list[dict] = []
        self.kill_switch_events: list[dict] = []
        self.option_chain_snapshots: list[dict] = []
        self.migrated: list[int] = []

    # --- lifecycle ---------------------------------------------------------------

    def connect(self) -> "MemoryDAL":
        return self

    def close(self) -> None:
        pass

    def migrate(self) -> list[int]:
        self.migrated = [1]
        return [1]

    def ping(self) -> bool:
        return True

    # --- runs / config ---------------------------------------------------------------

    def get_or_create_config_version(self, payload: dict, engine_version: str = ENGINE_VERSION) -> int:
        h = config_hash(payload)
        for row in self.config_versions:
            if row["hash"] == h:
                return row["id"]
        row = {"id": len(self.config_versions) + 1, "hash": h, "payload": _plain(payload), "engine_version": engine_version}
        self.config_versions.append(row)
        return row["id"]

    def insert_run(self, run_id, mode, started_at, *, git_sha=None, host=None, config_version_id=None, notes=None) -> str:
        self.runs[str(run_id)] = {"run_id": str(run_id), "mode": mode, "started_at": started_at, "ended_at": None,
                                  "git_sha": git_sha, "host": host, "config_version_id": config_version_id,
                                  "status": "running", "notes": _plain(notes or {})}
        return str(run_id)

    def end_run(self, run_id, ended_at, status="completed", notes=None) -> None:
        r = self.runs[str(run_id)]
        r["ended_at"], r["status"] = ended_at, status
        r["notes"] = {**(r["notes"] or {}), **_plain(notes or {})}

    # --- candles -------------------------------------------------------------------------

    def upsert_candles(self, token, exchange, underlying, tf, candles: list[Candle], run_id=None) -> int:
        for c in candles:
            self.candles[(token, tf, c.ts)] = {
                "token": token, "exchange": exchange, "underlying": underlying, "tf": tf, "ts": c.ts,
                "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume, "oi": c.oi,
                "tick_count": c.tick_count, "max_gap_ms": c.max_gap_ms, "complete": c.complete, "source": c.source,
                "run_id": str(run_id) if run_id else None,
            }
        return len(candles)

    def last_candle_ts(self, token, tf):
        ts = [k[2] for k in self.candles if k[0] == token and k[1] == tf]
        return max(ts) if ts else None

    def load_candles(self, token, tf, since, until=None, complete_only=True) -> list[Candle]:
        rows = [r for (t, f, ts), r in self.candles.items() if t == token and f == tf and ts >= since
                and (until is None or ts < until) and (not complete_only or r["complete"])]
        rows.sort(key=lambda r: r["ts"])
        return [Candle(ts=r["ts"], open=r["open"], high=r["high"], low=r["low"], close=r["close"], volume=r["volume"],
                       oi=r["oi"], tick_count=r["tick_count"], max_gap_ms=r["max_gap_ms"], complete=r["complete"],
                       source=r["source"]) for r in rows]

    # --- engine records ---------------------------------------------------------------------

    def insert_context_snapshot(self, run_id, ctx: ContextSnapshot, config_version_id=None) -> int:
        row = {"id": len(self.context_snapshots) + 1, "run_id": str(run_id), **_plain(ctx.to_dict()),
               "config_version_id": config_version_id}
        row["ts"] = ctx.ts
        self.context_snapshots.append(row)
        return row["id"]

    def insert_presignal_event(self, run_id, ts, ev: StageEvent, context_snapshot_id=None) -> int:
        row = {"id": len(self.presignal_events) + 1, "run_id": str(run_id), "ts": ts, "underlying": ev.underlying,
               "setup_id": ev.setup_id, "direction": ev.direction, "from_stage": ev.from_stage, "to_stage": ev.to_stage,
               "confidence": ev.confidence, "reason_code": ev.reason_code, "bar_index": ev.bar_index,
               "details": _plain(ev.details), "context_snapshot_id": context_snapshot_id}
        self.presignal_events.append(row)
        return row["id"]

    def insert_signal(self, run_id, *, signal_id, setup_id, ts, mode, underlying, direction, stage, status, explanation,
                      snapshot, option_type=None, strategy=None, strategy_version=None, score=None, reason_code=None,
                      context_snapshot_id=None) -> str:
        self.signals.append({
            "signal_id": str(signal_id), "setup_id": setup_id, "run_id": str(run_id), "ts": ts, "mode": mode,
            "underlying": underlying, "direction": direction, "option_type": option_type, "strategy": strategy,
            "strategy_version": strategy_version, "stage": stage, "score": score, "status": status,
            "reason_code": reason_code, "explanation": _plain(explanation), "snapshot": _plain(snapshot),
            "context_snapshot_id": context_snapshot_id,
        })
        return str(signal_id)

    # --- M2 records -----------------------------------------------------------------------------------

    def update_signal_status(self, signal_id, status, reason_code=None) -> None:
        for s in self.signals:
            if s["signal_id"] == str(signal_id):
                s["status"] = status
                if reason_code is not None:
                    s["reason_code"] = reason_code

    def insert_risk_decision(self, run_id, signal_id, ts, *, decision, reason_code, risk_amount, quantity, all_in_cost,
                             expected_value, details) -> int:
        self.risk_decisions.append({"id": len(self.risk_decisions) + 1, "run_id": str(run_id),
                                    "signal_id": str(signal_id) if signal_id else None, "ts": ts, "decision": decision,
                                    "reason_code": reason_code, "risk_amount": risk_amount, "quantity": quantity,
                                    "all_in_cost": all_in_cost, "expected_value": expected_value, "details": _plain(details)})
        return self.risk_decisions[-1]["id"]

    def insert_execution(self, run_id, signal_id, ts, *, mode, side, state, broker_order_id, ordertag, requested_price, fill_price,
                         quantity, filled_quantity, latency_ms, slippage, details) -> int:
        self.executions.append({"id": len(self.executions) + 1, "run_id": str(run_id),
                                "signal_id": str(signal_id) if signal_id else None, "ts": ts, "mode": mode, "side": side,
                                "state": state, "broker_order_id": broker_order_id, "ordertag": ordertag,
                                "requested_price": requested_price, "fill_price": fill_price, "quantity": quantity,
                                "filled_quantity": filled_quantity, "latency_ms": latency_ms, "slippage": slippage,
                                "details": _plain(details)})
        return self.executions[-1]["id"]

    def insert_trade_result(self, run_id, signal_id, *, entry_ts, exit_ts, entry_price, exit_price, quantity, gross_pnl, costs,
                            net_pnl, r_multiple, exit_reason, mae, mfe, details) -> int:
        self.trade_results.append({"id": len(self.trade_results) + 1, "run_id": str(run_id),
                                   "signal_id": str(signal_id) if signal_id else None, "entry_ts": entry_ts, "exit_ts": exit_ts,
                                   "entry_price": entry_price, "exit_price": exit_price, "quantity": quantity,
                                   "gross_pnl": gross_pnl, "costs": costs, "net_pnl": net_pnl, "r_multiple": r_multiple,
                                   "exit_reason": exit_reason, "mae": mae, "mfe": mfe, "details": _plain(details)})
        return self.trade_results[-1]["id"]

    def insert_kill_switch_event(self, run_id, ts, *, switch, action, reason, details) -> int:
        self.kill_switch_events.append({"id": len(self.kill_switch_events) + 1, "run_id": str(run_id), "ts": ts,
                                        "switch": switch, "action": action, "reason": reason, "details": _plain(details)})
        return self.kill_switch_events[-1]["id"]

    def insert_option_chain_snapshots(self, run_id, ts, underlying, rows) -> int:
        for r in rows:
            self.option_chain_snapshots.append({"run_id": str(run_id), "ts": ts, "underlying": underlying, **_plain(r)})
        return len(rows)

    def trade_results_for_run(self, run_id) -> list[dict]:
        return [copy.deepcopy(t) for t in self.trade_results if t["run_id"] == str(run_id)]

    def risk_decisions_for_run(self, run_id) -> list[dict]:
        return [copy.deepcopy(r) for r in self.risk_decisions if r["run_id"] == str(run_id)]

    # --- reads --------------------------------------------------------------------------------------

    def signals_for_run(self, run_id) -> list[dict]:
        rows = [copy.deepcopy(s) for s in self.signals if s["run_id"] == str(run_id)]
        rows.sort(key=lambda r: (r["ts"], r["underlying"], r["direction"]))
        return rows

    def presignal_events_for_run(self, run_id) -> list[dict]:
        return [copy.deepcopy(e) for e in self.presignal_events if e["run_id"] == str(run_id)]

    def context_count(self, run_id) -> int:
        return sum(1 for c in self.context_snapshots if c["run_id"] == str(run_id))
