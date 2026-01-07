# Remo

Spin up a fully-configured cloud development server in minutes. One command gives you a persistent, secure remote coding environment with Dev Containers support.

## Why Remote Coding?

- **Code from anywhere** - SSH into your server from any machine
- **Persistent sessions** - Disconnect and reconnect without losing work (Zellij terminal multiplexer)
- **Dev Containers ready** - Full Docker and devcontainer CLI support out of the box
- **Cost-effective** - Pay only for what you use (~€4/month for Hetzner's smallest server)
- **Data persistence** - Your home directory survives server teardown/rebuild

## What You Get

| Component | Description |
|-----------|-------------|
| **Hetzner Cloud Server** | 2 vCPU, 4 GB RAM, 40 GB SSD (Ubuntu 24.04) |
| **Persistent Volume** | `/home/g2k` survives server teardown |
| **Docker + Compose** | Official Docker CE with compose plugin |
| **Dev Containers CLI** | `devcontainer up`, `devcontainer exec`, etc. |
| **Incus Bootstrap** | Container/VM management system (optional, see below) |
| **Node.js 24 LTS** | From NodeSource repository |
| **GitHub CLI** | `gh` for GitHub workflow integration |
| **Zellij** | Terminal multiplexer for persistent sessions |
| **Strict Firewall** | SSH-only access (port 22) |
| **DuckDNS Domain** | Automatic DNS registration |

## Quick Start

### Option 1: GitHub Actions (Recommended)

Fork this repo and use GitHub Actions to manage your server without any local setup.

1. **Fork** this repository to your GitHub account

2. **Add secrets** in Settings → Secrets and variables → Actions:

   | Secret | Description |
   |--------|-------------|
   | `HETZNER_API_TOKEN` | Your Hetzner Cloud API token |
   | `SSH_PRIVATE_KEY` | Your SSH private key |
   | `SSH_PUBLIC_KEY` | Your SSH public key |
   | `DUCKDNS_TOKEN` | Your DuckDNS token |
   | `DUCKDNS_DOMAIN` | Your subdomain (without `.duckdns.org`) |

3. **Provision**: Actions → Provision Server → Run workflow → type `yes`

4. **Teardown**: Actions → Teardown Server → Run workflow → type `yes`

#### Scheduled Actions for Cost Optimization

Add automatic schedules to provision at workday start and teardown at end:

```yaml
on:
  schedule:
    - cron: '0 8 * * 1-5'   # 8 AM UTC weekdays
  workflow_dispatch:
    # ... existing manual trigger
```

### Option 2: Local CLI

Run Ansible playbooks directly from your machine.

#### Prerequisites

- Python 3.8+ with pip
- SSH key pair (`~/.ssh/id_rsa`)
- [Hetzner Cloud](https://www.hetzner.com/cloud) account + API token
- [DuckDNS](https://www.duckdns.org/) account + token + subdomain

#### Setup

```bash
# Clone and install dependencies
git clone https://github.com/get2knowio/remote-coding.git
cd remote-coding
pip install ansible hcloud
ansible-galaxy collection install -r ansible/requirements.yml

# Configure credentials
cp .env.example .env
# Edit .env with your tokens

# Provision!
./run.sh site.yml
```

See [ansible/README.md](ansible/README.md) for detailed playbook documentation.

## The Workflow

```bash
# 1. Provision your server (one command)
./run.sh site.yml

# 2. SSH in and start coding
ssh g2k@your-subdomain.duckdns.org

# 3. Start a Zellij session for persistence (optional)
zellij attach --create main

# 4. Clone a repo and launch a devcontainer:
git clone https://github.com/your/project.git
cd project
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . zsh

# 5. Disconnect anytime - your session persists (if using Zellij)
#    Ctrl+o, d to detach from Zellij
#    Close SSH - reconnect later and pick up where you left off

# 6. Tear down when done (data persists on volume)
./run.sh teardown.yml
```

## Zellij: Persistent Terminal Sessions

[Zellij](https://zellij.dev/) is available for persistent terminal sessions:

- **Start/Attach**: Run `zellij attach --create main` to start or attach to a session
- **Detach**: `Ctrl+o, d` returns to host shell
- **Persist**: Close SSH, processes keep running
- **Reconnect**: SSH back in, run `zellij attach --create main` to resume

### Nested Zellij for Devcontainers

The server's Zellij is configured as an "outer" session for **tab management only**. This lets you run a second "inner" Zellij inside devcontainers without keybind conflicts.

**Outer Zellij keybinds (on the host):**
| Keybind | Action |
|---------|--------|
| `Ctrl-t` | New tab |
| `Ctrl-Tab` | Next tab |
| `Ctrl-Shift-Tab` | Previous tab |
| `Ctrl-w` | Close tab |
| `Ctrl-d` | Detach from session |

All other keybinds pass through to the inner Zellij in your devcontainer.

### devshell Helper

A convenience script at `~/.local/bin/devshell`:

```bash
devshell  # cd to workspace, start devcontainer, open shell inside
```

### Automatic Devcontainer Rebuilds

The system automatically detects when your devcontainer configuration has changed and rebuilds the container when needed. This happens when:

- `.devcontainer/devcontainer.json` is modified
- `.devcontainer/Dockerfile` is modified  
- `.devcontainer/docker-compose.yml` is modified
- `.devcontainer.json` (root level) is modified

Configuration hashes are stored in `~/.cache/devcontainer-hashes/` and compared on each session start.

**Force a manual rebuild:**
```bash
# Create a rebuild flag file in your project
touch ~/projects/my-project/.devcontainer-rebuild

# Next time you select the project, it will rebuild
```

This is useful when you need to pick up base image updates or other changes not tracked by the config files.

## Usage Reference

### Playbooks

| Command | Description |
|---------|-------------|
| `./run.sh site.yml` | Full provisioning + configuration |
| `./run.sh provision.yml` | Create server + update DNS only |
| `./run.sh configure.yml -e "hetzner_server_ip=<ip>"` | Configure existing server |
| `./run.sh teardown.yml` | Destroy server (keeps volume) |
| `./run.sh teardown.yml -e remove_volume=true` | Destroy server + volume |

### Connecting

```bash
ssh g2k@<your-subdomain>.duckdns.org
```

## User Account

The `g2k` user is created with:
- Passwordless sudo (`NOPASSWD:ALL`)
- Docker group membership
- SSH key from your configuration

## Home Lab Alternative: Incus (Optional)

As an alternative to Hetzner Cloud, you can use [Incus](https://linuxcontainers.org/incus/) to run lightweight system containers on your own hardware. This gives you the same workflow—SSH into a host, run devcontainers—but on a home server or local workstation instead of a cloud VM.

### Bootstrap Incus on a Remote Host

To set up Incus on a remote server (OpenSUSE Tumbleweed):

```bash
./run.sh incus_bootstrap.yml -i "<host>," -e "target_hosts=all ansible_user=<user>"
```

Replace `<host>` with your server's IP or hostname, and `<user>` with your SSH username. The trailing comma is required for single-host inventory.

**Requirements:**
- SSH key access to the remote host
- The user must have sudo privileges (the playbook uses `become: true`)

**Example:**

```bash
./run.sh incus_bootstrap.yml -i "192.168.1.100," -e "target_hosts=all ansible_user=paul"
```

If your user requires a sudo password, add `--ask-become-pass`:

```bash
./run.sh incus_bootstrap.yml -i "192.168.1.100," -e "target_hosts=all ansible_user=paul" --ask-become-pass
```

This will:
- Install Incus and incus-tools packages
- Enable and start the Incus daemon services
- Add the specified user to the `incus-admin` group for non-root container management
- Initialize a directory-based storage pool
- Configure a NAT bridge network for container connectivity

#### Bootstrap on Localhost

For local development, omit the inventory flag:

```bash
./run.sh incus_bootstrap.yml
```

#### Verbose Output

For detailed verification output including full YAML configuration:

```bash
./run.sh incus_bootstrap.yml -e incus_bootstrap_verbosity=detailed
```

By default, bootstrap shows only summary information. Use `detailed` mode to see complete storage pool and network configurations during verification.

**After bootstrap**, log out and back in (or run `newgrp incus-admin`) to activate group membership, then:

```bash
# Launch a container
incus launch images:alpine/edge my-container

# List containers
incus list

# Access container shell
incus exec my-container -- sh
```

See [specs/001-bootstrap-incus-host/quickstart.md](specs/001-bootstrap-incus-host/quickstart.md) for detailed usage instructions.

### Hetzner vs Incus

| | Hetzner Cloud | Incus (Home Lab) |
|---|---|---|
| **Host** | Cloud VM | System container on your hardware |
| **Cost** | ~€4/month | Your electricity bill |
| **Setup** | `./run.sh site.yml` | `./run.sh incus_bootstrap.yml` |
| **Access** | SSH over internet | SSH over local network |
| **Use case** | Remote work, always-on | Local development, testing |

Both approaches give you a Linux host where you can run devcontainers.

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

## License

MIT License - see LICENSE file for details.
