#!/bin/bash
# Provisions the nightly review-and-tuning agent (Claude Code) on the
# trading-bot EC2 instance. Run ONCE, via SSM, on an instance that's already
# been provisioned by user_data.sh/setup_aws.sh.
#
#   aws ssm send-command --instance-ids "$INSTANCE_ID" \
#     --document-name AWS-RunShellScript \
#     --parameters 'commands=["bash /opt/trading-bot/deploy/setup_claude_agent.sh"]'
#
# Prerequisites - put these in SSM Parameter Store as SecureString FIRST:
#   /trading-bot/anthropic-api-key   an Anthropic API key
#   /trading-bot/github-token        a GitHub PAT with repo scope (clone + push)
#
# What it does:
#   - installs Node.js, git, and Claude Code
#   - clones the repo to ~/trading-bot-agent (separate from /opt/trading-bot,
#     which is an unzipped release artifact, not a git checkout)
#   - installs the systemd service + timer that runs the review each weekday
set -euo pipefail
# Deliberately NOT using -x: xtrace would echo ANTHROPIC_API_KEY/GITHUB_TOKEN
# in cleartext into this script's output, which lands in SSM command
# output/CloudWatch Logs (readable by anyone with read access to command
# history) - defeats the point of storing them as SecureString.

REGION="${AWS_REGION:-ap-south-1}"
AGENT_USER="tradingbot"
AGENT_HOME="/opt/trading-bot"          # tradingbot's home per user_data.sh
AGENT_REPO="${AGENT_HOME}/trading-bot-agent"
REPO_URL_PATH="Mahendrakoppula/tradingBot.git"

dnf install -y git nodejs npm

npm install -g @anthropic-ai/claude-code

# --- secrets -------------------------------------------------------------
# Same pattern as deploy/fetch_secrets.sh: pulled from SSM at setup time,
# written to a root-owned-but-agent-readable env file, never baked into the
# deploy zip or committed.
ANTHROPIC_API_KEY="$(aws ssm get-parameter --region "$REGION" \
  --name /trading-bot/anthropic-api-key --with-decryption \
  --query 'Parameter.Value' --output text)"
GITHUB_TOKEN="$(aws ssm get-parameter --region "$REGION" \
  --name /trading-bot/github-token --with-decryption \
  --query 'Parameter.Value' --output text)"

install -m 600 -o "$AGENT_USER" -g "$AGENT_USER" /dev/null "${AGENT_HOME}/.agent-env"
cat > "${AGENT_HOME}/.agent-env" <<EOF
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
EOF
chmod 600 "${AGENT_HOME}/.agent-env"
chown "$AGENT_USER:$AGENT_USER" "${AGENT_HOME}/.agent-env"

# --- repo checkout -------------------------------------------------------
if [ ! -d "${AGENT_REPO}/.git" ]; then
  sudo -u "$AGENT_USER" git clone \
    "https://${GITHUB_TOKEN}@github.com/${REPO_URL_PATH}" "$AGENT_REPO"
fi
# Store the token in the remote URL so unattended push works without a
# credential helper prompt. `git clone`/`remote set-url` write it into
# .git/config at the checkout user's default umask (typically 644), NOT
# 0600 - chmod it explicitly so the PAT isn't world/group-readable on disk.
sudo -u "$AGENT_USER" git -C "$AGENT_REPO" remote set-url origin \
  "https://${GITHUB_TOKEN}@github.com/${REPO_URL_PATH}"
chmod 600 "${AGENT_REPO}/.git/config"
sudo -u "$AGENT_USER" git -C "$AGENT_REPO" config user.email "trading-bot-agent@users.noreply.github.com"
sudo -u "$AGENT_USER" git -C "$AGENT_REPO" config user.name "trading-bot nightly agent"

sudo -u "$AGENT_USER" mkdir -p "${AGENT_HOME}/.state/reviews"

# --- schedule ------------------------------------------------------------
cp "${AGENT_HOME}/deploy/trading-bot-review.service" /etc/systemd/system/
cp "${AGENT_HOME}/deploy/trading-bot-review.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now trading-bot-review.timer

systemctl list-timers trading-bot-review.timer --no-pager
echo "Nightly review agent installed. Next run shown above."
