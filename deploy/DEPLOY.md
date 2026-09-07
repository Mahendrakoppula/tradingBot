# Deploying to AWS

Architecture: one EC2 instance, started at 6am IST and stopped at 8pm IST on
weekdays by EventBridge Scheduler, running the bot as a systemd service.
Credentials live in a `.env` file inside a private S3 bucket (not AWS
Secrets Manager - skipped to save the ~$0.40/month flat fee; still not
baked into any file that would end up in a public repo). No SSH, no open
inbound ports - everything's managed through AWS Systems Manager (SSM).

Estimated cost: **~$4-5/month** (EC2 ~14h/day x weekdays + a few cents of
EBS/S3), possibly close to $0 if your AWS account is still in its 12-month
Free Tier window. See the cost breakdown discussed in-session for the math.

## 1. Set up Telegram alerts

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   follow the prompts. It gives you a **bot token**
   (`123456789:ABC-...`).
2. Message your new bot anything (so it has a chat to talk back into).
3. Get your **chat ID**: visit
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser right
   after step 2 - look for `"chat":{"id":...}` in the JSON.
4. Put both values in `.env` (see step 2 below).

## 2. Fill in real credentials

Copy `.env.example` to `.env` in the repo root and fill in everything:
SmartAPI key/client code/PIN/TOTP secret, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, and review the strategy settings (watchlist, DTE window,
capital, risk caps). This file is gitignored - it will be uploaded to a
*private* S3 bucket, never committed anywhere.

Also decide now: keep `DRY_RUN=true` and `ENABLE_TRADING=false` for the
first while you watch it run on AWS before flipping either to
false/true. Nothing places a real order until both are set.

## 3. Give me (or yourself) AWS access

If I'm doing the deployment: run `aws configure` **yourself**, using the
`!` prefix in this session (`! aws configure`) so your access key/secret
never appear in our conversation transcript - only I need `aws` calls to
succeed afterward, I don't need to see the raw keys. You'll need an IAM
user/role with permission to create EC2 instances, IAM roles, S3 buckets,
security groups, and EventBridge schedules (an `AdministratorAccess` policy
on a throwaway account is simplest if this is a fresh AWS account just for
this project; otherwise ask your AWS admin for those specific permissions).

If you're running the scripts yourself instead: same `aws configure` step,
then follow steps 4-5 yourself.

## 4. Provision everything

```
cd trading-bot
DEPLOY_BUCKET=trading-bot-deploy-<pick-something-unique> ./deploy/setup_aws.sh
```

This creates: a private encrypted S3 bucket, an IAM role (S3 read + SSM,
nothing else), a security group with **no inbound rules at all**, the EC2
instance itself (stopped as `pending` until first boot finishes
provisioning), and the two EventBridge schedules (start 6am IST / stop 8pm
IST, Mon-Fri).

**But first**, package and upload the code + your `.env`:

```
DEPLOY_BUCKET=<same-bucket-name> ./deploy/package_and_upload.sh
```

Run `package_and_upload.sh` BEFORE `setup_aws.sh` the very first time (the
instance's user-data downloads `app.zip` from S3 on first boot - it needs
to already be there).

## 5. Verify it's running

No SSH needed - use SSM Session Manager:

```
aws ssm start-session --target <INSTANCE_ID>
sudo journalctl -u trading-bot -f
```

You should also get a Telegram message when the service starts
("Condor runner started - ..."). If you don't see anything by a few minutes
after 6am IST (or right after `setup_aws.sh` finishes, since the instance
launches immediately as part of that), check:
- `sudo cat /var/log/cloud-init-output.log` (first-boot provisioning log)
- `sudo systemctl status trading-bot` (service status)

## 6. Deploying code updates later

The instance's user-data only runs once (first boot) - restarting it daily
doesn't re-pull code. To push a change:

```
DEPLOY_BUCKET=<bucket> ./deploy/package_and_upload.sh
DEPLOY_BUCKET=<bucket> INSTANCE_ID=<id> ./deploy/redeploy.sh
```

`redeploy.sh` needs the instance to be running (it uses SSM Run Command) -
either do it during the 6am-8pm window, or `aws ec2 start-instances
--instance-ids <id>` first and stop it again after.

## 7. Tearing it down

```
DEPLOY_BUCKET=<bucket> ./deploy/teardown_aws.sh          # keeps the S3 bucket
DEPLOY_BUCKET=<bucket> DELETE_BUCKET=1 ./deploy/teardown_aws.sh   # removes everything
```
