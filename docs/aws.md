# AWS Setup

Spin up an EC2 instance with EFS storage for persistent home directories.

## Prerequisites

- Python 3.8+
- SSH key pair (`~/.ssh/id_rsa`)
- [AWS account](https://aws.amazon.com/) with IAM credentials
- Default VPC in your region (most accounts have this)

## Quick Start

```bash
# Install remo
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash

# Edit .env with your AWS credentials
vim ~/.remo/.env

# Provision instance
remo aws create

# SSH in (uses auto-configured ~/.ssh/config)
ssh remo-aws
```

## Configuration

Add to your `~/.remo/.env` file:

```bash
# Required - AWS Access Key ID
# Get from: https://console.aws.amazon.com/iam/home#/security_credentials
AWS_ACCESS_KEY_ID=your-access-key-id

# Required - AWS Secret Access Key
AWS_SECRET_ACCESS_KEY=your-secret-access-key

# Optional - AWS Region (default: us-west-2)
AWS_REGION=us-west-2

# Optional - Route53 Zone (for --dns flag)
AWS_ROUTE53_ZONE_ID=Z0123456789ABCDEFGHIJ
AWS_ROUTE53_ZONE_DOMAIN=example.com
```

## CLI Commands

```bash
# Create instance with defaults
remo aws create

# Create with spot instance (cheaper, can be interrupted)
remo aws create --spot

# Create with custom options
remo aws create --name alice --type t3.large --region us-east-1

# Create with Route53 DNS record
remo aws create --dns

# Show instance information
remo aws info

# Update security group with current IP (after IP change)
remo aws update-ip

# Destroy instance (keeps EFS storage)
remo aws destroy --yes

# Destroy instance AND EFS (removes all data)
remo aws destroy --yes --remove-efs
```

### Create Options

| Option | Default | Description |
|--------|---------|-------------|
| `--name <name>` | `$USER` | Resource namespace (for multi-user support) |
| `--type <type>` | `t3.medium` | Instance type |
| `--region <region>` | `us-west-2` | AWS region |
| `--spot` | (off) | Use spot instance for cost savings |
| `--dns` | (off) | Create Route53 DNS record |

### Destroy Options

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation prompt |
| `--remove-efs` | Also delete the EFS filesystem (destroys all data) |
| `--name <name>` | Resource namespace (default: `$USER`) |

## Features

| Feature | Description |
|---------|-------------|
| **EFS Storage** | `/home/remo` on elastic filesystem, persists across instance termination |
| **Auto IP Detection** | SSH allowed only from your current public IP |
| **Elastic IP** | Stable public IP that survives instance stop/start |
| **SSH Config** | Auto-managed `~/.ssh/config` for easy `ssh remo-aws` access |
| **Spot Instances** | Optional spot pricing for ~70% cost savings |
| **Multi-user** | Resources namespaced by `--name` for shared AWS accounts |

## Instance Types

| Type | vCPU | RAM | Price (on-demand) | Price (spot) |
|------|------|-----|-------------------|--------------|
| `t3.medium` | 2 | 4 GB | ~$0.042/hr (~$30/mo) | ~$0.013/hr |
| `t3.large` | 2 | 8 GB | ~$0.083/hr (~$60/mo) | ~$0.025/hr |
| `t3.xlarge` | 4 | 16 GB | ~$0.166/hr (~$120/mo) | ~$0.050/hr |
| `m5.large` | 2 | 8 GB | ~$0.096/hr (~$70/mo) | ~$0.040/hr |

Prices vary by region. See [EC2 pricing](https://aws.amazon.com/ec2/pricing/).

## Regions

| Code | Location |
|------|----------|
| `us-west-2` | Oregon (default) |
| `us-east-1` | N. Virginia |
| `us-east-2` | Ohio |
| `eu-west-1` | Ireland |
| `eu-central-1` | Frankfurt |
| `ap-northeast-1` | Tokyo |

## EFS Storage

Your home directory (`/home/remo`) is stored on Amazon EFS:

- **Elastic**: Grows and shrinks automatically
- **Persistent**: Survives instance termination
- **Cost-optimized**: Files unused for 7 days move to Infrequent Access tier (~$0.016/GB vs $0.30/GB)

To check EFS usage:
```bash
df -h /home/remo
```

## Security

### IP-based Access

SSH access is restricted to your current public IP when you run `remo aws create`. If your IP changes:

```bash
remo aws update-ip
```

This updates the security group to allow SSH from your new IP.

### What's Created

| Resource | Name Pattern | Description |
|----------|--------------|-------------|
| EC2 Instance | `remo-<name>` | Ubuntu 24.04 instance |
| Security Group | `remo-<name>-sg` | SSH from your IP only |
| Key Pair | `remo-<name>-key` | Your SSH public key |
| Elastic IP | `remo-<name>-eip` | Stable public IP |
| EFS | `remo-<name>-home` | Persistent home directory |
| EFS Security Group | `remo-<name>-home-sg` | NFS access from EC2 |

## Multi-user Support

Multiple users can share the same AWS account by using different `--name` values:

```bash
# Alice's environment
remo aws create --name alice

# Bob's environment
remo aws create --name bob
```

Each user gets isolated resources (instance, EFS, security group).

## Spot Instances

Spot instances offer ~70% savings but can be interrupted with 2 minutes notice:

```bash
remo aws create --spot
```

Good for:
- Development work (save often, use git)
- Cost-sensitive workloads
- Interruptible tasks

Not recommended for:
- Long-running processes without checkpointing
- Production workloads

If interrupted, the instance stops (not terminates), preserving your EFS data.

## Route53 DNS (Optional)

If you have a Route53 hosted zone, create a DNS record:

```bash
remo aws create --dns
```

This creates `<name>.<domain>` (e.g., `alice.example.com`) pointing to your Elastic IP.

Requires `AWS_ROUTE53_ZONE_ID` and `AWS_ROUTE53_ZONE_DOMAIN` in `.env`.

## Troubleshooting

**"AWS credentials not configured"?**
Verify `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are set in `~/.remo/.env`.

**"No default VPC found"?**
Create a default VPC in the AWS Console: VPC → Your VPCs → Actions → Create default VPC.

**SSH connection refused after IP change?**
Run `remo aws update-ip` to update the security group with your current IP.

**Spot instance terminated?**
Spot instances can be interrupted by AWS. Your EFS data is preserved. Run `remo aws create --spot` again.

**EFS mount fails?**
Ensure the instance and EFS are in the same region and the security groups allow NFS traffic.

**boto3 not found?**
Run `remo init` to install Python dependencies.
