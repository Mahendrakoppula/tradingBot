"""Entry point for the second bot (systemd: trading-bot-technical.service).

M1 status: SKELETON. This process loads its configuration, prints the mode
banner the spec requires (§50), validates it can never place a live order
in its current mode, and exits. The shadow loop, feed, engines and database
land in the following M1 steps - see the phase checklist in
.claude/plans/goofy-plotting-sedgewick.md. The systemd unit stays disabled
on the instance until the loop exists.
"""
import logging
import sys

from trading_bot.engine.config import EngineConfig, broker_config
from trading_bot.timeutil import now_ist

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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = EngineConfig.from_env()
    for line in banner(cfg).splitlines():
        log.info(line)

    # Resolve the broker config the way the loop will, purely to prove the
    # safety invariant at startup: outside a fully-armed LIVE mode, dry_run
    # is forced on and RestClient refuses order-mutating calls.
    bcfg = broker_config(cfg)
    if not cfg.can_place_live_orders and not bcfg.dry_run:
        log.critical("Safety invariant violated: broker config is not dry_run in %s mode - refusing to start", cfg.mode)
        return 2
    log.info("broker dry_run=%s (forced by mode)", bcfg.dry_run)
    log.info("M1 skeleton: no feed, no engines, no database yet - exiting cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
