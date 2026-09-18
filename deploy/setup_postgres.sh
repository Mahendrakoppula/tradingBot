#!/bin/bash
# One-time PostgreSQL 16 install on the EC2 box (Amazon Linux 2023), run as
# root via SSM Run Command - see deploy/DEPLOY.md section 8. Idempotent.
#
# Auth model: the bots run as OS user `tradingbot`, so the `tradingbot`
# database role authenticates with PEER auth over the Unix socket - the
# kernel vouches for the identity, there is no password and nothing secret
# to store. TECH_DATABASE_URL is therefore non-secret config:
#     postgresql:///tradingbot?host=/var/run/postgresql
# (An optional password can be passed as $1 for TCP clients; not needed.)
#
# Sizing: reads MemTotal so the same script is safe on the current 1 GB
# t3.micro (small shared_buffers) and right on a 2 GB t3.small. On a box
# under 2 GB it also adds a 1 GB swapfile first so the package install and
# initdb can never OOM the running bots.
set -euo pipefail

DB_PASSWORD="${1:-}"
MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
MEM_MB=$((MEM_KB / 1024))

if [ "$MEM_MB" -lt 2000 ] && [ "$(swapon --show --noheadings | wc -l)" -eq 0 ]; then
  echo "RAM ${MEM_MB}MB and no swap: adding a 1G swapfile"
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 >/dev/null
fi

if ! command -v psql >/dev/null 2>&1; then
  dnf install -y -q postgresql16 postgresql16-server
fi

PGDATA=/var/lib/pgsql/data
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  postgresql-setup --initdb >/dev/null
fi

if [ "$MEM_MB" -lt 2000 ]; then
  SHARED=32MB; EFFECTIVE=256MB; MAXCONN=16
else
  SHARED=128MB; EFFECTIVE=512MB; MAXCONN=32
fi
CONF="$PGDATA/postgresql.conf"
sed -i '/^# trading-bot tuning/,/^# end trading-bot tuning/d' "$CONF"
cat >> "$CONF" <<PGCONF
# trading-bot tuning (deploy/setup_postgres.sh, MemTotal=${MEM_MB}MB)
shared_buffers = ${SHARED}
work_mem = 4MB
maintenance_work_mem = 32MB
effective_cache_size = ${EFFECTIVE}
max_connections = ${MAXCONN}
timezone = 'Asia/Kolkata'
log_timezone = 'Asia/Kolkata'
listen_addresses = 'localhost'
# end trading-bot tuning
PGCONF

HBA="$PGDATA/pg_hba.conf"
if ! grep -q "trading-bot" "$HBA"; then
  # our rules must come BEFORE the stock 'local all all peer' line; prepend
  TMP=$(mktemp)
  cat > "$TMP" <<'HBACONF'
# trading-bot (deploy/setup_postgres.sh): peer auth over the socket for the bots' OS user,
# scram over localhost TCP if a password is ever set
local   tradingbot      tradingbot                              peer
host    tradingbot      tradingbot      127.0.0.1/32            scram-sha-256
host    tradingbot      tradingbot      ::1/128                 scram-sha-256
HBACONF
  cat "$HBA" >> "$TMP"
  cp "$TMP" "$HBA"
  rm -f "$TMP"
  chown postgres:postgres "$HBA"
fi

systemctl enable --now postgresql >/dev/null
sleep 2

if [ -n "$DB_PASSWORD" ]; then
  ROLE_SQL="CREATE ROLE tradingbot LOGIN PASSWORD '${DB_PASSWORD}'"
  ALTER_SQL="ALTER ROLE tradingbot WITH LOGIN PASSWORD '${DB_PASSWORD}'"
else
  ROLE_SQL="CREATE ROLE tradingbot LOGIN"
  ALTER_SQL="ALTER ROLE tradingbot WITH LOGIN"
fi
sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tradingbot') THEN
    ${ROLE_SQL};
  ELSE
    ${ALTER_SQL};
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE tradingbot OWNER tradingbot'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'tradingbot') \gexec
SQL

systemctl reload postgresql
# prove peer auth works for the bots' user
sudo -u tradingbot psql "postgresql:///tradingbot?host=/var/run/postgresql" -Atc "SELECT current_user || ' @ ' || version();"
echo "PostgreSQL ready (shared_buffers=${SHARED}). TECH_DATABASE_URL=postgresql:///tradingbot?host=/var/run/postgresql"
