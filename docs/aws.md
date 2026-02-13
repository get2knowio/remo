# AWS Setup

Spin up an EC2 instance with EBS storage for persistent home directories.

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

# Connect
remo shell
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
# Create instance with defaults (SSM access, no public IP/EBS home volume)
remo aws create

# Create with spot instance (cheaper, can be interrupted)
remo aws create --spot

# Create with custom options
remo aws create --name alice --type t3.large --region us-east-1

# Create with SSM Session Manager access (no inbound SSH port)
remo aws create --access ssm

# Create with Route53 DNS record
remo aws create --dns

# List registered instances
remo aws list

# Update dev tools on existing instance
remo aws update

# Update only specific tools
remo aws update --only zellij --only fzf

# Update but skip specific tools
remo aws update --skip docker --skip nodejs

# Show instance information
remo aws info

# Update security group with current IP (after IP change)
remo aws update-ip

# Stop instance (pause billing, keep storage)
remo aws stop

# Start a stopped instance
remo aws start

# Destroy instance (keeps EBS storage)
remo aws destroy --yes

# Destroy instance AND EBS (removes all data)
remo aws destroy --yes --remove-storage
```

### Create Options

| Option | Default | Description |
|--------|---------|-------------|
| `--name <name>` | `$USER` | Resource namespace (for multi-user support) |
| `--type <type>` | `m6a.large` | Instance type |
| `--arm`, `--graviton` | (off) | Use ARM/Graviton instance (`m6g.large`) for better price-perf |
| `--region <region>` | `us-west-2` | AWS region |
| `--spot` | (off) | Use spot instance for cost savings |
| `--dns` | (off) | Create Route53 DNS record |
| `--access <mode>` | `ssm` | Access mode: `ssm` (SSM Session Manager) or `direct` (SSH) |

### Update Options

| Option | Description |
|--------|-------------|
| `--only <tool>` | Only update specified tool (can repeat) |
| `--skip <tool>` | Skip specified tool (can repeat) |
| `--name <name>` | Resource namespace (default: `$USER`) |

Available tools: `docker`, `user_setup`, `nodejs`, `devcontainers`, `github_cli`, `fzf`, `zellij`

### Destroy Options

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation prompt |
| `--remove-storage` | Also delete the EBS volume (destroys all data) |
| `--name <name>` | Resource namespace (default: `$USER`) |

### Stop Options

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation prompt |
| `--name <name>` | Resource namespace (default: `$USER`) |

### Start Options

| Option | Description |
|--------|-------------|
| `--name <name>` | Resource namespace (default: `$USER`) |

## Features

| Feature | Description |
|---------|-------------|
| **EBS Storage** | `/home/remo` on persistent block volume, survives instance termination |
| **Auto IP Detection** | SSH allowed only from your current public IP (direct only) |
| **Elastic IP** | Stable public IP that survives instance stop/start (direct only) |
| **Stop/Start** | Pause compute billing without destroying the instance; `remo shell` auto-starts stopped instances |
| **Spot Instances** | Optional spot pricing for ~70% cost savings |
| **Multi-user** | Resources namespaced by `--name` for shared AWS accounts |
| **SSM Access** | Default zero-inbound-port access via AWS SSM Session Manager |

## Instance Types

| Type | Arch | vCPU | RAM | Price (on-demand) | Price (spot) |
|------|------|------|-----|-------------------|--------------|
| `m6a.large` | x86 | 2 | 8 GB | ~$0.086/hr (~$63/mo) | ~$0.031/hr |
| `m6a.xlarge` | x86 | 4 | 16 GB | ~$0.173/hr (~$126/mo) | ~$0.062/hr |
| `m6g.large` | ARM | 2 | 8 GB | ~$0.077/hr (~$56/mo) | ~$0.028/hr |
| `m6g.xlarge` | ARM | 4 | 16 GB | ~$0.154/hr (~$112/mo) | ~$0.055/hr |
| `t3.medium` | x86 | 2 | 4 GB | ~$0.042/hr (~$30/mo) | ~$0.013/hr |

Use `--arm` / `--graviton` for ARM instances, or `--type <type>` for any specific type.
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

## EBS Storage

Your home directory (`/home/remo`) is stored on an EBS volume:

- **Persistent**: Survives instance termination
- **Fast**: Local block storage for single-instance use

To check usage:
```bash
df -h /home/remo
```

## Security

### IP-based Access

SSH access is restricted to your current public IP when you run `remo aws create --access direct`. If your IP changes:

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
| EBS Volume | `remo-<name>-home` | Persistent home directory |

In SSM mode, remo uses the default security group and does not create an Elastic IP or the extra EBS home volume.

## Multi-user Support

Multiple users can share the same AWS account by using different `--name` values:

```bash
# Alice's environment
remo aws create --name alice

