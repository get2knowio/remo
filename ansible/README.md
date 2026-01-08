# Ansible Playbooks

This directory contains the Ansible automation for provisioning and configuring the remote coding server.

## Playbooks

| Playbook | Description |
|----------|-------------|
| `site.yml` | Complete workflow: provision server + configure all software |
| `provision.yml` | Create Hetzner server and register with DuckDNS |
| `configure.yml` | Install software on an existing server |
| `teardown.yml` | Destroy server and optionally the volume |

## Roles

| Role | Description |
|------|-------------|
| `hetzner_server` | Creates Hetzner Cloud server, firewall, volume, and SSH key |
| `duckdns` | Updates DuckDNS with the server's IP address |
| `docker` | Installs Docker CE and docker-compose plugin |
| `user_setup` | Creates `g2k` user with sudo and docker access |
| `nodejs` | Installs Node.js LTS from NodeSource |
| `devcontainers` | Installs @devcontainers/cli globally |
| `github_cli` | Installs GitHub CLI (`gh`) |
| `zellij` | Installs and configures Zellij terminal multiplexer |

## Running Playbooks

### Using the wrapper script (recommended)

From the repository root:

```bash
./run.sh site.yml                    # Full provision + configure
./run.sh provision.yml               # Just create server
./run.sh configure.yml               # Configure existing server
./run.sh teardown.yml                # Destroy server (keep volume)
./run.sh teardown.yml -e remove_volume=true  # Destroy everything
```

### Running directly

From this directory:

```bash
ansible-playbook site.yml
ansible-playbook provision.yml
ansible-playbook configure.yml -e "hetzner_server_ip=<ip>"
ansible-playbook teardown.yml
```

## Configuration

### Environment Variables

Set these in `.env` at the repository root, or export them:

| Variable | Required | Description |
|----------|----------|-------------|
| `HETZNER_API_TOKEN` | Yes | Hetzner Cloud API token |
| `DUCKDNS_TOKEN` | Yes | DuckDNS authentication token |
| `DUCKDNS_DOMAIN` | Yes | Subdomain (without `.duckdns.org`) |
| `SSH_PRIVATE_KEY_PATH` | No | Path to SSH private key (default: `~/.ssh/id_rsa`) |
| `SSH_PUBLIC_KEY_PATH` | No | Path to SSH public key (default: `~/.ssh/id_rsa.pub`) |

### Global Variables

Edit `group_vars/all.yml` to customize:

```yaml
# Server configuration
hetzner_server_name: remote-coding
hetzner_server_type: cx22
hetzner_server_image: ubuntu-24.04
hetzner_server_location: hel1

# Node.js version
nodejs_version: "24"

# Zellij session name
zellij_session_name: main

# Workspace directory for devshell helper
dev_workspace_dir: ~/workspace
```

## Project Structure

```
ansible/
├── ansible.cfg          # Ansible configuration
├── requirements.yml     # Galaxy collection dependencies
├── site.yml             # Main playbook
├── provision.yml        # Server creation only
├── configure.yml        # Software installation only
├── teardown.yml         # Cleanup playbook
├── inventory/
│   └── hosts.yml        # Dynamic inventory
├── group_vars/
│   └── all.yml          # Global variables
└── roles/
    ├── hetzner_server/
    │   ├── defaults/main.yml
    │   └── tasks/main.yml
    ├── duckdns/
    │   ├── defaults/main.yml
    │   └── tasks/main.yml
    ├── docker/
    │   ├── handlers/main.yml
    │   └── tasks/main.yml
    ├── user_setup/
    │   ├── defaults/main.yml
    │   ├── tasks/main.yml
    │   └── templates/devshell.sh.j2
    ├── nodejs/
    │   ├── defaults/main.yml
    │   └── tasks/main.yml
    ├── devcontainers/
    │   └── tasks/main.yml
    ├── github_cli/
    │   └── tasks/main.yml
    └── zellij/
        ├── defaults/main.yml
        ├── tasks/main.yml
        └── templates/config.kdl.j2
```

## Adding New Roles

1. Create role structure:
   ```bash
   mkdir -p roles/myrole/{tasks,defaults,handlers,templates}
   ```

2. Add tasks in `roles/myrole/tasks/main.yml`

3. Add the role to `configure.yml` (or `site.yml`):
   ```yaml
   roles:
     - role: myrole
   ```

4. Optionally add a tag for selective execution:
   ```yaml
   roles:
     - role: myrole
       tags: [myrole]
   ```

## Server Specifications

Default Hetzner server (configurable in `group_vars/all.yml`):

| Spec | Value |
|------|-------|
| Type | cx22 (smallest shared vCPU) |
| CPU | 2 vCPU (shared) |
| RAM | 4 GB |
| Disk | 40 GB SSD |
| Image | Ubuntu 24.04 LTS |
| Location | Helsinki (hel1) |

## Firewall Rules

The `hetzner_server` role creates a firewall allowing only:

| Port | Protocol | Description |
|------|----------|-------------|
| 22 | TCP | SSH |

All other inbound traffic is blocked.

## Incus Container Commands

Provision and manage Incus containers on localhost. Requires an Incus host bootstrapped with `incus_bootstrap.yml`.

### Container Provisioning

```bash
# Create a new container
./run.sh incus_container.yml -e container_name=mycontainer

# Create with custom image
./run.sh incus_container.yml -e container_name=mycontainer -e container_image=images:debian/12/cloud

# Create with custom SSH user
./run.sh incus_container.yml -e container_name=mycontainer -e container_ssh_user=admin
```

### Container Configuration

```bash
# Configure container with all dev tools (Docker, Node.js, devcontainer CLI, GitHub CLI, fzf, Zellij)
./run.sh incus_container_configure.yml -e container_name=mycontainer

# Disable specific tools
./run.sh incus_container_configure.yml -e container_name=mycontainer -e configure_docker=false
./run.sh incus_container_configure.yml -e container_name=mycontainer -e configure_devcontainers=false
```

### Container Teardown

```bash
# Destroy container (preserves host data directories by default)
./run.sh incus_container_teardown.yml -e container_name=mycontainer

# Force destroy running container
./run.sh incus_container_teardown.yml -e container_name=mycontainer -e force=true
```

### Common Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `container_name` | (required) | Name of the container |
| `container_image` | `images:ubuntu/24.04/cloud` | Cloud image to use |
| `container_ssh_user` | `ubuntu` | SSH user created in container |
| `container_ssh_key_path` | `~/.ssh/id_rsa.pub` | SSH public key to inject |
| `container_domain` | (empty) | Domain for FQDN, enables DHCP hostname registration |
| `force` | `false` | Force teardown of running containers |
| `preserve_data` | `true` | Keep host mount directories on teardown |
