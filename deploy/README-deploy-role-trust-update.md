# One-time admin step: let codex-deploy.yml assume the existing deploy role

Codex reuses the existing `trading-bot-github-deploy-role` (same OIDC
provider, same permissions - it already has S3 write access to
`trading-bot/*` in the deploy bucket, SSM SendCommand on this exact
instance, and EC2 start/stop) rather than provisioning a new role.

That role's trust policy currently only accepts an OIDC token whose
`sub` claim matches `ref:refs/heads/main`. Until it's widened to also
accept `codex-bot-main`, `.github/workflows/codex-deploy.yml`'s
"Configure AWS credentials via OIDC" step will fail with
`AccessDenied`/`InvalidIdentityToken`. Manual SSM deploys (what this
project used to bring codex up initially) work regardless - this only
gates the automated CI path for future code changes.

Run once, with AWS credentials that have `iam:UpdateAssumeRolePolicy`
(the `trading-bot-deployer` user this project's CLI normally uses does
NOT have this - needs a broader/admin identity, same constraint noted
earlier for full new-infra provisioning):

```bash
aws iam update-assume-role-policy \
  --role-name trading-bot-github-deploy-role \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {"Federated": "arn:aws:iam::396913392704:oidc-provider/token.actions.githubusercontent.com"},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
          "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
          "StringLike": {
            "token.actions.githubusercontent.com:sub": [
              "repo:Mahendrakoppula@87843354/tradingBot@1360385816:ref:refs/heads/main",
              "repo:Mahendrakoppula@87843354/tradingBot@1360385816:ref:refs/heads/codex-bot-main"
            ]
          }
        }
      }
    ]
  }'
```

After this, a push to `codex-bot-main` will deploy automatically via
`codex-deploy.yml`, exactly like pushes to `main` already do.
