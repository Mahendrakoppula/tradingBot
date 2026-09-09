# Deploying to AWS

**Status: live.** Architecture: one EC2 instance, started at 8am IST and
stopped at 6pm IST on weekdays by EventBridge Scheduler, running the bot as
a systemd service. No SSH, no open inbound ports - everything's managed
through AWS Systems Manager (SSM).

Real credentials (SmartAPI key/client code/PIN/TOTP, Telegram tokens) live
in **AWS SSM Parameter Store** under `/trading-bot/*` (free tier - no
Secrets Manager flat fee), fetched fresh onto the instance on every boot by
`trading-bot-bootstrap.service` (see `fetch_secrets.sh`). They are never
bundled into the deploy package, never touch S3, and never touch git.
Non-secret strategy config (`config.env`) IS committed to the repo and IS
part of the deploy package - it's not sensitive, and having it in git means
config changes go through the same branch -> main -> auto-deploy flow as
code changes.

Estimated cost: **~$4-5/month** (EC2 ~14h/day x weekdays + a few cents of
EBS/S3/Parameter Store), possibly close to $0 if your AWS account is still
in its 12-month Free Tier window.

## 1. Set up Telegram alerts

Three separate bots recommended: one for the daily bot's routine activity
(entries, exits, morning briefing), one dedicated to its error alerts so
real problems don't get lost in the noise, and one for the second
(technical-indicator) bot's alerts - both routine and error, one channel.

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   follow the prompts, for each bot. You get a **bot token**
   (`123456789:ABC-...`) each time.
2. Message each new bot anything (so it has a chat to talk back into).
3. Get each **chat ID**: visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser right after
   step 2 - look for `"chat":{"id":...}` in the JSON.
4. Put all six values in `.env`: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
   (daily bot activity), `ERROR_TELEGRAM_BOT_TOKEN`/`ERROR_TELEGRAM_CHAT_ID`
   (daily bot errors), `TECH_TELEGRAM_BOT_TOKEN`/`TECH_TELEGRAM_CHAT_ID`
   (second bot, everything).

## 2. Fill in real credentials locally

Copy `.env.example` to `.env` in the repo root and fill in everything:
SmartAPI key/client code/PIN/TOTP secret, all three Telegram bot pairs. This
file is gitignored and never leaves your machine except via the SSM push
below.

Also decide now: keep `DRY_RUN=true`/`TECH_DRY_RUN=true` and
`ENABLE_TRADING=false`/`TECH_ENABLE_TRADING=false` (in `deploy/config.env`,
not `.env` - see below) for a while before flipping either pair. Nothing
places a real order until both of a pair are set.

## 3. One-time AWS setup

Needs an IAM user/role with permission to create EC2 instances, IAM
roles/policies, S3 buckets, security groups, EventBridge schedules, SSM
parameters, and an OIDC provider. An `AdministratorAccess` policy on a
throwaway/dedicated AWS account is simplest; otherwise ask your AWS admin.
Configure it with `aws configure` (use the `!` prefix in a Claude Code
session so keys don't land in the conversation transcript).

```
cd trading-bot
DEPLOY_BUCKET=trading-bot-deploy-<pick-something-unique> ./deploy/package_and_upload.sh
DEPLOY_BUCKET=<same-bucket-name> ./deploy/setup_aws.sh
```

This creates: a private encrypted S3 bucket, the EC2 instance's IAM role (S3
read + SSM Parameter Store read, nothing else), a security group with **no
inbound rules at all**, the EC2 instance itself, and the two EventBridge
schedules (start 8am IST / stop 6pm IST, Mon-Fri).

Then push your real secrets to Parameter Store (never touches S3 or git):

```
cd deploy/.tmp && python push_secrets_to_ssm.py
```

