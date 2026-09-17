"""PostgreSQL data-access layer (psycopg 3). One connection, autocommit
per statement - the loop writes small rows at bar cadence, so there is
nothing to batch and no transaction spans a bar.

Every write returns the id it created so the loop can link
presignal_events/signals to the context_snapshot they were computed from.
`MemoryDAL` (memory.py) mirrors this exact interface.
"""
import datetime as dt
import logging
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from trading_bot.engine.candles import Candle
from trading_bot.engine.context import ContextSnapshot
from trading_bot.engine.db.common import ENGINE_VERSION, config_hash, plain
from trading_bot.engine.db.schema_v1 import MIGRATIONS
from trading_bot.engine.presignal import StageEvent
from trading_bot.timeutil import IST

log = logging.getLogger(__name__)


def _jsonb(obj) -> Jsonb:
    return Jsonb(plain(obj))


def _norm(row: dict) -> dict:
    """Make a psycopg row look like MemoryDAL's: uuid columns as str,
    timestamptz in IST (the server hands back the SESSION timezone)."""
    out = {}
    for k, v in row.items():
        if isinstance(v, uuid.UUID):
            v = str(v)
        elif isinstance(v, dt.datetime) and v.tzinfo is not None:
            v = v.astimezone(IST)
        out[k] = v
    return out


