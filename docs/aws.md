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

# Configure AWS credentials (choose one method)
aws configure                  # Interactive setup (stores in ~/.aws/)
# or
export AWS_PROFILE=your-profile  # Use an existing named profile / SSO

# Provision instance
remo aws create

# Connect
remo shell
```

## Configuration

remo uses standard AWS credential resolution (`~/.aws/credentials`, `~/.aws/config`, environment variables). Configure with any of these methods:

```bash
# Option 1: AWS CLI interactive setup (recommended)
aws configure

# Option 2: Named profile / SSO
export AWS_PROFILE=your-profile

# Option 3: Environment variables
export AWS_ACCESS_KEY_ID=your-access-key-id
export AWS_SECRET_ACCESS_KEY=your-secret-access-key
export AWS_REGION=us-west-2           # Optional (default: us-west-2)
```

## CLI Commands

```bash
# Create instance with defaults (SSM access, no inbound ports)
remo aws create

# Create with spot instance (cheaper, can be interrupted)
remo aws create --spot

# Create with custom options
remo aws create --name alice --type t3.large --region us-east-1

# List registered instances
remo aws list

# Update dev tools on existing instance
remo aws update

# Update only specific tools
remo aws update --only zellij --only fzf

# Update but skip specific tools
remo aws update --skip docker --skip nodejs

# Grow the persistent EBS volume (and the filesystem) in place
remo aws update --volume-size 100

# Show instance information (type, cores, memory, EBS volume size)
remo aws info

# Stop instance (pause billing, keep storage)
remo aws stop

# Start a stopped instance
remo aws start

# Destroy instance (keeps EBS storage)
remo aws destroy --yes

# Destroy instance AND EBS (removes all data)
remo aws destroy --yes --remove-storage

