"""Entry point for the second bot (systemd: trading-bot-technical.service).

M1: SHADOW analysis bot. Warms up history over REST, streams ticks over
WebSocket, aggregates candles, runs the trend/regime/structure/pre-signal
engines at every 5m close, journals everything to Postgres, and logs
would-be signals with a full explanation. It places NO orders: the broker
config is forced dry_run outside a fully-armed LIVE mode, and nothing
under trading_bot.engine can import an order call (AST-tested).

Modes (§51): SHADOW/PAPER/LIVE run the live loop (M1 treats all three as
shadow - there is no execution layer yet); BACKTEST replays a stored day
through the same loop (`TECH_REPLAY_DATE=YYYY-MM-DD`); RESEARCH is BACKTEST
without Telegram.
"""
import datetime as dt
import logging
import os
import queue
import signal
import subprocess
import sys

from trading_bot import technical_notifier
from trading_bot.auth import Session
from trading_bot.engine import jsonlog
from trading_bot.engine.candles import CandleStore
from trading_bot.engine.clock import is_trading_day, session_close_at, session_open_at
from trading_bot.engine.config import EngineConfig, broker_config
from trading_bot.engine.db.dal import Database
from trading_bot.engine.db.memory import MemoryDAL
from trading_bot.engine.feed import LiveTickSource, ResilientMarketStream, Subscription, smartapi_stream_factory
from trading_bot.engine.instruments import resolve_instruments, ws_type
from trading_bot.engine.ratelimit import RateLimiter
from trading_bot.engine.replay import SimClock, TickReplaySource
from trading_bot.engine.shadow import ShadowLoop
from trading_bot.engine.warmup import WarmupPlan, backfill_today_1m, warm_up
from trading_bot.instruments import InstrumentLookup
from trading_bot.rest_client import RestClient
from trading_bot.timeutil import IST, now_ist, today_ist

log = logging.getLogger("trading_bot.technical")


def banner(cfg: EngineConfig) -> str:
    live = "LIVE ORDER PLACEMENT ENABLED" if cfg.can_place_live_orders else "LIVE ORDER PLACEMENT DISABLED"
    return (
        f"=== {cfg.mode} MODE | {live} ===\n"
        f"underlyings={','.join(cfg.underlyings)} capital=Rs.{cfg.capital:,.0f} "
        f"risk/trade={cfg.risk_per_trade_pct:.2%} daily-cap={cfg.daily_loss_cap_pct:.2%} "
        f"eod-cutoff={cfg.eod_cutoff:%H:%M} session-end={cfg.session_end:%H:%M} "
        f"db={'configured' if cfg.database_url else 'NOT CONFIGURED'} now={now_ist():%Y-%m-%d %H:%M:%S %Z}"
    )


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                              check=False).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


class _Notifier:
    def notify(self, message: str, *, html: bool = False) -> None:
        technical_notifier.notify(message, html=html)


def _open_dal(cfg: EngineConfig):
    if not cfg.database_url:
        log.warning("TECH_DATABASE_URL not set - journaling to memory only (nothing persists past this process)")
        return MemoryDAL().connect()
    dal = Database(cfg.database_url).connect()
    applied = dal.migrate()
    log.info("database connected; migrations applied: %s", applied or "none (up to date)")
    return dal


def run_live(cfg: EngineConfig) -> int:
    today = today_ist()
    if not is_trading_day(today, cfg.holidays):
        log.info("%s is not a trading day - exiting", today)
        return 0
    bcfg = broker_config(cfg)
    session = Session(bcfg)
    session.login()
    rest = RestClient(session)
    limiter = RateLimiter()

    lookup = InstrumentLookup(bcfg.scrip_master_url)
    lookup.load()
    instruments = resolve_instruments(lookup.instruments, cfg.underlyings, today, cfg.volume_proxy)
    lookup.instruments = []  # drop the scrip master; only the resolved tokens are needed
    lookup._by_symbol = {}

    stores: dict[tuple[str, str], CandleStore] = {}
    t0 = now_ist()
    report = warm_up(rest, instruments, WarmupPlan.from_config(cfg), stores, t0, limiter)
    jsonlog.event("warmup", "done", calls=report.calls, seconds=round((now_ist() - t0).total_seconds(), 1),
                  bars={f"{k[0]}/{k[1]}": v for k, v in report.bars.items()}, errors=report.errors)
    if report.errors:
        log.warning("warmup errors: %s", report.errors)

    dal = _open_dal(cfg)
    _persist_history(dal, instruments, stores)
    notifier = _Notifier()
    q: queue.Queue = queue.Queue(maxsize=50_000)
    subs = [Subscription(ws_type(i.exchange), (i.token,)) for i in instruments]
    stream = ResilientMarketStream(smartapi_stream_factory(session), subs, q, backoff_max=cfg.feed_backoff_max_seconds)
    source = LiveTickSource(stream, q)

    def _backfill() -> None:
        # feed is up: close the restart gap with today's 1m over REST, once
        now = now_ist()
        if now <= session_open_at(today):
            return
        for inst in instruments:
            store = stores.setdefault((inst.token, "1m"), CandleStore("1m"))
            try:
                n = backfill_today_1m(rest, inst, store, now, limiter)
            except Exception as exc:  # noqa: BLE001 - a missing backfill is a GAP, not a fatal error
                jsonlog.event("warmup", "backfill_failed", severity="WARN", underlying=inst.underlying,
                              token=inst.token, error=repr(exc))
                log.warning("backfill %s (%s) failed - continuing with a gap: %s", inst.underlying, inst.token, exc)
                continue
            jsonlog.event("warmup", "backfill_today", underlying=inst.underlying, token=inst.token, bars=n)

    loop = ShadowLoop(cfg, dal, instruments, source, now_ist, notifier, stores, git_sha=_git_sha(),
                      after_feed_start=_backfill)

    def _sigterm(signum, frame):
        log.warning("signal %s received - stopping loop", signum)
        loop.stop()

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    try:
        notifier.notify(banner(cfg).replace("===", "").strip())
    except Exception as exc:  # noqa: BLE001
        log.warning("startup notify failed: %s", exc)

    try:
        stats = loop.run()
    finally:
        dal.close()
    log.info("session complete: %s", vars(stats))
    return 0


