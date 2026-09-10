"""Phase 1 entry point: the health-check skeleton every later phase
builds on. Deliberately does nothing trading-related yet - loads config,
sets up logging, confirms the database is reachable, sends a startup
Telegram notification, and idles. This is the concrete, observable proof
the whole chain (config -> secrets -> Docker -> network -> Telegram/DB)
actually works before any trading logic exists to obscure a plumbing
failure - see the Phase 1 plan's own Verification section.
"""
import logging
import time

import psycopg2

from config.settings import get_settings
from monitoring.logging_setup import setup_logging
from monitoring.notifier import notify

log = logging.getLogger("codex.app")

HEALTH_CHECK_INTERVAL_SECONDS = 60


def check_database(database_url: str) -> bool:
    try:
        conn = psycopg2.connect(database_url, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        finally:
            conn.close()
    except Exception:
        log.exception("Database health check failed")
        return False


def main() -> None:
    settings = get_settings()
    setup_logging()
    log.info(
        "Starting codex Phase 1 health-check skeleton (environment=%s, dry_run=%s, instruments=%s)",
        settings.environment, settings.dry_run, settings.instruments,
    )

    db_ok = check_database(settings.database_url)
    status = "reachable" if db_ok else "UNREACHABLE"
    log.info("Database status: %s", status)

    notify(
        f"\U0001F7E2 <b>CODEX STARTED</b> (Phase 1 health check)\n"
        f"Environment: {settings.environment} | Database: {status}",
        html=True,
    )

    while True:
        time.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
        db_ok = check_database(settings.database_url)
        log.info("Health check: database %s", "ok" if db_ok else "FAILED")


if __name__ == "__main__":
    main()
