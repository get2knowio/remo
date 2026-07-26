# Hetzner Cloud Setup

Spin up a cloud VM with full dev tooling and persistent storage.

## Prerequisites

- Python 3.8+
- SSH key pair (`~/.ssh/id_rsa`)
- [Hetzner Cloud](https://www.hetzner.com/cloud) account + API token

## Quick Start

```bash
# Install remo
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash

# Set your Hetzner API token
export HETZNER_API_TOKEN=your-hetzner-api-token

# Provision server
remo hetzner create

# Connect
remo shell
```

## Configuration

Set the `HETZNER_API_TOKEN` environment variable. Get your token from the [Hetzner Cloud Console](https://console.hetzner.cloud/) under Security → API Tokens.

```bash
# Add to your shell profile (~/.bashrc, ~/.zshrc, etc.)
export HETZNER_API_TOKEN=your-hetzner-api-token
```

## CLI Commands

```bash
# Create server with defaults
remo hetzner create

# Create with custom options
remo hetzner create --name my-server --type cx32 --location fsn1

# List registered servers
remo hetzner list

# Update dev tools on existing server
remo hetzner update

# Update only specific tools
remo hetzner update --only zellij --only fzf

# Update but skip specific tools
remo hetzner update --skip docker --skip nodejs

# Grow the persistent volume (and the filesystem) in place
remo hetzner update --volume-size 100

# Inspect resources on an existing server (type, cores, memory, volume size)
remo hetzner info

# Destroy server (keeps persistent volume)
remo hetzner destroy --yes

# Destroy server AND volume (removes all data)
remo hetzner destroy --yes --remove-volume
```

### Create Options

| Option | Default | Description |
|--------|---------|-------------|
| `--name <name>` | `remo` | Server name |
| `--type <type>` | `cx22` | Server type (see [Hetzner pricing](https://www.hetzner.com/cloud)) |
| `--location <loc>` | `hel1` | Datacenter: `fsn1`, `nbg1`, `hel1`, `ash`, `hil` |

### Update Options

| Option | Description |
|--------|-------------|
| `--only <tool>` | Only update specified tool (can repeat) |
| `--skip <tool>` | Skip specified tool (can repeat) |
| `--volume-size <GB>` | Grow the persistent Hetzner volume to this size and grow the ext4 filesystem in place. Hetzner only supports growing. |
| `--name <name>` | Server name (default: `remo`) |

Available tools: `docker`, `user_setup`, `nodejs`, `devcontainers`, `github_cli`, `fzf`, `zellij`

### Destroy Options

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation prompt |
| `--remove-volume` | Also delete the persistent volume (destroys all data) |

### Sync

```bash
# Reconcile the registry with every server in the Hetzner Cloud project
remo hetzner sync

# Also adopt servers without the remo label
remo hetzner sync --all

# Skip the removal confirmation prompt
remo hetzner sync --yes

# Preview the plan without changing the registry or prompting
remo hetzner sync --dry-run
```

`sync` reconciles the registry against every server in the project (Hetzner
has no per-node/per-region boundary to scope to). Servers still present in
the project are never removed just because they lack the `remo` label —
only a server that has genuinely disappeared, in a fully-paginated listing,
is proposed for removal, and that removal always requires confirmation (or
`--yes`) first. `--dry-run` prints the same plan without prompting or
changing anything, and always exits `0`.

By default only servers carrying the `remo` label are *added*; `--all`
widens that to every server in the project for this run only — Hetzner has
no naming convention to narrow to, so `--all` here is deliberately broad.

The `remo` label is applied automatically at `create` time. A server created
before this label existed (or one whose label was somehow lost) can be
backfilled permanently — without disturbing any of its other labels — by
running `remo hetzner update`.

| Option | Description |
|--------|-------------|
| `--all` | Also register servers without the managed label (this run only) |
| `--yes`, `-y` | Skip the removal confirmation prompt |
| `--dry-run` | Show what would change without writing to the registry |

## Features

| Feature | Description |
|---------|-------------|
| **Persistent Volume** | `/home/remo` mounted on a separate volume that survives server teardown |
| **Strict Firewall** | SSH-only access (port 22) |
| **Ubuntu 24.04** | Latest LTS with automatic security updates |

## Server Types

| Type | vCPU | RAM | Disk | Price |
|------|------|-----|------|-------|
| `cx22` | 2 | 4 GB | 40 GB | ~€4/month |
| `cx32` | 4 | 8 GB | 80 GB | ~€8/month |
| `cx42` | 8 | 16 GB | 160 GB | ~€16/month |

See [Hetzner Cloud pricing](https://www.hetzner.com/cloud) for full list.

## Locations

| Code | Location |
|------|----------|
| `fsn1` | Falkenstein, Germany |
| `nbg1` | Nuremberg, Germany |
| `hel1` | Helsinki, Finland |
| `ash` | Ashburn, USA |
| `hil` | Hillsboro, USA |

## GitHub Actions (Alternative)

Fork this repo and use GitHub Actions to provision without local setup:

1. **Add secrets** in Settings → Secrets → Actions:
   - `HETZNER_API_TOKEN`, `SSH_PRIVATE_KEY`, `SSH_PUBLIC_KEY`

2. **Run**: Actions → Provision Server → Run workflow → type `yes`

## Troubleshooting

**Hetzner API errors?**
Verify your API token has read/write permissions in the Hetzner Cloud Console.

**SSH connection refused?**
The server may still be initializing. Wait 1-2 minutes after provisioning.

**Volume not mounting?**
Check the volume exists in Hetzner Console and is in the same location as the server.
