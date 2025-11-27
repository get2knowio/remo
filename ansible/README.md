# Remote Coding Server - Ansible Project

This Ansible project provisions and configures a minimal Hetzner Cloud server for remote development.

## Features

- **Hetzner Cloud Server**: Provisions the smallest/cheapest shared vCPU server (cx22)
- **SSL-Only Firewall**: Only allows SSH (22) and HTTPS (443) traffic
- **DuckDNS Integration**: Automatically registers the server IP with DuckDNS
- **Docker**: Installed from official Docker repository
- **User Setup**: Creates `g2k` user with passwordless sudo and docker group membership
- **Node.js LTS**: Installed from NodeSource repository
- **Git**: Version control system
- **@devcontainers/cli**: Dev Containers CLI tool
- **GitHub CLI**: `gh` command-line tool
- **Zellij**: Terminal multiplexer with auto-start on SSH login

## Prerequisites

1. **Ansible** (2.15+)
2. **Python 3.8+** with pip
3. **SSH Key Pair** (~/.ssh/id_rsa and ~/.ssh/id_rsa.pub)
4. **Hetzner Cloud Account** with API token
5. **DuckDNS Account** with domain and token

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/get2knowio/remote-coding.git
   cd remote-coding/ansible
   ```

2. Install Ansible dependencies:
   ```bash
   pip install ansible hcloud
   ansible-galaxy collection install -r requirements.yml
   ```

## Configuration

### Option 1: Using .env file (Recommended)

Create a `.env` file in the repository root by copying the example:

```bash
cp .env.example .env
```

Edit `.env` with your actual values:

```bash
# Required
HETZNER_API_TOKEN=your-hetzner-api-token
DUCKDNS_TOKEN=your-duckdns-token
DUCKDNS_DOMAIN=your-subdomain  # Just the subdomain part (e.g., "myserver" for myserver.duckdns.org)

# Optional
SSH_PRIVATE_KEY_PATH=~/.ssh/id_rsa
SSH_PUBLIC_KEY_PATH=~/.ssh/id_rsa.pub
```

Then use the wrapper script to run playbooks (it automatically sources the `.env` file):

```bash
./run.sh site.yml
```

### Option 2: Environment Variables

Alternatively, set environment variables directly:

```bash
# Required
export HETZNER_API_TOKEN="your-hetzner-api-token"
export DUCKDNS_TOKEN="your-duckdns-token"
export DUCKDNS_DOMAIN="your-subdomain"  # Just the subdomain part (e.g., "myserver" for myserver.duckdns.org)

# Optional
export SSH_PRIVATE_KEY_PATH="~/.ssh/id_rsa"  # Default: ~/.ssh/id_rsa
export SSH_PUBLIC_KEY_PATH="~/.ssh/id_rsa.pub"  # Default: ~/.ssh/id_rsa.pub
```

Then run playbooks directly from the ansible directory:

```bash
cd ansible
ansible-playbook site.yml
```

You can also modify `group_vars/all.yml` with your configuration.

## Usage

### Full Provisioning and Configuration

Run the complete setup with a single command:

```bash
# Using .env file (from repository root)
./run.sh site.yml

# Or manually (from ansible directory)
ansible-playbook site.yml
```

### Step-by-Step Provisioning

1. **Provision the server** (creates Hetzner server and updates DuckDNS):
   ```bash
   ./run.sh provision.yml
   ```

2. **Configure the server** (installs all software):
   ```bash
   ./run.sh configure.yml -e "hetzner_server_ip=<server-ip>"
   ```

### Individual Roles

You can also run individual roles:

```bash
# Only configure Docker
./run.sh configure.yml --tags docker -e "hetzner_server_ip=<server-ip>"