(That script reads `../../.env` and pushes each `SMARTAPI_*`/`TELEGRAM_*`/
`ERROR_TELEGRAM_*`/`TECH_TELEGRAM_*` key as a `SecureString` parameter under
`/trading-bot/`. It's local-only/gitignored, not tracked in the repo -
re-run it after adding/rotating any secret in `.env`. Needs AWS credentials
with `ssm:PutParameter` on `/trading-bot/*` - the scoped
`trading-bot-deployer` IAM user does NOT have this by design (least
privilege: it can provision infrastructure but not read/write runtime
secrets), so run this step with different/broader credentials.)

## 4. Verify it's running

No SSH needed - use SSM Session Manager:

```
aws ssm start-session --target <INSTANCE_ID>
sudo journalctl -u trading-bot -f
sudo journalctl -u trading-bot-technical -f   # second bot, separate unit/log
```

You should get a Telegram message when each service starts (daily bot's own
chat; second bot's separate chat), followed by the daily bot's morning
briefing. If nothing shows up:
- `sudo systemctl status trading-bot-bootstrap` (did secret-fetching work? -
  shared by both bots, since they read the same `/opt/trading-bot/.env`)
- `sudo systemctl status trading-bot` (did the daily bot start?)
- `sudo systemctl status trading-bot-technical` (did the second bot start? -
  `TECH_ENABLE_TRADING=false` at first deploy on purpose, see config.env's
  own comment - flip it once this looks healthy)
- `sudo cat /var/log/cloud-init-output.log` (first-boot provisioning log,
  only relevant right after the very first launch)

## 5. CI/CD: auto-deploy on push to main

**Workflow:** make changes on a branch, merge to `main` - GitHub Actions
then tests, packages, uploads to S3, and redeploys onto the live instance
automatically (starting it first if it's outside the 8am-6pm window, and
stopping it again afterward so you're not paying for idle time).

One-time setup, already done for this repo but documented here in case it
needs to be redone:

1. **OIDC trust, no static AWS keys in GitHub**: created an OIDC identity
   provider for `token.actions.githubusercontent.com` and an IAM role
   (`trading-bot-github-deploy-role`) whose trust policy only allows
   `repo:Mahendrakoppula/tradingBot:ref:refs/heads/main` to assume it - see
   `deploy/github_actions_trust_policy.json` and
   `deploy/github_actions_permissions_policy.json` (S3 put, SSM
   send-command, EC2 describe/start/stop, all scoped to this project's
   specific bucket/instance).
2. **GitHub repo Variables** (Settings -> Secrets and variables -> Actions
   -> Variables tab - these are NOT secrets, just config the workflow
   needs): `AWS_ACCOUNT_ID`, `DEPLOY_BUCKET`, `INSTANCE_ID`.
3. That's it - `.github/workflows/deploy.yml` handles the rest. It runs
   `pytest tests/` first and refuses to deploy if anything fails.

To change strategy behavior in production without touching code: edit
`deploy/config.env` (DTE window, entry/exit times, risk caps, watchlist,
etc. - all non-secret), commit on a branch, merge to main.

To rotate a secret: update it in Parameter Store directly (`aws ssm
put-parameter ... --overwrite`) or re-run `push_secrets_to_ssm.py` after
updating local `.env` - it'll be picked up on the instance's next boot
(no code deploy needed for a pure secret rotation).

## 6. Deploying code updates manually (without CI)

```
DEPLOY_BUCKET=<bucket> ./deploy/package_and_upload.sh
DEPLOY_BUCKET=<bucket> INSTANCE_ID=<id> ./deploy/redeploy.sh
```

`redeploy.sh` needs the instance to be running (it uses SSM Run Command) -
either do it during the 8am-6pm window, or `aws ec2 start-instances
--instance-ids <id>` first and stop it again after.

## 7. Tearing it down

```
DEPLOY_BUCKET=<bucket> ./deploy/teardown_aws.sh          # keeps the S3 bucket
DEPLOY_BUCKET=<bucket> DELETE_BUCKET=1 ./deploy/teardown_aws.sh   # removes everything
```

Doesn't currently delete the OIDC provider, GitHub Actions role, or SSM
parameters - clean those up separately if fully decommissioning.
