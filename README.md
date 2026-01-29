# Remo

Spin up a fully-configured development environment in minutes. One command gives you a persistent, secure coding environment with Dev Containers support.

## Installation

```bash
# Install latest stable version
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash

# Install latest pre-release (for testing new features)
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash -s -- --pre-release
```

After installation, `remo` is available in `~/.local/bin`. Update with:

```bash
remo self-update
```

---

## Choose Your Platform

| | [Hetzner Cloud](docs/hetzner.md) | [AWS](docs/aws.md) | [Incus](docs/incus.md) |
|---|---|---|---|
| **Type** | Cloud VM | Cloud VM | Local container |
| **Location** | EU/US datacenters | Global regions | Your hardware |
| **Cost** | ~€4/month | ~$30/month | Your electricity |
| **Storage** | Block volume | EFS (elastic) | Host mounts |
| **Access** | DuckDNS domain | Elastic IP / Route53 | LAN hostname |
| **Best for** | EU, budget hosting | US, enterprise, elastic storage | Local dev, homelab |

All platforms give you the same dev workflow and tooling described below.

---

## The Dev Workflow

SSH in and you're greeted with an interactive project menu:

```
ssh remo@your-host

  Remote Coding Server
  --------------------

> my-project - active
  another-project
  [Clone new repo]
  [Exit to shell]
```

Select a project and you're in a persistent Zellij session. Devcontainer projects auto-start their container. Disconnect anytime—your session survives.

### Project Menu

The `fzf`-powered menu shows your projects from `~/projects`:

- **Arrow keys** or **1-9**: Select a project
- **Enter**: Launch/attach to the project's Zellij session
- **c**: Clone a new repository
- **x**: Exit to shell

### Persistent Sessions

[Zellij](https://zellij.dev/) keeps your terminal sessions alive:

- **Detach**: `Ctrl+d` returns to the project menu
- **Reconnect**: SSH back in, select the same project to resume

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
| **fzf** | Fuzzy finder powering the project menu |

---

## CLI Quick Reference

```bash
# Connect to environment
remo shell                          # Auto-connect (or picker if multiple)

# Setup
remo init                           # Install dependencies, create .env

# Hetzner Cloud
remo hetzner create                 # Provision VM
remo hetzner list                   # List registered VMs
remo hetzner sync                   # Discover existing VMs
remo hetzner update                 # Update dev tools
remo hetzner destroy [--yes]        # Tear down (keeps volume)

# AWS
remo aws create [--spot]            # Provision EC2 + EFS
remo aws list                       # List registered instances
remo aws sync                       # Discover existing instances
remo aws update                     # Update dev tools
remo aws destroy [--yes]            # Tear down (keeps EFS)
remo aws update-ip                  # Update security group with current IP
remo aws info                       # Show instance info

# Incus Containers
remo incus create <name> [--host H] # Create container
remo incus list                     # List registered containers
remo incus sync [--host H]          # Discover existing containers
remo incus update <name>            # Update dev tools
remo incus destroy <name> [--yes]   # Destroy container
remo incus bootstrap                # Initialize Incus on host

# Updates
remo self-update                    # Update to latest version

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

## License

MIT License - see LICENSE file for details.
