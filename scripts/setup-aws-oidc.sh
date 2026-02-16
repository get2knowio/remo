#!/usr/bin/env bash
# setup-aws-oidc.sh — Configure AWS OIDC federation for GitHub Actions CI
#
# Creates:
#   1. An OIDC identity provider in your AWS account (if not already present)
#   2. An IAM role that GitHub Actions can assume via OIDC
#   3. An inline policy granting EC2 + SSM permissions for smoke tests
#   4. A GitHub repository secret (AWS_OIDC_ROLE_ARN)
#
# Prerequisites:
#   - aws CLI, authenticated with IAM admin permissions
#   - gh CLI, authenticated with repo admin access
#   - openssl (for OIDC thumbprint)
#
# Usage:
#   ./scripts/setup-aws-oidc.sh
#   ./scripts/setup-aws-oidc.sh --role-name my-custom-name
#   ./scripts/setup-aws-oidc.sh --dry-run

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================
GITHUB_REPO="get2knowio/remo"
OIDC_PROVIDER="token.actions.githubusercontent.com"
ROLE_NAME="remo-ci-github-actions"
POLICY_NAME="remo-ci-permissions"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --role-name)  ROLE_NAME="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=true; shift ;;
    -h|--help)
      echo "Usage: $0 [--role-name NAME] [--dry-run]"
      echo ""
      echo "Options:"
      echo "  --role-name NAME   IAM role name (default: remo-ci-github-actions)"
      echo "  --dry-run          Print what would be done without making changes"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# ============================================================================
# Preflight checks
# ============================================================================
for cmd in aws gh openssl; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: '$cmd' is required but not found in PATH." >&2
    exit 1
  fi
done

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "AWS Account: $AWS_ACCOUNT_ID"
echo "GitHub Repo: $GITHUB_REPO"
echo "Role Name:   $ROLE_NAME"
echo ""

if $DRY_RUN; then
  echo "[dry-run] Would create OIDC provider, IAM role, and GitHub secret."
  echo "[dry-run] No changes will be made."
  echo ""
fi

# ============================================================================
# Step 1: Create OIDC Identity Provider
# ============================================================================
PROVIDER_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/${OIDC_PROVIDER}"

if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER_ARN" &>/dev/null; then
  echo "OIDC provider already exists — skipping."
else
  echo "Creating OIDC identity provider..."
  THUMBPRINT=$(openssl s_client -connect "${OIDC_PROVIDER}:443" -servername "$OIDC_PROVIDER" \
    </dev/null 2>/dev/null \
    | openssl x509 -fingerprint -noout -sha1 \
    | sed 's/.*=//' | tr -d ':' | tr '[:upper:]' '[:lower:]')

  if $DRY_RUN; then
    echo "[dry-run] aws iam create-open-id-connect-provider (thumbprint: ${THUMBPRINT})"
  else
    aws iam create-open-id-connect-provider \
      --url "https://${OIDC_PROVIDER}" \
      --client-id-list sts.amazonaws.com \
      --thumbprint-list "$THUMBPRINT"
    echo "OIDC provider created."
  fi
fi

# ============================================================================
# Step 2: Create IAM Role with trust policy
# ============================================================================
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"

TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "${PROVIDER_ARN}"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${OIDC_PROVIDER}:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "${OIDC_PROVIDER}:sub": "repo:${GITHUB_REPO}:*"
        }
      }
    }
  ]
}
EOF
)

if aws iam get-role --role-name "$ROLE_NAME" &>/dev/null; then
  echo "IAM role '$ROLE_NAME' already exists — updating trust policy."
  if ! $DRY_RUN; then
    aws iam update-assume-role-policy \
      --role-name "$ROLE_NAME" \
      --policy-document "$TRUST_POLICY"
  fi
