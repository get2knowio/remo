# Remote Coding Server

Spin up a fully-configured cloud development server in minutes. One command gives you a persistent, secure remote coding environment with VS Code Dev Containers support.

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
| **Node.js 24 LTS** | From NodeSource repository |
| **GitHub CLI** | `gh` for GitHub workflow integration |
| **Zellij** | Terminal multiplexer with auto-attach on SSH |
| **Strict Firewall** | Only SSH (22) and HTTPS (443) allowed |
| **DuckDNS Domain** | Automatic DNS registration |

## The Workflow

```bash
# 1. Provision your server (one command)
./run.sh site.yml

# 2. SSH in and start coding
ssh g2k@your-subdomain.duckdns.org

# 3. You're automatically in a Zellij session
#    Clone a repo and launch a devcontainer:
git clone https://github.com/your/project.git
cd project
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . zsh

# 4. Disconnect anytime - your session persists
#    Ctrl+o, d to detach from Zellij
#    Close SSH - reconnect later and pick up where you left off

# 5. Tear down when done (data persists on volume)
./run.sh teardown.yml
```

## Zellij: Persistent Terminal Sessions

When you SSH into the server, you automatically land in a [Zellij](https://zellij.dev/) session:

- **Auto-attach**: SSH → immediately in your `main` session
- **Detach**: `Ctrl+o, d` returns to host shell
- **Persist**: Close SSH, processes keep running
- **Reconnect**: SSH back in, right where you left off

### devshell Helper

A convenience script at `~/.local/bin/devshell`:

```bash
devshell  # cd to workspace, start devcontainer, open shell inside
```

---

## Quick Start

### Prerequisites

- Python 3.8+ with pip
- SSH key pair (`~/.ssh/id_rsa`)
- [Hetzner Cloud](https://www.hetzner.com/cloud) account + API token
- [DuckDNS](https://www.duckdns.org/) account + token + subdomain

### Setup

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

### Configuration

Create a `.env` file in the repository root:

```bash
# Required
HETZNER_API_TOKEN=your-hetzner-api-token
DUCKDNS_TOKEN=your-duckdns-token
DUCKDNS_DOMAIN=your-subdomain  # e.g., "myserver" for myserver.duckdns.org

# Optional
SSH_PRIVATE_KEY_PATH=~/.ssh/id_rsa
SSH_PUBLIC_KEY_PATH=~/.ssh/id_rsa.pub
```

---

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
# or
ssh g2k@<server-ip>
```

### Running Individual Roles

```bash
./run.sh configure.yml --tags docker -e "hetzner_server_ip=<ip>"
./run.sh configure.yml --tags user_setup -e "hetzner_server_ip=<ip>"
```

---

## Project Structure

```
remote-coding/
├── .env.example             # Environment variables template
├── .env                     # Your credentials (git-ignored)
├── run.sh                   # Wrapper script
└── ansible/
    ├── site.yml             # Main playbook
    ├── provision.yml        # Server creation
    ├── configure.yml        # Software installation
    ├── teardown.yml         # Cleanup
    ├── group_vars/all.yml   # Global variables
    └── roles/
        ├── hetzner_server/  # Cloud provisioning
        ├── duckdns/         # DNS registration
        ├── docker/          # Docker CE
        ├── user_setup/      # g2k user + sudo
        ├── nodejs/          # Node.js LTS
        ├── devcontainers/   # @devcontainers/cli
        ├── github_cli/      # gh CLI
        └── zellij/          # Terminal multiplexer
```

## User Account

The `g2k` user is created with:
- Passwordless sudo (`NOPASSWD:ALL`)
- Docker group membership
- SSH key from your local machine

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
