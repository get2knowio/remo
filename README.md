# Remo

Spin up a fully-configured development environment in minutes. One command gives you a persistent, secure coding environment with Dev Containers support.

---

## The Dev Workflow

SSH in and you're greeted with an interactive project menu:

```bash
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

On SSH login, the `fzf`-powered menu shows your projects from `~/projects`:

- **Arrow keys** or **1-9**: Select a project
- **Enter**: Launch/attach to the project's Zellij session
- **c**: Clone a new repository
- **x**: Exit to shell

Active Zellij sessions are marked in the menu.

### Persistent Sessions with Zellij

[Zellij](https://zellij.dev/) keeps your terminal sessions alive:

- **Detach**: `Ctrl+o d` returns to the project menu
- **Reconnect**: SSH back in, select the same project to resume

The host Zellij runs as an "outer" session (tab management only), so you can run an "inner" Zellij inside devcontainers without keybind conflicts.

### What's Installed

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

## Two Ways to Get Started

| | Hetzner Cloud | Incus Container |
|---|---|---|
| **Where** | Cloud VM (remote) | Container on your hardware (local/homelab) |
| **Cost** | ~€4/month | Your electricity |
| **Access** | SSH over internet via DuckDNS | SSH on your LAN by hostname |
| **Best for** | Remote work, always-on | Local development, testing |

Both give you the same dev workflow described above.

---

## CLI Quick Reference

```bash
# First time setup
./remo init

# Incus containers
./remo incus create <name> [--host <host>] [--user <user>] [--domain <domain>]
./remo incus destroy <name> [--host <host>] [--user <user>] [--yes]
./remo incus list [--host <host>] [--user <user>]
./remo incus bootstrap [--host <host>] [--user <user>]

# Hetzner VMs
./remo hetzner create [--name <name>] [--type <type>] [--location <loc>]
./remo hetzner destroy [--yes] [--remove-volume]

# Help
./remo --help
./remo incus --help
./remo hetzner --help
```

---

## Hetzner Cloud Setup

Spin up a cloud VM with full dev tooling.

### Prerequisites

- Python 3.8+
- SSH key pair (`~/.ssh/id_rsa`)
- [Hetzner Cloud](https://www.hetzner.com/cloud) account + API token
- [DuckDNS](https://www.duckdns.org/) account + token + subdomain

### Quick Start

```bash
# Clone and setup
git clone https://github.com/get2knowio/remo.git
cd remo
./remo init

# Edit .env with your Hetzner and DuckDNS tokens
vim .env

# Provision server
./remo hetzner create

# SSH in
ssh remo@your-subdomain.duckdns.org
```

### Additional Features

| Feature | Description |
|---------|-------------|
| **Persistent Volume** | `/home/remo` survives server teardown |
| **Strict Firewall** | SSH-only access (port 22) |
| **DuckDNS Domain** | Automatic DNS registration |

### GitHub Actions (Alternative)

Fork this repo and use GitHub Actions to provision without local setup:

1. **Add secrets** in Settings → Secrets → Actions:
   - `HETZNER_API_TOKEN`, `SSH_PRIVATE_KEY`, `SSH_PUBLIC_KEY`
   - `DUCKDNS_TOKEN`, `DUCKDNS_DOMAIN`

2. **Run**: Actions → Provision Server → Run workflow → type `yes`

### Teardown

```bash
./remo hetzner destroy --yes                 # Destroy server (keeps volume)
./remo hetzner destroy --yes --remove-volume # Destroy everything
```

---

## Incus Container Setup

Spin up a lightweight system container on your own hardware. Containers get IPs from your LAN's DHCP and are accessible by hostname from any machine on your network.

### Prerequisites

- Incus installed and bootstrapped on your host (see [Incus Bootstrap](#incus-bootstrap) below)
- SSH key pair (`~/.ssh/id_rsa`)

### Quick Start

```bash
# Clone and setup (on your workstation)
git clone https://github.com/get2knowio/remo.git
cd remo
./remo init

# Create and configure container
./remo incus create dev1 --host incus-host --user youruser --domain int.example.com

# SSH in
ssh remo@dev1
ssh remo@dev1.int.example.com
```

### Additional Features

| Feature | Description |
|---------|-------------|
| **System Container** | Lightweight, near-native performance |
| **LAN IP via DHCP** | Accessible from any machine on your network |
| **Hostname DNS** | Works if your router registers DHCP hostnames |
| **Host Mounts** | Optional persistent data directories from the Incus host |

### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host <host>` | localhost | Incus host to connect to |
| `--user <user>` | (current user) | SSH user for the Incus host |
| `--domain <domain>` | (none) | Domain for FQDN (e.g., `int.example.com`) |
| `--image <image>` | `images:ubuntu/24.04/cloud` | Cloud image to use |
| `--yes`, `-y` | (prompt) | Skip confirmation on destroy |

### Teardown

```bash
./remo incus destroy dev1 --host incus-host --user youruser --yes
```

---

## Incus Bootstrap

**Skip this if you already have Incus installed and initialized.**

To use Incus containers, you first need to bootstrap Incus on your host machine. This installs Incus, creates a storage pool, and configures macvlan networking so containers get LAN IPs.

### Bootstrap a Remote Host

```bash
./remo incus bootstrap --host 192.168.1.100 --user paul
```

### Bootstrap Localhost

```bash
./remo incus bootstrap
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
./remo incus bootstrap --verbose

# For advanced options (network type, interface), use ./run.sh directly:
./run.sh incus_bootstrap.yml -e "incus_network_parent=eth0"
./run.sh incus_bootstrap.yml -e "incus_network_type=bridge"
```

---

## Troubleshooting

**SSH connection fails?**
```bash
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa
```

**Ansible collection not found?**
```bash
ansible-galaxy collection install -r ansible/requirements.yml
```

**Hetzner API errors?**
Verify your API token has read/write permissions in the Hetzner Cloud Console.

**Container not accessible by hostname?**
- Verify your router/DHCP server registers hostnames (check if other devices are accessible by name)
- DNS registration may take a few seconds after container boot
- Try by IP first to confirm the container is running

**Can't reach container from Incus host?**
This is a known macvlan limitation. Access containers from a different machine on your LAN.

---

## License

MIT License - see LICENSE file for details.
