#!/bin/bash
# Nightly logical backup of the engine's PostgreSQL database to S3 (via
# trading-bot-pgdump.timer at 16:30 IST, before the 18:00 IST instance
# stop). The local EBS volume is the only copy otherwise. Keeps the last
# 30 daily dumps in the bucket (older ones are deleted here, not by a
# lifecycle rule, so the retention is visible in the repo).
set -euo pipefail

DEPLOY_BUCKET="${DEPLOY_BUCKET:-trading-bot-deploy-396913392704}"
DB_URL="${TECH_DATABASE_URL:-}"
if [ -z "$DB_URL" ]; then
  # .env is assembled by fetch_secrets.sh; source only the one line we need
  DB_URL=$(grep '^TECH_DATABASE_URL=' /opt/trading-bot/.env | cut -d= -f2- || true)
fi
if [ -z "$DB_URL" ]; then
  echo "TECH_DATABASE_URL not configured - nothing to dump" >&2
  exit 0
fi

STAMP=$(TZ=Asia/Kolkata date +%Y-%m-%d)
OUT="/tmp/tradingbot-${STAMP}.dump"
pg_dump --format=custom --no-owner --file="$OUT" "$DB_URL"
aws s3 cp "$OUT" "s3://${DEPLOY_BUCKET}/trading-bot/pgdump/tradingbot-${STAMP}.dump" --only-show-errors
rm -f "$OUT"

# retention: keep the newest 30
aws s3 ls "s3://${DEPLOY_BUCKET}/trading-bot/pgdump/" | awk '{print $4}' | sort | head -n -30 | while read -r key; do
  [ -n "$key" ] && aws s3 rm "s3://${DEPLOY_BUCKET}/trading-bot/pgdump/${key}" --only-show-errors
done
echo "pg_dump uploaded: tradingbot-${STAMP}.dump"