def _persist_history(dal, instruments, stores) -> None:
    """Warmup bars newer than what the database already holds, so BACKTEST
    replays can rebuild the same indicator history the live run saw."""
    for inst in instruments:
        for tf in ("1m", "5m", "30m", "1d"):
            st = stores.get((inst.token, tf))
            if st is None:
                continue
            last = dal.last_candle_ts(inst.token, tf)
            fresh = [c for c in st if last is None or c.ts > last]
            if fresh:
                n = dal.upsert_candles(inst.token, inst.exchange, inst.underlying, tf, fresh)
                log.info("persisted %d new %s bars for %s", n, tf, inst.underlying)


def run_replay(cfg: EngineConfig) -> int:
    """BACKTEST/RESEARCH: replay TECH_REPLAY_DATE's stored 1m candles
    through the same loop against the same database (a new run_id, mode
    BACKTEST) - the §51 parity check is a diff of two run_ids."""
    day = dt.date.fromisoformat(os.environ["TECH_REPLAY_DATE"])
    if not cfg.database_url:
        log.error("BACKTEST needs TECH_DATABASE_URL (candles are read from the database)")
        return 2
    dal = Database(cfg.database_url).connect()
    dal.migrate()
    instruments = _instruments_from_db(dal, cfg.underlyings, day)
    if not instruments:
        log.error("no candles for %s in the database", day)
        return 2
    stores: dict[tuple[str, str], CandleStore] = {}
    today_1m: dict[str, list] = {}
    since = dt.datetime.combine(day - dt.timedelta(days=400), dt.time(9, 15), tzinfo=IST)
    for inst in instruments:
        for tf in ("1m", "5m", "30m", "1d"):
            hist = dal.load_candles(inst.token, tf, since, until=session_open_at(day))
            st = CandleStore(tf)
            st.extend(hist)
            stores[(inst.token, tf)] = st
        today_1m[inst.token] = dal.load_candles(inst.token, "1m", session_open_at(day), until=session_close_at(day))
    clock = SimClock(dt.datetime.combine(day, dt.time(9, 0), tzinfo=IST))
    source = TickReplaySource(today_1m, clock, day)
    notifier = _Notifier() if cfg.mode == "BACKTEST" else None
    loop = ShadowLoop(cfg, dal, instruments, source, clock.now, notifier, stores, git_sha=_git_sha())
    try:
        stats = loop.run()
    finally:
        dal.close()
    log.info("replay complete: run_id=%s %s", loop.run_id, vars(stats))
    return 0


def _instruments_from_db(dal: Database, underlyings, day: dt.date):
    from trading_bot.engine.warmup import Instrument
    rows = dal.conn.execute(
        "SELECT DISTINCT token, exchange, underlying FROM candles WHERE tf = '1m' AND ts >= %s AND ts < %s",
        (session_open_at(day), session_close_at(day)),
    ).fetchall()
    out = []
    for r in rows:
        if r["underlying"] not in underlyings:
            continue
        role = "spot" if r["exchange"] in ("NSE", "BSE") else "volume_proxy"
        out.append(Instrument(r["underlying"], r["exchange"], r["token"], role))
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    cfg = EngineConfig.from_env()
    for line in banner(cfg).splitlines():
        log.info(line)
    bcfg = broker_config(cfg)
    if not cfg.can_place_live_orders and not bcfg.dry_run:
        log.critical("Safety invariant violated: broker config is not dry_run in %s mode - refusing to start", cfg.mode)
        return 2
    log.info("broker dry_run=%s (forced by mode)", bcfg.dry_run)
    if cfg.mode in ("BACKTEST", "RESEARCH"):
        return run_replay(cfg)
    if cfg.mode == "LIVE":
        # M1 has no execution layer: LIVE behaves as SHADOW and says so loudly
        log.warning("LIVE mode requested but M1 has no execution layer - running the shadow loop, zero orders")
    return run_live(cfg)


if __name__ == "__main__":
    sys.exit(main())
