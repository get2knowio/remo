# Remo

Spin up a fully-configured development environment in minutes. One command gives you a persistent, secure coding environment with Dev Containers support.

## Two Ways to Get a Dev Environment

| | Hetzner Cloud | Incus Container |
|---|---|---|
| **Where** | Cloud VM (remote) | Container on your hardware (local/homelab) |
| **Cost** | ~€4/month | Your electricity |
| **Access** | SSH over internet via DuckDNS | SSH on your LAN by hostname |
| **Best for** | Remote work, always-on | Local development, testing |

Both give you a Linux host where you can run devcontainers, with persistent sessions via Zellij.

---

## Option 1: Hetzner Cloud (Remote)

Spin up a cloud VM with full dev tooling.

### What You Get

| Component | Description |
|-----------|-------------|
| **Hetzner Server** | 2 vCPU, 4 GB RAM, 40 GB SSD (Ubuntu 24.04) |
| **Persistent Volume** | `/home/g2k` survives server teardown |
| **Docker + Compose** | Official Docker CE with compose plugin |
| **Dev Containers CLI** | `devcontainer up`, `devcontainer exec`, etc. |
| **Node.js 24 LTS** | From NodeSource repository |
| **GitHub CLI** | `gh` for GitHub workflow integration |
| **Zellij** | Terminal multiplexer for persistent sessions |
| **Strict Firewall** | SSH-only access (port 22) |
| **DuckDNS Domain** | Automatic DNS registration |

### Prerequisites

- Python 3.8+ with pip
- SSH key pair (`~/.ssh/id_rsa`)
- [Hetzner Cloud](https://www.hetzner.com/cloud) account + API token
- [DuckDNS](https://www.duckdns.org/) account + token + subdomain

### Quick Start

```bash
# Clone and install dependencies
git clone https://github.com/get2knowio/remote-coding.git
cd remote-coding
pip install ansible hcloud
ansible-galaxy collection install -r ansible/requirements.yml

# Configure credentials
cp .env.example .env
# Edit .env with your tokens

# Provision server
./run.sh hetzner_site.yml

# SSH in
ssh g2k@your-subdomain.duckdns.org
```

### GitHub Actions (Alternative)

Fork this repo and use GitHub Actions to provision without local setup:

1. **Add secrets** in Settings → Secrets → Actions:
   - `HETZNER_API_TOKEN`, `SSH_PRIVATE_KEY`, `SSH_PUBLIC_KEY`
   - `DUCKDNS_TOKEN`, `DUCKDNS_DOMAIN`

2. **Run**: Actions → Provision Server → Run workflow → type `yes`

### Teardown

```bash
./run.sh hetzner_teardown.yml                      # Destroy server (keeps volume)
./run.sh hetzner_teardown.yml -e remove_volume=true  # Destroy everything
```

---

## Option 2: Incus Container (Local/Homelab)

Spin up a lightweight system container on your own hardware. Containers get IPs from your LAN's DHCP and are accessible by hostname from any machine on your network.

### What You Get

| Component | Description |
|-----------|-------------|
| **System Container** | Lightweight, near-native performance |
| **LAN IP via DHCP** | Accessible from any machine on your network |
| **Hostname DNS** | `ssh ubuntu@container-name` (if your router registers DHCP hostnames) |
| **Docker + Compose** | Official Docker CE with compose plugin |
| **Dev Containers CLI** | `devcontainer up`, `devcontainer exec`, etc. |
| **Node.js 24 LTS** | From NodeSource repository |
| **GitHub CLI** | `gh` for GitHub workflow integration |
| **Zellij** | Terminal multiplexer for persistent sessions |
| **Host Mounts** | Persistent data directories from the Incus host |

### Prerequisites

- Incus installed and bootstrapped on your host (see [Incus Bootstrap](#incus-bootstrap) below)
- SSH key pair (`~/.ssh/id_rsa`)

### Quick Start

```bash
# Create and configure container in one step (from your laptop)
./run.sh incus_site.yml \
  -i "incus-host," \
  -e "container_name=dev1" \
  -e "container_domain=int.example.com" \
  -e "ansible_user=youruser"

# SSH in (once DNS registers the hostname)
ssh ubuntu@dev1
ssh ubuntu@dev1.int.example.com
```

Or run the steps separately:

```bash
# Step 1: Create container
./run.sh incus_provision.yml -i "incus-host," -e "container_name=dev1 ansible_user=youruser"

# Step 2: Install dev tools (pass the container IP from step 1)
./run.sh incus_configure.yml -e "container_ip=192.168.1.x"
```

### Common Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `container_name` | (required) | Name of the container |
| `container_image` | `images:ubuntu/24.04/cloud` | Cloud image to use |
| `container_domain` | (empty) | Domain for FQDN (e.g., `int.example.com`) |
| `container_ssh_user` | `ubuntu` | SSH user created in container |
| `container_mounts` | `[]` | Host directories to mount |

### Teardown

```bash
./run.sh incus_teardown.yml -e container_name=dev1
./run.sh incus_teardown.yml -e container_name=dev1 -e force=true  # If running
```

---

## The Dev Workflow

Once you have an environment (Hetzner or Incus), the workflow is the same:

```bash
# 1. SSH in
ssh user@your-host

# 2. Start a Zellij session for persistence
zellij attach --create main

# 3. Clone and launch a devcontainer
git clone https://github.com/your/project.git
cd project
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . zsh

# 4. Disconnect anytime - your session persists
#    Ctrl+d to detach from Zellij
#    Close SSH - reconnect later and pick up where you left off
```

### Zellij: Persistent Terminal Sessions

[Zellij](https://zellij.dev/) keeps your terminal sessions alive:

- **Start/Attach**: `zellij attach --create main`
- **Detach**: `Ctrl+d` returns to host shell
- **Reconnect**: SSH back in, run same command to resume

For devcontainers, the host Zellij is configured as an "outer" session (tab management only), so you can run an "inner" Zellij inside containers without keybind conflicts.

---

## Incus Bootstrap

**Skip this if you already have Incus installed and initialized.**

To use Incus containers, you first need to bootstrap Incus on your host machine. This installs Incus, creates a storage pool, and configures macvlan networking so containers get LAN IPs.

### Bootstrap a Remote Host

```bash
./run.sh incus_bootstrap.yml \
  -i "192.168.1.100," \
  -e "target_hosts=all ansible_user=paul"
```

### Bootstrap Localhost

```bash
./run.sh incus_bootstrap.yml
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
# Specify network interface explicitly
./run.sh incus_bootstrap.yml -e "incus_network_parent=eth0"

# Use NAT bridge instead of macvlan (containers get private IPs)
./run.sh incus_bootstrap.yml -e "incus_network_type=bridge"

# Verbose output
./run.sh incus_bootstrap.yml -e "incus_bootstrap_verbosity=detailed"
```

See [specs/001-bootstrap-incus-host/quickstart.md](specs/001-bootstrap-incus-host/quickstart.md) for more details.

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
