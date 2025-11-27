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

Set the following environment variables before running the playbooks:

```bash
# Required
export HETZNER_API_TOKEN="your-hetzner-api-token"
export DUCKDNS_TOKEN="your-duckdns-token"
export DUCKDNS_DOMAIN="your-subdomain"  # Just the subdomain part (e.g., "myserver" for myserver.duckdns.org)

# Optional
export SSH_PRIVATE_KEY_PATH="~/.ssh/id_rsa"  # Default: ~/.ssh/id_rsa
export SSH_PUBLIC_KEY_PATH="~/.ssh/id_rsa.pub"  # Default: ~/.ssh/id_rsa.pub
```

Alternatively, you can modify `group_vars/all.yml` with your configuration.

## Usage

### Full Provisioning and Configuration

Run the complete setup with a single command:

```bash
ansible-playbook site.yml
```

### Step-by-Step Provisioning

1. **Provision the server** (creates Hetzner server and updates DuckDNS):
   ```bash
   ansible-playbook provision.yml
   ```

2. **Configure the server** (installs all software):
   ```bash
   ansible-playbook configure.yml -e "hetzner_server_ip=<server-ip>"
   ```

### Individual Roles

You can also run individual roles:

```bash
# Only configure Docker
ansible-playbook configure.yml --tags docker -e "hetzner_server_ip=<server-ip>"

# Only set up the g2k user
ansible-playbook configure.yml --tags user_setup -e "hetzner_server_ip=<server-ip>"
```

## Project Structure

```
ansible/
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

## User: g2k

The `g2k` user is created with:
- Home directory: `/home/g2k`
- Shell: `/bin/bash`
- Groups: `docker`
- Sudo: `g2k ALL=(ALL) NOPASSWD:ALL`

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