# Snapshots
remo aws snapshot create <instance>                 # Auto-named (remo-YYYYMMDD-HHMMSS)
remo aws snapshot create <instance> --name pre-x --description "before upgrade"
remo aws snapshot list <instance>                   # Single instance
remo aws snapshot list                              # All registered AWS instances
remo aws snapshot restore <instance> <snap-name> [-y]   # In-place EBS volume swap
remo aws snapshot delete <instance> <snap-name> [-y]    # Remove snapshot
```

### Create Options

| Option | Default | Description |
|--------|---------|-------------|
| `--name <name>` | `$USER` | Resource namespace (for multi-user support) |
| `--type <type>` | `m6a.large` | Instance type |
| `--region <region>` | `us-west-2` | AWS region |
| `--spot` | (off) | Use spot instance for cost savings |
| `--iam-profile <name>` | (auto) | Use existing IAM instance profile (skips discovery) |

### Update Options

| Option | Description |
|--------|-------------|
| `--only <tool>` | Only update specified tool (can repeat) |
| `--skip <tool>` | Skip specified tool (can repeat) |
| `--volume-size <GB>` | Grow the persistent EBS volume to this size and grow the ext4 filesystem in place via SSH-over-SSM. AWS only supports growing. |
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

### Snapshot Options

| Option | Applies to | Description |
|--------|------------|-------------|
| `--name <snap>` | `create` | Snapshot name. Defaults to `remo-YYYYMMDD-HHMMSS`. Validated client-side: 1–40 chars, `^[A-Za-z0-9][A-Za-z0-9_-]*$`. |
| `--description <text>` | `create` | Free-text description stored on the EBS snapshot and shown in `list`. |
| `--region <region>` | all | AWS region. Defaults to the registered instance's region. |
| `--yes`, `-y` | `restore`, `delete` | Bypass the confirmation prompt. |

### Snapshot Behavior

* **Create is asynchronous.** The command returns within a few seconds with a "creation started; will take several minutes" hint. Use `snapshot list` to watch the `STATUS` column transition from `pending` to `available`. Restore and delete refuse to operate on pending snapshots.
* **Restore is an in-place volume swap.** The instance is stopped, the root EBS volume is detached, a new volume is created from the snapshot, attached as root, and the instance is restarted. Typical downtime: 2-5 minutes.
* **Restored volume preserves current size.** If you grew the EBS volume after taking the snapshot, the restored volume is created at the current (larger) size. The filesystem inside stays at the snapshot's recorded size until you run `sudo resize2fs $(findmnt -no SOURCE /)` inside the instance — `remo` prints this hint on restore.
* **Pre-restore volume is preserved as an orphan.** After a successful restore, the pre-restore root volume is *not* deleted; it stays in the AZ tagged `remo-restore-orphan=<ISO-8601-timestamp>` as a safety net. Once you've verified the restore is healthy, delete it manually with `aws ec2 delete-volume --volume-id <id>`. `remo` prints the exact command on restore.
* **Snapshots are scoped by volume ID.** Snapshots are listed by filtering on the *current* root volume ID, so if you destroy an instance and create a new one with the same name, the old snapshots will not appear in `list` (they become orphans, invisible to `remo`, manageable only via the AWS console).
* **No cost estimation.** `remo` does not estimate monthly EBS snapshot storage cost — check your AWS billing console.
* **Destroy-time cleanup.** `remo aws destroy` lists existing snapshots and offers to delete them as part of teardown. Declining (or passing `-y`) keeps the snapshots, which will continue to incur storage cost.

## Features

| Feature | Description |
|---------|-------------|
| **EBS Storage** | `/home/remo` on persistent block volume, survives instance termination |
| **SSM Access** | Zero-inbound-port access via AWS SSM Session Manager |
| **Stop/Start** | Pause compute billing without destroying the instance; `remo shell` auto-starts stopped instances |
| **Spot Instances** | Optional spot pricing for ~70% cost savings |
| **Multi-user** | Resources namespaced by `--name` for shared AWS accounts |
| **Patch Manager** | Automatic OS security patching via AWS SSM Patch Manager — daily scan, weekly install (Sunday 4 AM UTC) with auto-reboot |

## Instance Types

| Type | Arch | vCPU | RAM | Price (on-demand) | Price (spot) |
|------|------|------|-----|-------------------|--------------|
| `m6a.large` | x86 | 2 | 8 GB | ~$0.086/hr (~$63/mo) | ~$0.031/hr |
| `m6a.xlarge` | x86 | 4 | 16 GB | ~$0.173/hr (~$126/mo) | ~$0.062/hr |
| `m6g.large` | ARM | 2 | 8 GB | ~$0.077/hr (~$56/mo) | ~$0.028/hr |
| `m6g.xlarge` | ARM | 4 | 16 GB | ~$0.154/hr (~$112/mo) | ~$0.055/hr |
| `t3.medium` | x86 | 2 | 4 GB | ~$0.042/hr (~$30/mo) | ~$0.013/hr |

Use `--type <type>` to select any instance type (e.g., `--type m6g.large` for ARM/Graviton).
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

All instances use SSM Session Manager — no inbound ports are opened.

### IAM Permissions

The principal `remo` uses (your local IAM user, SSO session, or assumed role) needs the following EC2 actions. If you scoped your `remo` policy tightly, you'll see `UnauthorizedOperation` errors the first time you try a subcommand whose actions aren't in your policy.

| Subcommand | Required EC2 actions |
|---|---|
| `create` | `RunInstances`, `DescribeInstances`, `DescribeImages`, `DescribeVolumes`, `CreateTags`, `CreateVolume`, `DescribeSecurityGroups`, `CreateSecurityGroup`, `AuthorizeSecurityGroupIngress`, `CreateKeyPair`, `ImportKeyPair`, `DescribeKeyPairs`, `RunInstances`, `iam:PassRole`, `iam:GetRole`, `iam:CreateRole`, `iam:AttachRolePolicy`, `iam:GetInstanceProfile`, `iam:CreateInstanceProfile`, `iam:AddRoleToInstanceProfile` |
| `destroy` | `DescribeInstances`, `TerminateInstances`, `DescribeVolumes`, `DeleteVolume`, `DescribeSecurityGroups`, `DeleteSecurityGroup`, `DescribeKeyPairs`, `DeleteKeyPair`, plus the IAM cleanup mirror of `create` |
| `update`, `info`, `list`, `sync` | `DescribeInstances`, `DescribeVolumes` (+ `ModifyVolume` and `DescribeVolumesModifications` for `update --volume-size`) |
| `stop`, `start`, `reboot` | `DescribeInstances`, `StopInstances` / `StartInstances` / `RebootInstances` |
| `snapshot create` | `DescribeInstances`, `DescribeVolumes`, `DescribeSnapshots`, `CreateSnapshot`, `CreateTags` |
| `snapshot list` | `DescribeInstances`, `DescribeVolumes`, `DescribeSnapshots` |
| `snapshot delete` | `DescribeInstances`, `DescribeVolumes`, `DescribeSnapshots`, `DeleteSnapshot` |
| `snapshot restore` | Everything in `snapshot list` **plus** `StopInstances`, `DetachVolume`, `CreateVolume`, `AttachVolume`, `StartInstances`, `CreateTags` |
| `destroy` (with existing snapshots, `-y` declined cleanup) | Adds `DescribeSnapshots` (to enumerate) and `DeleteSnapshot` (if cleanup accepted) on top of the regular `destroy` set |

> **Note on `CreateSnapshot` resource scoping.** AWS doesn't allow `Resource: <arn>` scoping for `ec2:CreateSnapshot` — the policy must use `Resource: "*"`. If you want to constrain `remo` to snapshotting only its own volumes, add a `Condition` like `"StringEquals": {"ec2:ResourceTag/remo": "true"}` on the source-volume condition key.

#### Minimum snapshot-only policy (attach as an inline policy to your `remo` role)

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "ec2:CreateSnapshot",
      "ec2:DescribeSnapshots",
      "ec2:DeleteSnapshot",
      "ec2:CreateTags"
    ],
    "Resource": "*"
  }]
}
```

