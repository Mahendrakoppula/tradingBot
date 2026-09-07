#!/bin/bash
# EC2 user-data: runs ONCE on first boot to provision the instance. Every
# boot after that (the daily 6am start via EventBridge) just relies on the
# systemd service already being enabled - this script does NOT re-run.
set -euxo pipefail

DEPLOY_BUCKET="__DEPLOY_BUCKET__"
DEPLOY_KEY="__DEPLOY_KEY__"   # e.g. trading-bot/app.zip

dnf install -y python3.12 python3.12-pip unzip

# Official AWS CLI v2 installer (more reliable than relying on a repo package name).
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install
rm -rf /tmp/awscliv2.zip /tmp/aws

id -u tradingbot &>/dev/null || useradd --system --create-home --home-dir /opt/trading-bot --shell /usr/sbin/nologin tradingbot

mkdir -p /opt/trading-bot
cd /opt/trading-bot

# app.zip contains trading_bot/, requirements.txt, and .env (credentials +
# strategy config) - built and uploaded by deploy/package_and_upload.sh.
aws s3 cp "s3://${DEPLOY_BUCKET}/${DEPLOY_KEY}" /opt/trading-bot/app.zip
unzip -oq /opt/trading-bot/app.zip -d /opt/trading-bot
rm -f /opt/trading-bot/app.zip

python3.12 -m venv /opt/trading-bot/.venv
/opt/trading-bot/.venv/bin/pip install --quiet --upgrade pip
/opt/trading-bot/.venv/bin/pip install --quiet -r /opt/trading-bot/requirements.txt

chown -R tradingbot:tradingbot /opt/trading-bot
chmod 600 /opt/trading-bot/.env

cp /opt/trading-bot/deploy/trading-bot.service /etc/systemd/system/trading-bot.service
systemctl daemon-reload
systemctl enable --now trading-bot.service
