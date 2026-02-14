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

# Edit .env with your Hetzner token
vim ~/.remo/.env

# Provision server
remo hetzner create

# Connect
remo shell
```

## Configuration

Add to your `~/.remo/.env` file:

```bash
# Required - Hetzner Cloud API Token
# Get from: https://console.hetzner.cloud/projects/<project-id>/security/tokens
HETZNER_API_TOKEN=your-hetzner-api-token
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

# Destroy server (keeps persistent volume)
remo hetzner destroy --yes

# Destroy server AND volume (removes all data)
remo hetzner destroy --yes --remove-volume
```

### Create Options

| Option | Default | Description |
|--------|---------|-------------|
| `--name <name>` | `remote-coding-server` | Server name |
| `--type <type>` | `cx22` | Server type (see [Hetzner pricing](https://www.hetzner.com/cloud)) |
| `--location <loc>` | `hel1` | Datacenter: `fsn1`, `nbg1`, `hel1`, `ash`, `hil` |

### Update Options

| Option | Description |
|--------|-------------|
| `--only <tool>` | Only update specified tool (can repeat) |
| `--skip <tool>` | Skip specified tool (can repeat) |

Available tools: `docker`, `user_setup`, `nodejs`, `devcontainers`, `github_cli`, `fzf`, `zellij`

### Destroy Options

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation prompt |
| `--remove-volume` | Also delete the persistent volume (destroys all data) |

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