class Database:
    def __init__(self, url: str):
        self.url = url
        self._conn: psycopg.Connection | None = None

    # --- lifecycle -------------------------------------------------------------

    def connect(self) -> "Database":
        self._conn = psycopg.connect(self.url, autocommit=True, row_factory=dict_row,
                                     options="-c TimeZone=Asia/Kolkata")
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() first")
        return self._conn

    def migrate(self) -> list[int]:
        """Apply missing schema versions in order; returns the versions applied."""
        applied: list[int] = []
        with self.conn.transaction():
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            have = {r["version"] for r in self.conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for version, sql in MIGRATIONS:
            if version in have:
                continue
            with self.conn.transaction():
                self.conn.execute(sql)
                self.conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)
            log.info("schema migration %d applied", version)
        return applied

    def ping(self) -> bool:
        return self.conn.execute("SELECT 1").fetchone() is not None

    # --- runs / config ---------------------------------------------------------------

    def get_or_create_config_version(self, payload: dict, engine_version: str = ENGINE_VERSION) -> int:
        h = config_hash(payload)
        row = self.conn.execute(
            "INSERT INTO config_versions (hash, payload, engine_version) VALUES (%s, %s, %s) "
            "ON CONFLICT (hash) DO UPDATE SET hash = EXCLUDED.hash RETURNING id",
            (h, _jsonb(payload), engine_version),
        ).fetchone()
        return int(row["id"])

    def insert_run(self, run_id: uuid.UUID | str, mode: str, started_at: dt.datetime, *, git_sha: str | None = None,
                   host: str | None = None, config_version_id: int | None = None, notes: dict | None = None) -> str:
        self.conn.execute(
            "INSERT INTO runs (run_id, mode, started_at, git_sha, host, config_version_id, notes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (str(run_id), mode, started_at, git_sha, host, config_version_id, _jsonb(notes or {})),
        )
        return str(run_id)

    def end_run(self, run_id: uuid.UUID | str, ended_at: dt.datetime, status: str = "completed", notes: dict | None = None) -> None:
        self.conn.execute(
            "UPDATE runs SET ended_at = %s, status = %s, notes = COALESCE(notes, '{}'::jsonb) || %s WHERE run_id = %s",
            (ended_at, status, _jsonb(notes or {}), str(run_id)),
        )

    # --- candles ---------------------------------------------------------------------------

    def upsert_candles(self, token: str, exchange: str, underlying: str, tf: str, candles: list[Candle],
                       run_id: uuid.UUID | str | None = None) -> int:
        if not candles:
            return 0
        rows = [
            (token, exchange, underlying, tf, c.ts, c.open, c.high, c.low, c.close, c.volume, c.oi,
             c.tick_count, c.max_gap_ms, c.complete, c.source, str(run_id) if run_id else None)
            for c in candles
        ]
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO candles (token, exchange, underlying, tf, ts, open, high, low, close, volume, oi, "
                "tick_count, max_gap_ms, complete, source, run_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (token, tf, ts) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
                "close=EXCLUDED.close, volume=EXCLUDED.volume, oi=EXCLUDED.oi, tick_count=EXCLUDED.tick_count, "
                "max_gap_ms=EXCLUDED.max_gap_ms, complete=EXCLUDED.complete, source=EXCLUDED.source, run_id=EXCLUDED.run_id",
                rows,
            )
        return len(rows)

    def last_candle_ts(self, token: str, tf: str) -> dt.datetime | None:
        row = self.conn.execute("SELECT max(ts) AS ts FROM candles WHERE token = %s AND tf = %s", (token, tf)).fetchone()
        return row["ts"].astimezone(IST) if row and row["ts"] is not None else None

    def load_candles(self, token: str, tf: str, since: dt.datetime, until: dt.datetime | None = None,
                     complete_only: bool = True) -> list[Candle]:
        q = "SELECT * FROM candles WHERE token = %s AND tf = %s AND ts >= %s"
        params: list = [token, tf, since]
        if until is not None:
            q += " AND ts < %s"
            params.append(until)
        if complete_only:
            q += " AND complete"
        q += " ORDER BY ts"
        out = []
        for r in self.conn.execute(q, params).fetchall():
            out.append(Candle(ts=r["ts"].astimezone(IST), open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                              close=float(r["close"]), volume=int(r["volume"]), oi=r["oi"], tick_count=r["tick_count"],
                              max_gap_ms=r["max_gap_ms"], complete=r["complete"], source=r["source"]))
        return out

    # --- engine records ----------------------------------------------------------------------

    def insert_context_snapshot(self, run_id: uuid.UUID | str, ctx: ContextSnapshot, config_version_id: int | None = None) -> int:
        row = self.conn.execute(
            "INSERT INTO context_snapshots (run_id, ts, underlying, trigger_tf, spot, session_phase, quality, bar_index, "
            "trends, alignment, regime, structure, levels, price_action, indicators, volume, config_version_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (str(run_id), ctx.ts, ctx.underlying, ctx.trigger_tf, ctx.spot, ctx.session_phase, ctx.quality, ctx.bar_index,
             _jsonb(ctx.trends), _jsonb(ctx.alignment), _jsonb(ctx.regime), _jsonb(ctx.structure), _jsonb(ctx.levels),
             _jsonb(ctx.price_action), _jsonb(ctx.indicators), _jsonb(ctx.volume), config_version_id),
        ).fetchone()
        return int(row["id"])

    def insert_presignal_event(self, run_id: uuid.UUID | str, ts: dt.datetime, ev: StageEvent,
                               context_snapshot_id: int | None = None) -> int:
        row = self.conn.execute(
            "INSERT INTO presignal_events (run_id, ts, underlying, setup_id, direction, from_stage, to_stage, confidence, "
            "reason_code, bar_index, details, context_snapshot_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (str(run_id), ts, ev.underlying, ev.setup_id, ev.direction, ev.from_stage, ev.to_stage, ev.confidence,
             ev.reason_code, ev.bar_index, _jsonb(ev.details), context_snapshot_id),
        ).fetchone()
        return int(row["id"])

    def insert_signal(self, run_id: uuid.UUID | str, *, signal_id: uuid.UUID | str, setup_id: str | None, ts: dt.datetime,
                      mode: str, underlying: str, direction: str, stage: str, status: str, explanation: dict,
                      snapshot: dict, option_type: str | None = None, strategy: str | None = None,
                      strategy_version: str | None = None, score: float | None = None, reason_code: str | None = None,
                      context_snapshot_id: int | None = None) -> str:
        self.conn.execute(
            "INSERT INTO signals (signal_id, setup_id, run_id, ts, mode, underlying, direction, option_type, strategy, "
            "strategy_version, stage, score, status, reason_code, explanation, snapshot, context_snapshot_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(signal_id), setup_id, str(run_id), ts, mode, underlying, direction, option_type, strategy, strategy_version,
             stage, score, status, reason_code, _jsonb(explanation), _jsonb(snapshot), context_snapshot_id),
        )
        return str(signal_id)

    # --- reads for EOD / parity ------------------------------------------------------------------

    def signals_for_run(self, run_id: uuid.UUID | str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM signals WHERE run_id = %s ORDER BY ts, underlying, direction", (str(run_id),)).fetchall()
        return [_norm(r) for r in rows]

    def presignal_events_for_run(self, run_id: uuid.UUID | str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM presignal_events WHERE run_id = %s ORDER BY ts, id", (str(run_id),)).fetchall()
        return [_norm(r) for r in rows]

    def context_count(self, run_id: uuid.UUID | str) -> int:
        return int(self.conn.execute("SELECT count(*) AS n FROM context_snapshots WHERE run_id = %s", (str(run_id),)).fetchone()["n"])
