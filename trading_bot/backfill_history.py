"""Backfill historical candles into the journal so old sessions can be replayed.

The engine only ever stored what it warmed up for: on 2026-09-29 that was 12
days of 1m and 22 of 5m, which caps a replay-based backtest at a handful of
days and leaves any statistic inside the noise band (121 live signals, 41%
target-first, nothing separable).

A probe on 2026-09-29 found the broker serves complete ONE_MINUTE days at
least 365 days back (375 bars/day) and accepts a 30-day range in one call, so
a year of 1m for one instrument is ~13 calls, not 250.

Replay needs more than 1m: the pre-signal and trend engines warm up on 5m and
30m BEFORE the replayed day, so those must reach further back than the
earliest day to be replayed or the higher-timeframe read starts blind.

Idempotent (upserts on token+tf+ts) and resumable - re-running only costs the
API calls, never duplicate rows. Writes candles and nothing else: no runs, no
signals, so it cannot disturb the live journal.
"""
import argparse
import datetime as dt
import logging
import sys

from trading_bot.auth import Session
from trading_bot.config import Config
from trading_bot.engine.config import EngineConfig, broker_config
from trading_bot.engine.db.dal import Database
from trading_bot.engine.instruments import resolve_instruments
from trading_bot.engine.ratelimit import RateLimiter
from trading_bot.engine.warmup import Instrument, fetch_history
from trading_bot.instruments import InstrumentLookup
from trading_bot.rest_client import RestClient
from trading_bot.timeutil import IST

log = logging.getLogger("trading_bot.backfill")

DEFAULT_TFS = ("1m", "5m", "30m", "1d")


def backfill(rest, dal, instruments: list[Instrument], start: dt.date, end: dt.date,
             tfs=DEFAULT_TFS, limiter: RateLimiter | None = None) -> dict:
    """Pull [start, end] for each (instrument, tf) and upsert. Returns
    {(underlying, tf): bars_written}. One timeframe failing never aborts the
    rest - a partial backfill is still worth having, and re-running fills it."""
    written: dict = {}
    a = dt.datetime.combine(start, dt.time(0, 0), tzinfo=IST)
    b = dt.datetime.combine(end, dt.time(23, 59), tzinfo=IST)
    for inst in instruments:
        for tf in tfs:
            try:
                candles = fetch_history(rest, inst, tf, a, b, limiter)
            except Exception as exc:  # noqa: BLE001
                log.error("%s %s %s..%s failed: %s", inst.underlying, tf, start, end, exc)
                written[(inst.underlying, tf)] = -1
                continue
            n = dal.upsert_candles(inst.token, inst.exchange, inst.underlying, tf, candles) if candles else 0
            written[(inst.underlying, tf)] = n
            log.info("%s %s: %d bars %s..%s", inst.underlying, tf, n, start, end)
    return written


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    p = argparse.ArgumentParser(description="Backfill historical candles for replay-based backtesting")
    p.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--tfs", default=",".join(DEFAULT_TFS))
    args = p.parse_args(argv)
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if start > end:
        p.error("--from is after --to")

    cfg = EngineConfig.from_env()
    if not cfg.database_url:
        log.error("TECH_DATABASE_URL is required")
        return 2
    bcfg = broker_config(cfg, Config.from_env())
    session = Session(bcfg)
    limiter = RateLimiter()
    session.login()
    rest = RestClient(session)
    lookup = InstrumentLookup(bcfg.scrip_master_url)
    lookup.load()
    instruments = resolve_instruments(lookup.instruments, cfg.underlyings, end, cfg.volume_proxy, cfg.future_roll_days)
    lookup.instruments = []
    dal = Database(cfg.database_url).connect()
    dal.migrate()
    try:
        written = backfill(rest, dal, instruments, start, end, tuple(t.strip() for t in args.tfs.split(",")), limiter)
    finally:
        dal.close()
    ok = sum(v for v in written.values() if v > 0)
    failed = [k for k, v in written.items() if v < 0]
    print(f"backfill {start}..{end}: {ok} bars over {len(written)} (instrument, tf) pairs"
          + (f"; FAILED {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
