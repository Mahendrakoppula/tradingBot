"""Phase 2 entrypoint: backfill spot OHLCV history for the three in-scope
indices, across every interval the multi-timeframe engine (Phase 3) will
need. Run manually (`python -m data.pull_history`), not part of the
always-running app/main.py service - a backfill is an occasional,
deliberate operation, not something that should run on every process
start.

Never saves data that failed quality checks with ERROR-level issues -
per the spec's "do not pretend the data exists / is clean if it isn't"
principle, a loud failure here is strictly better than silently
persisting bad history that a later phase's backtester/ML pipeline would
then trust.
"""
import datetime as dt
import logging

from config.settings import get_settings
from data.broker_client import BrokerConfig, HistoricalDataClient, Session
from data.historical import fetch_history, last_completed_trading_date
from data.instrument_lookup import InstrumentLookup, find_spot_instrument
from data.quality import check_ohlcv
from data.storage import save_ohlcv
from monitoring.logging_setup import setup_logging

log = logging.getLogger("codex.data.pull_history")

INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]

# Lookback window per interval - long enough for the phases that will
# consume this (regime/MTF features, walk-forward backtesting) without
# requesting more than SmartAPI's historical minute-data availability
# realistically holds. Tunable; not a correctness-critical constant.
LOOKBACK_DAYS_BY_INTERVAL = {
    "ONE_MINUTE": 60,
    "FIVE_MINUTE": 120,
    "TEN_MINUTE": 120,
    "THIRTY_MINUTE": 365,
    "ONE_HOUR": 730,
    "ONE_DAY": 1825,
}


def run() -> None:
    setup_logging()
    settings = get_settings()
    if not (settings.smartapi_key and settings.smartapi_client_code and settings.smartapi_pin and settings.smartapi_totp_secret):
        log.error("SmartAPI credentials not configured (SMARTAPI_KEY/CLIENT_CODE/PIN/TOTP_SECRET) - cannot backfill.")
        return

    broker_cfg = BrokerConfig.from_settings(settings)
    session = Session(broker_cfg)
    session.login()
    client = HistoricalDataClient(session)

    try:
        lookup = InstrumentLookup(broker_cfg.scrip_master_url)
        lookup.load()

        end = last_completed_trading_date()
        for name in INDICES:
            try:
                instrument = find_spot_instrument(lookup.instruments, name)
            except LookupError as e:
                log.error("Skipping %s: %s", name, e)
                continue
            exchange = instrument["exch_seg"]
            token = instrument["token"]
            log.info("=== %s (exchange=%s token=%s) ===", name, exchange, token)

            for interval, lookback_days in LOOKBACK_DAYS_BY_INTERVAL.items():
                start = end - dt.timedelta(days=lookback_days)
                rows = fetch_history(client, exchange, token, interval, start, end)
                report = check_ohlcv(rows, symbol=name, interval=interval)
                if report.warnings:
                    for w in report.warnings:
                        log.warning("[%s %s] %s", name, interval, w.message)
                if not report.is_clean:
                    for e in report.errors:
                        log.error("[%s %s] %s", name, interval, e.message)
                    log.error("%s %s: %d error(s) - NOT saving", name, interval, len(report.errors))
                    continue
                save_ohlcv(rows, name, interval)
    finally:
        session.logout()


if __name__ == "__main__":
    run()
