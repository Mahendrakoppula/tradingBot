#!/bin/bash
# One-time PostgreSQL 16 install on the EC2 box (Amazon Linux 2023), run
# as root via SSM Run Command OUTSIDE market hours - see deploy/DEPLOY.md
# §8. Idempotent: re-running is safe.
#
# Creates the `tradingbot` role + `tradingbot` database the second bot's
# engine journals to (schema is applied by the bot itself at startup via
# trading_bot/engine/db/schema_v1.py). Local-socket + localhost only; the
# instance's security group already exposes nothing but SSM.
#
# Sizing for a t3.small (2 GB) shared with two bot processes: 128 MB
# shared_buffers, 32 max connections. The bot opens ONE connection.
set -euo pipefail

DB_PASSWORD="${1:?usage: setup_postgres.sh <db-password>  (the same value goes into SSM /trading-bot/TECH_DATABASE_URL)}"

if ! command -v psql >/dev/null 2>&1; then
  dnf install -y postgresql16 postgresql16-server
fi

PGDATA=/var/lib/pgsql/data
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  postgresql-setup --initdb
fi

# conservative memory footprint; only touch settings we own (marker comment)
CONF="$PGDATA/postgresql.conf"
if ! grep -q "# trading-bot tuning" "$CONF"; then
  cat >> "$CONF" <<'PGCONF'

# trading-bot tuning (deploy/setup_postgres.sh) - t3.small shared box
shared_buffers = 128MB
work_mem = 4MB
maintenance_work_mem = 32MB
effective_cache_size = 512MB
max_connections = 32
timezone = 'Asia/Kolkata'
log_timezone = 'Asia/Kolkata'
PGCONF
fi

# password auth over localhost TCP (the bot connects via postgresql://...@127.0.0.1)
HBA="$PGDATA/pg_hba.conf"
if ! grep -q "trading-bot" "$HBA"; then
  cat >> "$HBA" <<'HBACONF'
# trading-bot (deploy/setup_postgres.sh)
host    tradingbot      tradingbot      127.0.0.1/32            scram-sha-256
host    tradingbot      tradingbot      ::1/128                 scram-sha-256
HBACONF
fi

systemctl enable --now postgresql
sleep 2

sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tradingbot') THEN
    CREATE ROLE tradingbot LOGIN PASSWORD '${DB_PASSWORD}';
  ELSE
    ALTER ROLE tradingbot WITH PASSWORD '${DB_PASSWORD}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE tradingbot OWNER tradingbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'tradingbot') \gexec
SQL

systemctl reload postgresql
sudo -u postgres psql -c "SELECT version();" | head -3
echo "PostgreSQL ready. Now put postgresql://tradingbot:<password>@127.0.0.1:5432/tradingbot into SSM as /trading-bot/TECH_DATABASE_URL (SecureString)."
