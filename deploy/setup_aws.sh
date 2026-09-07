#!/bin/bash
# One-time AWS provisioning for the trading bot: S3 bucket, IAM role
# (S3 read + SSM, no SSH/inbound needed), a no-inbound security group, the
# EC2 instance itself, and EventBridge Scheduler rules to start it at 8am
# and stop it at 6pm IST on weekdays. Safe to re-run - skips anything that
# already exists by name/tag.
set -euo pipefail

REGION="${REGION:-ap-south-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.micro}"
NAME="trading-bot"
DEPLOY_BUCKET="${DEPLOY_BUCKET:?Set DEPLOY_BUCKET, e.g. DEPLOY_BUCKET=trading-bot-deploy-$(date +%s)}"

# Use a repo-local temp dir, not /tmp - on Windows the native aws.exe/Python
# resolve "/tmp/..." completely differently than Git Bash does, so a file
# bash writes to /tmp is invisible to aws CLI (confirmed: FileNotFoundError).
TMPDIR="$(dirname "$0")/.tmp"
mkdir -p "$TMPDIR"

echo "== Region: $REGION, bucket: $DEPLOY_BUCKET, instance type: $INSTANCE_TYPE =="

# --- S3 bucket for the deployment package ---
if ! aws s3api head-bucket --bucket "$DEPLOY_BUCKET" --region "$REGION" 2>/dev/null; then
  aws s3api create-bucket --bucket "$DEPLOY_BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION"
  aws s3api put-public-access-block --bucket "$DEPLOY_BUCKET" --region "$REGION" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "$DEPLOY_BUCKET" --region "$REGION" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  echo "Created private, encrypted bucket $DEPLOY_BUCKET"
else
  echo "Bucket $DEPLOY_BUCKET already exists - reusing"
fi

# --- IAM role for the EC2 instance (S3 read-only on this bucket + SSM, no SSH needed) ---
ROLE_NAME="trading-bot-ec2-role"
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "file://$(dirname "$0")/iam_trust_policy.json"
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  sed "s/__DEPLOY_BUCKET__/${DEPLOY_BUCKET}/" "$(dirname "$0")/iam_permissions_policy.json" > $TMPDIR/trading-bot-s3-policy.json
  aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name trading-bot-s3-read \
    --policy-document file://$TMPDIR/trading-bot-s3-policy.json
  echo "Created IAM role $ROLE_NAME"
else
  echo "IAM role $ROLE_NAME already exists - reusing"
fi

INSTANCE_PROFILE="trading-bot-instance-profile"
if ! aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$INSTANCE_PROFILE"
  aws iam add-role-to-instance-profile --instance-profile-name "$INSTANCE_PROFILE" --role-name "$ROLE_NAME"
  echo "Waiting for instance profile propagation..."
  sleep 15
fi

# --- Security group: NO inbound rules at all (SSM handles all access, no SSH port) ---
VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text)
SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters Name=group-name,Values=trading-bot-sg Name=vpc-id,Values="$VPC_ID" \
  --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || echo "None")
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group --region "$REGION" --group-name trading-bot-sg \
    --description "trading-bot: outbound only, no inbound (managed via SSM)" --vpc-id "$VPC_ID" \
    --query "GroupId" --output text)
  echo "Created security group $SG_ID (no inbound rules)"
else
  echo "Security group $SG_ID already exists - reusing"
fi

SUBNET_ID=$(aws ec2 describe-subnets --region "$REGION" --filters Name=vpc-id,Values="$VPC_ID" \
  --query "Subnets[0].SubnetId" --output text)

# --- EC2 instance ---
EXISTING=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query "Reservations[0].Instances[0].InstanceId" --output text 2>/dev/null || echo "None")

if [ "$EXISTING" = "None" ] || [ -z "$EXISTING" ]; then
  AMI_ID=$(aws ec2 describe-images --region "$REGION" --owners amazon \
    --filters "Name=name,Values=al2023-ami-*-x86_64" "Name=state,Values=available" \
    --query "sort_by(Images,&CreationDate)[-1].ImageId" --output text)

  sed -e "s/__DEPLOY_BUCKET__/${DEPLOY_BUCKET}/" -e "s|__DEPLOY_KEY__|trading-bot/app.zip|" \
    "$(dirname "$0")/user_data.sh" > $TMPDIR/trading-bot-user-data.sh

  INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" \
    --subnet-id "$SUBNET_ID" --security-group-ids "$SG_ID" \
    --iam-instance-profile Name="$INSTANCE_PROFILE" \
    --user-data "file://$TMPDIR/trading-bot-user-data.sh" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
    --query "Instances[0].InstanceId" --output text)
  echo "Launched instance $INSTANCE_ID (first boot will take a few minutes to provision)"
else
  INSTANCE_ID="$EXISTING"
  echo "Instance $INSTANCE_ID already exists - reusing (won't re-run user-data; use deploy/redeploy.sh for code updates)"
fi

# --- EventBridge Scheduler: start 8am IST, stop 6pm IST, weekdays only ---
SCHEDULER_ROLE_NAME="trading-bot-scheduler-role"
if ! aws iam get-role --role-name "$SCHEDULER_ROLE_NAME" >/dev/null 2>&1; then
  cat > $TMPDIR/scheduler-trust-policy.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"scheduler.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
  aws iam create-role --role-name "$SCHEDULER_ROLE_NAME" --assume-role-policy-document file://$TMPDIR/scheduler-trust-policy.json
  cat > $TMPDIR/scheduler-permissions-policy.json <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ec2:StartInstances","ec2:StopInstances"],"Resource":"arn:aws:ec2:${REGION}:*:instance/${INSTANCE_ID}"}]}
EOF
  aws iam put-role-policy --role-name "$SCHEDULER_ROLE_NAME" --policy-name trading-bot-ec2-start-stop \
    --policy-document file://$TMPDIR/scheduler-permissions-policy.json
  echo "Waiting for scheduler role propagation..."
  sleep 15
fi
SCHEDULER_ROLE_ARN=$(aws iam get-role --role-name "$SCHEDULER_ROLE_NAME" --query "Role.Arn" --output text)

aws scheduler create-schedule --region "$REGION" --name trading-bot-start --schedule-expression "cron(0 8 ? * MON-FRI *)" \
  --schedule-expression-timezone "Asia/Kolkata" --flexible-time-window '{"Mode":"OFF"}' \
  --target "{\"Arn\":\"arn:aws:scheduler:::aws-sdk:ec2:startInstances\",\"RoleArn\":\"$SCHEDULER_ROLE_ARN\",\"Input\":\"{\\\"InstanceIds\\\":[\\\"$INSTANCE_ID\\\"]}\"}" \
  2>/dev/null || echo "Schedule trading-bot-start already exists - skipping"

aws scheduler create-schedule --region "$REGION" --name trading-bot-stop --schedule-expression "cron(0 18 ? * MON-FRI *)" \
  --schedule-expression-timezone "Asia/Kolkata" --flexible-time-window '{"Mode":"OFF"}' \
  --target "{\"Arn\":\"arn:aws:scheduler:::aws-sdk:ec2:stopInstances\",\"RoleArn\":\"$SCHEDULER_ROLE_ARN\",\"Input\":\"{\\\"InstanceIds\\\":[\\\"$INSTANCE_ID\\\"]}\"}" \
  2>/dev/null || echo "Schedule trading-bot-stop already exists - skipping"

echo
echo "== Done =="
echo "Instance: $INSTANCE_ID"
echo "Runs 8am-6pm IST, Mon-Fri. Logs: aws ssm start-session --target $INSTANCE_ID, then: journalctl -u trading-bot -f"
