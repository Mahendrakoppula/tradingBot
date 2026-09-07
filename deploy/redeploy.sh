#!/bin/bash
# Pushes the latest s3://$DEPLOY_BUCKET/trading-bot/app.zip (code + config.env
# - no secrets, those live in SSM Parameter Store and get fetched fresh by
# trading-bot-bootstrap.service on every start) onto an ALREADY RUNNING
# instance via SSM Run Command (no SSH needed) and restarts the service. Run
# deploy/package_and_upload.sh first to actually update what's in S3. The
# instance must be running (during the 6am-8pm window, or start it manually
# with: aws ec2 start-instances --instance-ids $INSTANCE_ID).
set -euo pipefail

if [ -z "${DEPLOY_BUCKET:-}" ] || [ -z "${INSTANCE_ID:-}" ]; then
  echo "Set DEPLOY_BUCKET and INSTANCE_ID first, e.g.:"
  echo "  DEPLOY_BUCKET=my-trading-bot-deploy-xxxx INSTANCE_ID=i-0123456789abcdef0 ./deploy/redeploy.sh"
  exit 1
fi

COMMAND_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --comment "trading-bot redeploy" \
  --parameters "commands=[
    'systemctl stop trading-bot.service',
    'aws s3 cp s3://${DEPLOY_BUCKET}/trading-bot/app.zip /opt/trading-bot/app.zip',
    'unzip -oq /opt/trading-bot/app.zip -d /opt/trading-bot',
    'rm -f /opt/trading-bot/app.zip',
    '/opt/trading-bot/.venv/bin/pip install --quiet -r /opt/trading-bot/requirements.txt',
    'chmod +x /opt/trading-bot/deploy/fetch_secrets.sh',
    'chown -R tradingbot:tradingbot /opt/trading-bot',
    'cp /opt/trading-bot/deploy/trading-bot-bootstrap.service /etc/systemd/system/trading-bot-bootstrap.service',
    'cp /opt/trading-bot/deploy/trading-bot.service /etc/systemd/system/trading-bot.service',
    'systemctl daemon-reload',
    'systemctl start trading-bot.service'
  ]" \
  --query "Command.CommandId" --output text)

echo "Sent command $COMMAND_ID - waiting for it to finish..."
aws ssm wait command-executed --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" || true
aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" \
  --query "{Status:Status,StdOut:StandardOutputContent,StdErr:StandardErrorContent}" --output table