For restore, also add `ec2:StopInstances`, `ec2:DetachVolume`, `ec2:CreateVolume`, `ec2:AttachVolume`, `ec2:StartInstances`.

### What's Created

| Resource | Name Pattern | Description |
|----------|--------------|-------------|
| EC2 Instance | `remo-<name>` | Ubuntu 24.04 instance |
| Security Group | `remo-<name>-sg` | No inbound rules (SSM only) |
| Key Pair | `remo-<name>-key` | Your SSH public key |
| EBS Volume | `remo-<name>-home` | Persistent home directory |
| IAM Role | `remo-<name>-ssm-role` | SSM access (if created by remo) |

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

## SSM Session Manager

All instances use SSM Session Manager — no inbound ports are opened. The SSM agent on the EC2 instance phones home to AWS over outbound HTTPS. SSH connections are tunneled through the SSM session using a ProxyCommand.

### Prerequisites

1. **AWS Session Manager Plugin** — must be installed locally:
   - macOS: `brew install --cask session-manager-plugin`
   - Linux: Download from [AWS docs](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)

2. **IAM Instance Profile** — an instance profile with the `AmazonSSMManagedInstanceCore` managed policy. During `remo aws create`, you can:
   - Select an existing profile (auto-detected from your account)
   - Let remo create one (`remo-<name>-ssm-role` / `remo-<name>-ssm-profile`)

### Connecting

```bash
remo shell

# Check instance details
remo aws info
```

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
Run `aws configure` or set `AWS_PROFILE` to a configured profile.

**"No default VPC found"?**
Create a default VPC in the AWS Console: VPC → Your VPCs → Actions → Create default VPC.

**Spot instance terminated?**
Spot instances can be interrupted by AWS. Your EBS data is preserved. Run `remo aws create --spot` again.

**boto3 not found?**
Run `remo init` to install Python dependencies.

**SSM agent not coming online?**
The SSM agent may take 2-5 minutes to register after instance launch. Ensure the IAM instance profile has the `AmazonSSMManagedInstanceCore` policy and the instance has outbound HTTPS access.

**"session-manager-plugin is not installed"?**
Install the AWS Session Manager Plugin. On macOS: `brew install --cask session-manager-plugin`. See [AWS docs](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) for other platforms.
