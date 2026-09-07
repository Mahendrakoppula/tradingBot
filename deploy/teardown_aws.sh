#!/bin/bash
# Removes everything setup_aws.sh created: EC2 instance, EventBridge
# schedules, IAM roles/instance profile, security group, and (only if you
# pass DELETE_BUCKET=1) the S3 bucket and its contents.
set -euo pipefail

REGION="${REGION:-ap-south-1}"
NAME="trading-bot"
DEPLOY_BUCKET="${DEPLOY_BUCKET:?Set DEPLOY_BUCKET to the same value you used in setup_aws.sh}"

aws scheduler delete-schedule --region "$REGION" --name trading-bot-start 2>/dev/null || true
aws scheduler delete-schedule --region "$REGION" --name trading-bot-stop 2>/dev/null || true

INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query "Reservations[0].Instances[0].InstanceId" --output text 2>/dev/null || echo "None")
if [ "$INSTANCE_ID" != "None" ] && [ -n "$INSTANCE_ID" ]; then
  aws ec2 terminate-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
  echo "Terminating $INSTANCE_ID..."
  aws ec2 wait instance-terminated --region "$REGION" --instance-ids "$INSTANCE_ID"
fi

aws iam remove-role-from-instance-profile --instance-profile-name trading-bot-instance-profile --role-name trading-bot-ec2-role 2>/dev/null || true
aws iam delete-instance-profile --instance-profile-name trading-bot-instance-profile 2>/dev/null || true
aws iam detach-role-policy --role-name trading-bot-ec2-role --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore 2>/dev/null || true
aws iam delete-role-policy --role-name trading-bot-ec2-role --policy-name trading-bot-s3-read 2>/dev/null || true
aws iam delete-role --role-name trading-bot-ec2-role 2>/dev/null || true

aws iam delete-role-policy --role-name trading-bot-scheduler-role --policy-name trading-bot-ec2-start-stop 2>/dev/null || true
aws iam delete-role --role-name trading-bot-scheduler-role 2>/dev/null || true

VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text)
SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters Name=group-name,Values=trading-bot-sg Name=vpc-id,Values="$VPC_ID" \
  --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || echo "None")
[ "$SG_ID" != "None" ] && [ -n "$SG_ID" ] && aws ec2 delete-security-group --region "$REGION" --group-id "$SG_ID" || true

if [ "${DELETE_BUCKET:-0}" = "1" ]; then
  aws s3 rm "s3://${DEPLOY_BUCKET}" --recursive
  aws s3api delete-bucket --bucket "$DEPLOY_BUCKET" --region "$REGION"
  echo "Deleted bucket $DEPLOY_BUCKET"
else
  echo "Left bucket $DEPLOY_BUCKET in place (pass DELETE_BUCKET=1 to remove it too)"
fi

echo "Teardown complete. Nothing billable should remain except the bucket (if kept, negligible cost)."
