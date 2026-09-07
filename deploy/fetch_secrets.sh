#!/bin/bash
# Runs on every boot (via trading-bot-bootstrap.service, before the main
# service) to assemble /opt/trading-bot/.env: non-secret config.env (bundled
# in the deploy package) + secrets fetched fresh from SSM Parameter Store
# (never bundled in the package, never touch S3 or git).
set -euo pipefail

APP_DIR="/opt/trading-bot"
ENV_FILE="$APP_DIR/.env"

cp "$APP_DIR/config.env" "$ENV_FILE"

aws ssm get-parameters-by-path --path "/trading-bot/" --with-decryption \
  --query "Parameters[].{Name:Name,Value:Value}" --output json \
  | python3 -c "
import json, sys
params = json.load(sys.stdin)
with open('$ENV_FILE', 'a') as f:
    f.write('\n# --- secrets, fetched from SSM Parameter Store at boot ---\n')
    for p in params:
        key = p['Name'].rsplit('/', 1)[-1]
        f.write(f'{key}={p[\"Value\"]}\n')
"

chown tradingbot:tradingbot "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "Assembled $ENV_FILE from config.env + SSM Parameter Store"
