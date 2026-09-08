#!/bin/bash
# Runs on the EC2 instance (via trading-bot-s3-sync.timer, every 30 min
# while the instance is up) to back up append-only/accumulating history -
# trade_log.jsonl, journal.jsonl, option_chain_log/*.jsonl, and
# candle_log/*.json - to S3. The instance's local EBS volume is currently
# the ONLY copy of this data; if the instance or volume is ever replaced,
# everything in .state/ is gone. This is a plain backup, not a queryable
# store - see research/README.md for what this data is for.
#
# Syncs everything EXCEPT the current-state files (capital.json,
# long_positions.json, scalp_positions.json - these get overwritten in
# place, not accumulated, so they're not useful in a growing archive) -
# an exclude-list rather than an extension allowlist so any new log
# category added later (like candle_log/*.json) is backed up automatically
# without needing to remember to update this script too.
set -euo pipefail

APP_DIR="/opt/trading-bot"
# Stable for the life of this project (see deploy/DEPLOY.md) - hardcoded
# rather than threaded through config.env, since it's deployment
# infrastructure, not strategy config. Override via env var if it ever
# changes without needing to touch this script.
DEPLOY_BUCKET="${DEPLOY_BUCKET:-trading-bot-deploy-396913392704}"

aws s3 sync "$APP_DIR/.state/" "s3://${DEPLOY_BUCKET}/trading-bot/historical/" \
  --exclude "capital.json" --exclude "long_positions.json" --exclude "scalp_positions.json" \
  --only-show-errors
