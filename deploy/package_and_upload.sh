#!/bin/bash
# Run this LOCALLY (not on the EC2 instance) whenever code or deploy/config.env
# changes, to push a fresh deployable package to S3. Creates the bucket if it
# doesn't exist yet, so this can safely run before OR after setup_aws.sh.
#
# Does NOT bundle real credentials - only deploy/config.env (non-secret
# strategy config) goes in as config.env. Real secrets live in SSM Parameter
# Store and are fetched fresh on every instance boot - see fetch_secrets.sh.
set -euo pipefail

REGION="${REGION:-ap-south-1}"

if [ -z "${DEPLOY_BUCKET:-}" ]; then
  echo "Set DEPLOY_BUCKET first, e.g.: DEPLOY_BUCKET=my-trading-bot-deploy-xxxx ./deploy/package_and_upload.sh"
  exit 1
fi

if ! aws s3api head-bucket --bucket "$DEPLOY_BUCKET" --region "$REGION" 2>/dev/null; then
  aws s3api create-bucket --bucket "$DEPLOY_BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
  aws s3api put-public-access-block --bucket "$DEPLOY_BUCKET" --region "$REGION" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "$DEPLOY_BUCKET" --region "$REGION" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  echo "Created private, encrypted bucket $DEPLOY_BUCKET"
fi

cd "$(dirname "$0")/.."   # repo root

python -c "
import zipfile, pathlib
root = pathlib.Path('.')
out = pathlib.Path('deploy_package.zip')
if out.exists():
    out.unlink()
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    # rglob, not glob: trading_bot/engine/ is a subpackage and must ship too
    for f in root.rglob('trading_bot/**/*.py'):
        if '__pycache__' not in f.parts:
            z.write(f, f.as_posix())
    z.write('requirements.txt', 'requirements.txt')
    z.write('deploy/config.env', 'config.env')
    z.write('deploy/fetch_secrets.sh', 'deploy/fetch_secrets.sh')
    z.write('deploy/assemble_env.py', 'deploy/assemble_env.py')
    z.write('deploy/trading-bot.service', 'deploy/trading-bot.service')
    z.write('deploy/trading-bot-technical.service', 'deploy/trading-bot-technical.service')
    z.write('deploy/trading-bot-bootstrap.service', 'deploy/trading-bot-bootstrap.service')
    z.write('deploy/sync_state_to_s3.sh', 'deploy/sync_state_to_s3.sh')
    z.write('deploy/trading-bot-s3-sync.service', 'deploy/trading-bot-s3-sync.service')
    z.write('deploy/trading-bot-s3-sync.timer', 'deploy/trading-bot-s3-sync.timer')
    # second bot: Postgres one-time setup + nightly pg_dump (deploy/DEPLOY.md section 8)
    z.write('deploy/setup_postgres.sh', 'deploy/setup_postgres.sh')
    z.write('deploy/pgdump_to_s3.sh', 'deploy/pgdump_to_s3.sh')
    z.write('deploy/trading-bot-pgdump.service', 'deploy/trading-bot-pgdump.service')
    z.write('deploy/trading-bot-pgdump.timer', 'deploy/trading-bot-pgdump.timer')
    z.write('deploy/engine_review.sh', 'deploy/engine_review.sh')
    z.write('deploy/trading-bot-engine-review.service', 'deploy/trading-bot-engine-review.service')
    z.write('deploy/trading-bot-engine-review.timer', 'deploy/trading-bot-engine-review.timer')
    # nightly review/tuning agent - see deploy/setup_claude_agent.sh
    z.write('deploy/setup_claude_agent.sh', 'deploy/setup_claude_agent.sh')
    z.write('deploy/daily_review_prompt.md', 'deploy/daily_review_prompt.md')
    z.write('deploy/trading-bot-review.service', 'deploy/trading-bot-review.service')
    z.write('deploy/trading-bot-review.timer', 'deploy/trading-bot-review.timer')
print('built', out, out.stat().st_size, 'bytes')
"

aws s3 cp deploy_package.zip "s3://${DEPLOY_BUCKET}/trading-bot/app.zip"
rm -f deploy_package.zip

echo "Uploaded to s3://${DEPLOY_BUCKET}/trading-bot/app.zip"
echo "New EC2 instances pick this up automatically via user-data on first boot."
echo "An ALREADY-RUNNING instance needs deploy/redeploy.sh to pick up this update."