# Only set up the g2k user
./run.sh configure.yml --tags user_setup -e "hetzner_server_ip=<server-ip>"
```

## Project Structure

```
remote-coding/
├── .env.example             # Environment variables template
├── .env                     # Your environment variables (git-ignored)
├── run.sh                   # Wrapper script to run with .env
└── ansible/
    ├── ansible.cfg              # Ansible configuration
    ├── requirements.yml         # Ansible Galaxy requirements
    ├── site.yml                 # Main playbook (full workflow)
    ├── provision.yml            # Server provisioning playbook
    ├── configure.yml            # Server configuration playbook
    ├── inventory/
    │   └── hosts.yml            # Dynamic inventory
    ├── group_vars/
    │   └── all.yml              # Global variables
    └── roles/
        ├── hetzner_server/      # Hetzner Cloud provisioning
        ├── duckdns/             # DuckDNS registration
        ├── docker/              # Docker installation
        ├── user_setup/          # g2k user creation
        ├── nodejs/              # Node.js LTS installation
        ├── devcontainers/       # @devcontainers/cli installation
        └── github_cli/          # GitHub CLI installation
```

## Server Specifications

- **Type**: cx22 (smallest shared vCPU)
  - 2 vCPU (shared)
  - 4 GB RAM
  - 40 GB SSD
- **Image**: Ubuntu 24.04 LTS
- **Location**: Falkenstein (fsn1) - can be changed in variables

## Firewall Rules

The server is provisioned with a strict firewall:

| Port | Protocol | Description |
|------|----------|-------------|
| 22   | TCP      | SSH         |
| 443  | TCP      | HTTPS       |

All other incoming traffic is blocked.

## Installed Software

After configuration, the server will have:

| Software | Source |
|----------|--------|
| Docker CE | Official Docker repository |
| Docker Compose | Docker plugin |
| Node.js 20 LTS | NodeSource repository |
| Git | Ubuntu repository |
| @devcontainers/cli | npm global |
| GitHub CLI (gh) | GitHub packages |
| Zellij | apt or GitHub releases |

## User: g2k

The `g2k` user is created with:
- Home directory: `/home/g2k`
- Shell: `/bin/bash`
- Groups: `docker`
- Sudo: `g2k ALL=(ALL) NOPASSWD:ALL`

## Zellij Workflow

The server is configured with [Zellij](https://zellij.dev/), a terminal multiplexer that enables:

- **Persistent Sessions**: SSH into the server and automatically land in a zellij session named `main`
- **Detach & Reattach**: Disconnect from SSH while processes continue running, then reattach later
- **Session Management**: Run multiple terminals within a single SSH connection

### Auto-Start Behavior

When you SSH into the server as the `g2k` user:
1. Zellij automatically starts (or attaches to existing session `main`)
2. Detach with `Ctrl+o, d` to return to the host shell
3. The session persists even after SSH disconnection

The auto-start only triggers when:
- The shell is interactive
- The session is via SSH (`$SSH_TTY` or `$SSH_CONNECTION` is set)
- Not already inside a zellij session

### devshell Helper Script

A helper script is installed at `~/.local/bin/devshell` that:
1. Changes to the project workspace directory (`$dev_workspace_dir`)
2. Starts the devcontainer with `devcontainer up`
3. Opens a zsh shell inside the devcontainer

Usage:
```bash
devshell
```

The workspace directory can be customized via the `dev_workspace_dir` variable in `group_vars/all.yml`.

## Connecting to the Server

After provisioning:

```bash
# As root (initial access)
ssh root@<server-ip>

# As g2k user
ssh g2k@<server-ip>

# Using DuckDNS domain
ssh g2k@<your-subdomain>.duckdns.org
```

## Cleanup

To destroy the server:

```bash
# Using Hetzner Cloud CLI
hcloud server delete remote-coding-server

# Or using the API
curl -X DELETE \
  -H "Authorization: Bearer $HETZNER_API_TOKEN" \
  "https://api.hetzner.cloud/v1/servers/<server-id>"
```

## Troubleshooting

### SSH Connection Issues

Ensure your SSH key is properly configured:
```bash
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa
```

### Ansible Collection Not Found

Install required collections:
```bash
ansible-galaxy collection install -r requirements.yml
```

### Hetzner API Errors

Verify your API token has the correct permissions in the Hetzner Cloud Console.

## License

MIT License - see LICENSE file for details.
