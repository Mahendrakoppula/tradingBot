#!/bin/bash
# Runs on every boot (via trading-bot-bootstrap.service, before the main
# service) to assemble /opt/trading-bot/.env: non-secret config.env (bundled
# in the deploy package) + secrets fetched fresh from SSM Parameter Store
# (never bundled in the package, never touch S3 or git).
set -euo pipefail

APP_DIR="/opt/trading-bot"
ENV_FILE="$APP_DIR/.env"

cp "$APP_DIR/config.env" "$ENV_FILE"

# At boot the instance role's credentials / DNS can lag the unit by a few
# seconds (2026-09-21: the very first call returned nothing 11 s after boot
# and both bots failed on the dependency). Retry until SSM answers with a
# JSON array, then hand it to assemble_env.py in one go.
PARAMS=""
for attempt in $(seq 1 12); do
  PARAMS=$(aws ssm get-parameters-by-path --path "/trading-bot/" --with-decryption     --query "Parameters[].{Name:Name,Value:Value}" --output json 2>/dev/null || true)
  case "$PARAMS" in
    "["*) break ;;
  esac
  echo "SSM not ready (attempt $attempt) - retrying in 5s" >&2
  PARAMS=""
  sleep 5
done
if [ -z "$PARAMS" ]; then
  echo "could not fetch parameters from SSM after 12 attempts" >&2
  exit 1
fi
printf '%s' "$PARAMS" | python3 "$APP_DIR/deploy/assemble_env.py" "$ENV_FILE"

chown tradingbot:tradingbot "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "Assembled $ENV_FILE from config.env + SSM Parameter Store"