else
  echo "Creating IAM role '$ROLE_NAME'..."
  if $DRY_RUN; then
    echo "[dry-run] aws iam create-role --role-name $ROLE_NAME"
  else
    aws iam create-role \
      --role-name "$ROLE_NAME" \
      --assume-role-policy-document "$TRUST_POLICY" \
      --description "GitHub Actions OIDC role for ${GITHUB_REPO} CI" \
      --output text --query 'Role.Arn'
    echo "IAM role created."
  fi
fi

# ============================================================================
# Step 3: Attach inline permissions policy
# ============================================================================
PERMISSIONS_POLICY=$(cat <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2ReadOnly",
      "Effect": "Allow",
      "Action": "ec2:Describe*",
      "Resource": "*"
    },
    {
      "Sid": "EC2Write",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:StartInstances",
        "ec2:StopInstances",
        "ec2:TerminateInstances",
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceStatus",
        "ec2:ModifyInstanceAttribute",
        "ec2:ImportKeyPair",
        "ec2:CreateKeyPair",
        "ec2:DeleteKeyPair",
        "ec2:DescribeKeyPairs",
        "ec2:CreateSecurityGroup",
        "ec2:DeleteSecurityGroup",
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:AuthorizeSecurityGroupEgress",
        "ec2:RevokeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupEgress",
        "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
        "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
        "ec2:CreateTags",
        "ec2:DescribeImages",
        "ec2:DescribeSubnets",
        "ec2:DescribeVpcs",
        "ec2:DescribeVpcAttribute",
        "ec2:DescribeVolumes",
        "ec2:CreateVolume",
        "ec2:DeleteVolume",
        "ec2:AttachVolume",
        "ec2:DetachVolume",
        "ec2:ModifyVolume",
        "ec2:DescribeAddresses",
        "ec2:AllocateAddress",
        "ec2:AssociateAddress",
        "ec2:DisassociateAddress",
        "ec2:ReleaseAddress"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SSMAccess",
      "Effect": "Allow",
      "Action": [
        "ssm:StartSession",
        "ssm:TerminateSession",
        "ssm:DescribeSessions",
        "ssm:DescribeInstanceInformation",
        "ssm:ListAssociations",
        "ssm:CreateAssociation"
      ],
      "Resource": "*"
    },
    {
      "Sid": "IAMForSSMInstanceProfile",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:DeleteRole",
        "iam:GetRole",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:CreateInstanceProfile",
        "iam:DeleteInstanceProfile",
        "iam:GetInstanceProfile",
        "iam:AddRoleToInstanceProfile",
        "iam:RemoveRoleFromInstanceProfile",
        "iam:ListEntitiesForPolicy",
        "iam:ListInstanceProfilesForRole",
        "iam:PassRole"
      ],
      "Resource": "*"
    }
  ]
}
EOF
)

echo "Attaching permissions policy '${POLICY_NAME}'..."
if $DRY_RUN; then
  echo "[dry-run] aws iam put-role-policy --role-name $ROLE_NAME --policy-name $POLICY_NAME"
else
  aws iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "$POLICY_NAME" \
    --policy-document "$PERMISSIONS_POLICY"
  echo "Permissions policy attached."
fi

# ============================================================================
# Step 4: Set GitHub repository secret
# ============================================================================
echo "Setting GitHub secret AWS_OIDC_ROLE_ARN..."
if $DRY_RUN; then
  echo "[dry-run] gh secret set AWS_OIDC_ROLE_ARN --repo $GITHUB_REPO --body $ROLE_ARN"
else
  gh secret set AWS_OIDC_ROLE_ARN \
    --repo "$GITHUB_REPO" \
    --body "$ROLE_ARN"
  echo "GitHub secret set."
fi

# ============================================================================
# Done
# ============================================================================
echo ""
echo "=============================="
echo "Setup complete!"
echo "Role ARN: ${ROLE_ARN}"
echo "=============================="
echo ""
echo "The smoke-test workflow can now authenticate to AWS via OIDC."
echo "No long-lived access keys are stored anywhere."