# Bob's environment
remo aws create --name bob
```

Each user gets isolated resources (instance, EBS volume, security group).

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

If interrupted, the instance stops (not terminates), preserving your EBS data.

## Route53 DNS (Optional)

If you have a Route53 hosted zone, create a DNS record:

```bash
remo aws create --dns
```

This creates `<name>.<domain>` (e.g., `alice.example.com`) pointing to your Elastic IP.

Requires `AWS_ROUTE53_ZONE_ID` and `AWS_ROUTE53_ZONE_DOMAIN` in `.env`.

## SSM Session Manager Access

SSM Session Manager is the default access mode. Use direct SSH only if you need a public IP/EBS home volume:

```bash
remo aws create --access ssm
```

### How It Works

The SSM agent on the EC2 instance phones home to AWS over outbound HTTPS. No inbound ports are opened in the security group. SSH connections are tunneled through the SSM session using a ProxyCommand. In SSM mode, remo does not allocate a public IP/Elastic IP and skips the extra EBS home volume, so /home/remo stays on the root volume and the instance must have outbound HTTPS access (for example via a NAT gateway or VPC endpoints).

### Prerequisites

1. **AWS Session Manager Plugin** — must be installed locally:
   - macOS: `brew install --cask session-manager-plugin`
   - Linux: Download from [AWS docs](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)

2. **IAM Instance Profile** — an instance profile with the `AmazonSSMManagedInstanceCore` managed policy. During `remo aws create --access ssm`, you can:
   - Select an existing profile (auto-detected from your account)
   - Let remo create one (`remo-<name>-ssm-role` / `remo-<name>-ssm-profile`)

### Connecting

```bash
# Interactive picker (shows [SSM] indicator)
remo shell

# Check instance details
remo aws info
```

### SSM vs Direct

| Feature | Direct | SSM (default) |
|---------|-----------------|-----|
| Inbound ports | SSH (22) from your IP | None |
| IP changes | Run `remo aws update-ip` | Not needed |
| Requires | SSH key | SSH key + session-manager-plugin + IAM role |
| Connection | `ssh remo@<ip>` | Via SSM ProxyCommand tunnel |
| Public IP / EIP | Elastic IP by default | None |
| Home volume | EBS `/home/remo` (persistent) | EBS `/home/remo` (persistent) |

### Port Forwarding via SSM

To forward a port through SSM (e.g., for a web server on port 8080):

```bash
aws ssm start-session \
  --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8080"],"localPortNumber":["8080"]}'
```

### Cleanup

When you run `remo aws destroy`, IAM resources created by remo (role and instance profile) are automatically cleaned up. User-selected IAM profiles are left untouched.

## Troubleshooting

**"AWS credentials not configured"?**
Verify `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are set in `~/.remo/.env`.

**"No default VPC found"?**
Create a default VPC in the AWS Console: VPC → Your VPCs → Actions → Create default VPC.

**SSH connection refused after IP change (direct mode)?**
Run `remo aws update-ip` to update the security group with your current IP.

**Spot instance terminated?**
Spot instances can be interrupted by AWS. Your EBS data is preserved. Run `remo aws create --spot` again.

**boto3 not found?**
Run `remo init` to install Python dependencies.

**SSM agent not coming online?**
The SSM agent may take 2-5 minutes to register after instance launch. Ensure the IAM instance profile has the `AmazonSSMManagedInstanceCore` policy and the instance has outbound HTTPS access.

**"session-manager-plugin is not installed"?**
Install the AWS Session Manager Plugin. On macOS: `brew install --cask session-manager-plugin`. See [AWS docs](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) for other platforms.
