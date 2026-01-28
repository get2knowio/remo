# Incus Container Setup

Spin up a lightweight system container on your own hardware. Containers get IPs from your LAN's DHCP and are accessible by hostname from any machine on your network.

## Prerequisites

- Incus installed and bootstrapped on your host (see [Bootstrap](#bootstrap) below)
- SSH key pair (`~/.ssh/id_rsa`)

## Quick Start

```bash
# Install remo (on your workstation)
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash

# Create and configure container
remo incus create dev1 --host incus-host --user youruser

# Connect
remo shell
```

## Configuration

No `.env` configuration needed for Incus. Authentication uses your SSH key.

## CLI Commands

```bash
# Create container on remote host
remo incus create dev1 --host myserver --user paul

# Create container on localhost
remo incus create dev1

# Create with domain for FQDN
remo incus create dev1 --domain int.example.com

# List registered containers
remo incus list

# Update dev tools on existing container
remo incus update dev1 --host myserver --user paul

# Update only specific tools
remo incus update dev1 --only zellij --only fzf

# Skip specific tools during update
remo incus update dev1 --skip docker --skip nodejs

# Destroy container
remo incus destroy dev1 --host myserver --user paul --yes

# Bootstrap Incus on a host
remo incus bootstrap --host myserver --user paul
```

### Create Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host <host>` | `localhost` | Incus host to connect to |
| `--user <user>` | (current user) | SSH user for the Incus host |
| `--domain <domain>` | (none) | Domain for FQDN (e.g., `int.example.com`) |
| `--image <image>` | `images:ubuntu/24.04/cloud` | Cloud image to use |

### Update Options

| Option | Description |
|--------|-------------|
| `--only <tool>` | Only update specified tool (can repeat) |
| `--skip <tool>` | Skip specified tool (can repeat) |
| `--host <host>` | Incus host |
| `--user <user>` | SSH user for host |

Available tools: `docker`, `user_setup`, `nodejs`, `devcontainers`, `github_cli`, `fzf`, `zellij`

### Destroy Options

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation prompt |
| `--host <host>` | Incus host |
| `--user <user>` | SSH user for host |

## Features

| Feature | Description |
|---------|-------------|
| **System Container** | Lightweight, near-native performance |
| **LAN IP via DHCP** | Accessible from any machine on your network |
| **Hostname DNS** | Works if your router registers DHCP hostnames |
| **Host Mounts** | Optional persistent data directories from the Incus host |
| **macvlan Network** | Containers appear as separate devices on your LAN |

## Bootstrap

**Skip this if you already have Incus installed and initialized.**

To use Incus containers, first bootstrap Incus on your host machine:

### Bootstrap a Remote Host

```bash
remo incus bootstrap --host 192.168.1.100 --user paul
```

### Bootstrap Localhost

```bash
remo incus bootstrap
```

### What Bootstrap Does

- Installs Incus packages (OpenSUSE Tumbleweed)
- Enables and starts Incus daemon
- Adds your user to `incus-admin` group
- Creates directory-based storage pool
- Configures macvlan network (containers get LAN IPs via DHCP)

**After bootstrap**, log out and back in (or `newgrp incus-admin`) to activate group membership.

### Bootstrap Options

```bash
# Verbose output
remo incus bootstrap --verbose

# For advanced options, use the ansible playbook directly:
cd ~/.remo/ansible
./run.sh incus_bootstrap.yml -e "incus_network_parent=eth0"
./run.sh incus_bootstrap.yml -e "incus_network_type=bridge"
```

## Networking

### macvlan (Default)

Containers get IPs directly from your LAN's DHCP server and appear as separate devices:

```
Your LAN (192.168.1.0/24)
├── Router (192.168.1.1)
├── Your PC (192.168.1.10)
├── Incus Host (192.168.1.20)
├── Container dev1 (192.168.1.101)  ← Direct LAN IP
└── Container dev2 (192.168.1.102)
```

**Limitation**: The Incus host cannot directly reach containers via macvlan. Access containers from a different machine on your LAN.

### Hostname Resolution

If your router registers DHCP hostnames (common with OpenWrt, pfSense, etc.):

```bash
ssh remo@dev1                    # Short hostname
ssh remo@dev1.int.example.com    # FQDN (if --domain set)
```

## Host Mounts

Mount directories from the Incus host into containers for persistent storage:

```bash
# Via ansible directly (advanced)
cd ~/.remo/ansible
./run.sh incus_site.yml \
  -e "container_name=dev1" \
  -e 'incus_container_mounts=[{"source": "/data/projects", "path": "/home/remo/projects"}]'
```

## Cloud Images

| Image | Description |
|-------|-------------|
| `images:ubuntu/24.04/cloud` | Ubuntu 24.04 LTS (default) |
| `images:ubuntu/22.04/cloud` | Ubuntu 22.04 LTS |
| `images:debian/12/cloud` | Debian 12 Bookworm |
| `images:rockylinux/9/cloud` | Rocky Linux 9 |

Browse available images: `incus image list images:`

## Troubleshooting

**Container not accessible by hostname?**
- Verify your router/DHCP server registers hostnames
- DNS registration may take a few seconds after container boot
- Try by IP first: `incus list` shows container IPs

**Can't reach container from Incus host?**
This is a known macvlan limitation. Access containers from a different machine on your LAN.

**"Permission denied" on incus commands?**
Log out and back in after bootstrap, or run `newgrp incus-admin`.

**Container stuck in "Starting"?**
Check Incus logs: `incus info dev1 --show-log`

**DHCP not assigning IP?**
Verify your network has a DHCP server and the macvlan interface is configured correctly:
```bash
incus network show incusbr0
```

**SSH connection fails?**
Ensure your SSH public key exists at `~/.ssh/id_rsa.pub` and is readable.
