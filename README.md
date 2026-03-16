# Remo

Spin up a fully-configured remote development environment in minutes. One command gives you a persistent, secure coding environment with Dev Containers support — perfect for long-running AI agents that keep working after you disconnect.

## Installation

```bash
# From PyPI (recommended)
uv tool install remo-cli

# Or with pip
pip install remo-cli

# Initialize (installs Ansible collections)
remo init
```

### Prerequisites

- Python 3.11+
- SSH key pair (`~/.ssh/id_rsa`)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

---

## Quick Start

```bash
remo hetzner create             # Provision a VM (or: remo aws create / remo incus create)
remo shell                      # Connect to your environment
```

You land in an interactive project menu. Pick a project, and you're in a persistent Zellij session with your DevContainer already running. Disconnect anytime — your session survives.

```
  Remote Coding Server
  --------------------

> my-project - active
  another-project
  [Clone new repo]
  [Exit to shell]
```

---

## Choose Your Platform

| | [Hetzner Cloud](docs/hetzner.md) | [AWS](docs/aws.md) | [Incus](docs/incus.md) |
|---|---|---|---|
| **Type** | Cloud VM | Cloud VM | Local container |
| **Location** | EU/US datacenters | Global regions | Your hardware |
| **Cost** | ~€4/month | ~$30/month (~$10 spot) | Your electricity |
| **Storage** | Block volume | EBS / root volume | Host mounts |
| **Access** | Server IP | SSM (no inbound ports) or SSH | LAN hostname |
| **Best for** | EU, budget hosting | US, enterprise, spot instances | Local dev, homelab |

All platforms give you the same dev workflow and tooling described below.

---

## The Dev Workflow

### Persistent Sessions

[Zellij](https://zellij.dev/) keeps your terminal sessions alive across SSH disconnects:

- **Detach**: `Ctrl+d` returns to the project menu
- **Reconnect**: SSH back in, select the same project to resume exactly where you left off

### Project Menu

The `fzf`-powered menu shows your projects from `~/projects`:

- **Arrow keys** or **1-9**: Select a project
- **Enter**: Launch/attach to the project's Zellij session
- **c**: Clone a new repository
- **x**: Exit to shell

### Port Forwarding

Forward remote ports to your local machine during SSH sessions:

```bash
remo shell -L 8080                  # Forward remote :8080 to local :8080
remo shell -L 9000:8080             # Forward remote :8080 to local :9000
remo shell -L 8080 -L 3000          # Forward multiple ports
remo shell -L 8080 --no-open        # Skip auto-opening browser
```

Web ports automatically open in your browser when the tunnel is established.

### File Transfer

Copy files between your local machine and any remote environment:

```bash
remo cp ./file.txt :/tmp/           # Upload
remo cp :/var/log/app.log ./        # Download
remo cp -r ./my-dir :/home/remo/    # Recursive upload
remo cp --progress big-file.tar :/tmp/  # Show transfer progress
```

Uses colon notation — a bare `:path` targets your default environment, or `name:path` for a specific one.

### Version Checking

Remo checks that your local CLI and remote environment are running compatible versions before connecting. If the remote is behind, you'll be prompted to update it. Use `--no-update-check` to skip.

---

## What's Installed

Every remo environment includes:

| Tool | Description |
|------|-------------|
| **Docker + Compose** | Official Docker CE with compose plugin |
| **Dev Containers CLI** | `devcontainer up`, `devcontainer exec`, etc. |
| **Node.js 24 LTS** | From NodeSource repository |
| **GitHub CLI** | `gh` for GitHub workflow integration |
| **Zellij** | Terminal multiplexer for persistent sessions |
| **fzf** | Fuzzy finder powering the project menu (server-side) |

---

## CLI Reference

```bash
# Connect to environment
remo shell                          # Auto-connect (or picker if multiple)
remo shell my-env                   # Connect to a specific environment
remo shell -L 8080                  # Shell + forward remote :8080 to local :8080
remo shell -L 9000:8080             # Shell + forward remote :8080 to local :9000
remo shell -L 8080 -L 3000          # Shell + forward multiple ports
remo shell -L 8080 --no-open        # Skip auto-opening browser
remo shell --no-update-check        # Skip version check

# File transfer
remo cp ./file.txt :/tmp/           # Upload file
remo cp :/var/log/app.log ./        # Download file
remo cp -r ./dir :/home/remo/       # Recursive copy
remo cp --progress big.tar :/tmp/   # Show progress

# Setup
remo init                           # Install Ansible collections

# Hetzner Cloud
remo hetzner create                 # Provision VM
remo hetzner list                   # List registered VMs
remo hetzner sync                   # Discover existing VMs
remo hetzner update                 # Update dev tools
remo hetzner destroy [--yes]        # Tear down (keeps volume)

# AWS (SSM access — no inbound ports)
remo aws create                     # Provision EC2 via SSM
remo aws create --spot              # Use spot instance (~70% savings)
remo aws list                       # List registered instances
remo aws sync                       # Discover existing instances
remo aws update                     # Update dev tools
remo aws stop [--yes]               # Stop instance (pause billing)
remo aws start                      # Start a stopped instance
remo aws reboot                     # Reboot instance
remo aws destroy [--yes]            # Tear down (keeps storage)
remo aws info                       # Show instance info

# Incus Containers
remo incus create --name <n> [--host H]  # Create container
remo incus list                     # List registered containers
remo incus sync [--host H]          # Discover existing containers
remo incus update --name <n>        # Update dev tools
remo incus destroy --name <n> [--yes]    # Destroy container
remo incus bootstrap                # Initialize Incus on host

# Updates
uv tool upgrade remo-cli            # Update CLI to latest version
remo <platform> update              # Update dev tools on remote

# Help
remo --help
remo <command> --help
```

See platform-specific docs for full options:
- [Hetzner Cloud](docs/hetzner.md)
- [AWS](docs/aws.md)
- [Incus Containers](docs/incus.md)

### Environment Variables

| Variable | Description |
|----------|-------------|
| `REMO_HOME` | Config directory for remo state (default: `~/.config/remo`) |

---

## Troubleshooting

**Installed remo on a new machine with existing instances?**
```bash
remo aws sync       # Discover AWS instances with 'remo' tag
remo hetzner sync   # Discover Hetzner VMs with 'remo' label
remo incus sync     # Discover Incus containers
```

**SSH connection fails?**
```bash
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa
```

**Ansible collection not found?**
```bash
remo init  # Reinstalls dependencies
```

**Platform-specific issues?**
See troubleshooting sections in:
- [Hetzner troubleshooting](docs/hetzner.md#troubleshooting)
- [AWS troubleshooting](docs/aws.md#troubleshooting)
- [Incus troubleshooting](docs/incus.md#troubleshooting)

---

## Uninstalling

```bash
# Remove the CLI
uv tool uninstall remo-cli      # or: pip uninstall remo-cli

# Remove remo config and state
rm -rf ~/.config/remo
```

| Path | Contents |
|------|----------|
| `~/.config/remo/` | Runtime state: `known_hosts` (environment registry) |

**Note:** Uninstalling remo does not destroy any cloud resources (EC2 instances, Hetzner VMs, Incus containers). Run `remo <platform> destroy` first if you want to tear those down.

---

## License

MIT License - see LICENSE file for details.
