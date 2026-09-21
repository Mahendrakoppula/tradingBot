#!/bin/bash
# Creates the MCX crude-oil spike's own database (tradingbot_mcx, peer auth
# over the socket like the index engine's) on a box where
# deploy/setup_postgres.sh has already run. Idempotent; run as root by
# redeploy.sh / user_data.sh only when deploy/mcx.env has
# TECH_MCX_ENABLED=true. The engine applies its own schema on first start.
set -euo pipefail

if ! command -v psql >/dev/null 2>&1; then
  echo "PostgreSQL is not installed - run deploy/setup_postgres.sh first" >&2
  exit 1
fi

PGDATA=/var/lib/pgsql/data
HBA="$PGDATA/pg_hba.conf"
if ! grep -q "tradingbot_mcx" "$HBA"; then
  TMP=$(mktemp)
  cat > "$TMP" <<'HBACONF'
# trading-bot MCX spike (deploy/ensure_mcx_db.sh): same peer auth, separate database
local   tradingbot_mcx  tradingbot                              peer
HBACONF
  cat "$HBA" >> "$TMP"
  cp "$TMP" "$HBA"
  rm -f "$TMP"
  chown postgres:postgres "$HBA"
  systemctl reload postgresql
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<'SQL'
SELECT 'CREATE DATABASE tradingbot_mcx OWNER tradingbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'tradingbot_mcx') \gexec
SQL

sudo -u tradingbot psql "postgresql:///tradingbot_mcx?host=/var/run/postgresql" -Atc "SELECT 'tradingbot_mcx ready as ' || current_user;"
