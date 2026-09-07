#!/bin/bash
# Run this LOCALLY (not on the EC2 instance) whenever code or .env changes,
# to push a fresh deployable package to S3. Creates the bucket if it doesn't
# exist yet, so this can safely run before OR after setup_aws.sh.
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

if [ ! -f .env ]; then
  echo ".env not found - copy .env.example to .env and fill in real credentials first."
  exit 1
fi

python -c "
import zipfile, pathlib
root = pathlib.Path('.')
out = pathlib.Path('deploy_package.zip')
if out.exists():
    out.unlink()
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for f in root.glob('trading_bot/*.py'):
        z.write(f, f)
    z.write('requirements.txt', 'requirements.txt')
    z.write('.env', '.env')
    z.write('deploy/trading-bot.service', 'deploy/trading-bot.service')
print('built', out, out.stat().st_size, 'bytes')
"

aws s3 cp deploy_package.zip "s3://${DEPLOY_BUCKET}/trading-bot/app.zip"
rm -f deploy_package.zip

echo "Uploaded to s3://${DEPLOY_BUCKET}/trading-bot/app.zip"
echo "New EC2 instances pick this up automatically via user-data on first boot."
echo "An ALREADY-RUNNING instance needs deploy/redeploy.sh to pick up this update."
