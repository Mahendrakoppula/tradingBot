#!/bin/bash
# Nightly logical backup of the engine's PostgreSQL database (via
# trading-bot-pgdump.timer at 16:30 IST). The dump is written into
# .state/pgdump/, which the existing trading-bot-s3-sync.timer (every 30 min)
# copies to s3://<bucket>/trading-bot/historical/pgdump/ - the instance role
# may write under historical/ but not elsewhere (verified 2026-09-18: a direct
# upload to trading-bot/pgdump/ was AccessDenied). Keeps 30 local dumps; S3
# keeps everything the sync ever copied.
set -euo pipefail

OUT_DIR="/opt/trading-bot/.state/pgdump"
DB_URL="${TECH_DATABASE_URL:-}"
if [ -z "$DB_URL" ]; then
  DB_URL=$(grep '^TECH_DATABASE_URL=' /opt/trading-bot/.env | cut -d= -f2- | tr -d '"' || true)
fi
if [ -z "$DB_URL" ]; then
  echo "TECH_DATABASE_URL not configured - nothing to dump" >&2
  exit 0
fi

mkdir -p "$OUT_DIR"
STAMP=$(TZ=Asia/Kolkata date +%Y-%m-%d)
OUT="$OUT_DIR/tradingbot-${STAMP}.dump"
pg_dump --format=custom --no-owner --file="$OUT.tmp" "$DB_URL"
mv "$OUT.tmp" "$OUT"
ls -1t "$OUT_DIR"/tradingbot-*.dump | tail -n +31 | xargs -r rm -f
echo "pg_dump written: $OUT ($(du -h "$OUT" | cut -f1)); the s3-sync timer uploads .state/ within 30 min"
