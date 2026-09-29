"""Replay a date range of stored candles through the live decision loop.

`run_technical.run_replay` already replays ONE day (TECH_REPLAY_DATE) through
the identical pipeline against the journal. This drives it over a range so a
backfilled history becomes a few thousand journaled signals instead of the
121 the live sessions produced - the sample the 2026-09-29 analysis needed and
did not have (every slice sat inside the 95% band).

What a replay can and cannot tell you, stated once so no one over-reads the
output: there are no option quotes for a historical day, so every decision
stops at OPTION_SELECTION. You get pre-signal, routing, scoring and no-chase
with their reasons - the same population the counterfactuals measure - and
NOT fills, costs or sizing. Only days with option_chain_snapshots can ever
support those.

Point TECH_DATABASE_URL at a SEPARATE database. Replay writes runs and signals
like any session; mixing thousands of BACKTEST rows into the live PAPER
journal would silently corrupt every dated query the review tooling makes.
"""
import argparse
import datetime as dt
import logging
import os
import sys
import time

from trading_bot.engine.clock import is_trading_day
from trading_bot.engine.config import EngineConfig

log = logging.getLogger("trading_bot.replay_range")


def trading_days(start: dt.date, end: dt.date, holidays=()) -> list[dt.date]:
    out, day = [], start
    while day <= end:
        if is_trading_day(day, holidays):
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def replay_days(days: list[dt.date], run_one, on_day=None) -> dict:
    """Call `run_one(day)` for each day. A day with no stored candles returns
    non-zero and is counted as skipped rather than aborting the range - an
    early gap in the backfill should not cost the rest of the run."""
    stats = {"days": len(days), "ok": 0, "skipped": 0, "failed": 0}
    for day in days:
        os.environ["TECH_REPLAY_DATE"] = day.isoformat()
        started = time.monotonic()
        try:
            rc = run_one(day)
        except Exception as exc:  # noqa: BLE001 - one bad day must not end the range
            log.error("replay %s raised: %r", day, exc)
            stats["failed"] += 1
            continue
        took = time.monotonic() - started
        if rc == 0:
            stats["ok"] += 1
        elif rc == 2:
            stats["skipped"] += 1  # no candles stored for that day
        else:
            stats["failed"] += 1
        log.info("replay %s rc=%s (%.1fs) [ok=%d skipped=%d failed=%d]",
                 day, rc, took, stats["ok"], stats["skipped"], stats["failed"])
        if on_day is not None:
            on_day(day, rc, took)
    return stats


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    p = argparse.ArgumentParser(description="Replay a date range through the decision pipeline")
    p.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--dry-run", action="store_true", help="list the trading days and exit")
    args = p.parse_args(argv)
    start, end = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    if start > end:
        p.error("--from is after --to")

    cfg = EngineConfig.from_env()
    days = trading_days(start, end, cfg.holidays)
    if args.dry_run:
        print(f"{len(days)} trading days: {days[0]} .. {days[-1]}" if days else "no trading days in range")
        return 0
    if cfg.mode not in ("BACKTEST", "RESEARCH"):
        log.error("TECH_MODE must be BACKTEST or RESEARCH to replay (got %s) - and point TECH_DATABASE_URL "
                  "at a separate database so this never mixes with the live journal", cfg.mode)
        return 2
    if not cfg.database_url:
        log.error("TECH_DATABASE_URL is required")
        return 2

    from trading_bot import run_technical

    def run_one(_day: dt.date) -> int:
        return run_technical.run_replay(EngineConfig.from_env())

    t0 = time.monotonic()
    stats = replay_days(days, run_one)
    print(f"replayed {stats['ok']}/{stats['days']} days in {time.monotonic() - t0:.0f}s "
          f"(skipped {stats['skipped']} with no stored candles, failed {stats['failed']})")
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
