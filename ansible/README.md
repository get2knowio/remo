# Ansible Playbooks

This directory contains the Ansible automation for provisioning and configuring development environments.

## Playbooks

### Hetzner Cloud

| Playbook | Description |
|----------|-------------|
| `hetzner_site.yml` | Complete workflow: provision + configure |
| `hetzner_provision.yml` | Create server and register with DuckDNS |
| `hetzner_configure.yml` | Install dev tools on existing server |
| `hetzner_teardown.yml` | Destroy server and optionally the volume |

### Incus Containers

| Playbook | Description |
|----------|-------------|
| `incus_site.yml` | Complete workflow: provision + configure |
| `incus_provision.yml` | Create container with SSH access |
| `incus_configure.yml` | Install dev tools on existing container |
| `incus_teardown.yml` | Destroy container |
| `incus_bootstrap.yml` | Install and configure Incus on a host |

### Shared Configuration

Both `hetzner_configure.yml` and `incus_configure.yml` use the same shared task file (`tasks/configure_dev_tools.yml`) to install:
- Docker (with docker-compose plugin)
- Node.js LTS
- @devcontainers/cli
- GitHub CLI (gh)
- fzf
- Zellij terminal multiplexer

## Running Playbooks

### Hetzner

```bash
./run.sh hetzner_site.yml                    # Full provision + configure
./run.sh hetzner_provision.yml               # Just create server
./run.sh hetzner_configure.yml               # Configure existing server
./run.sh hetzner_teardown.yml                # Destroy server (keep volume)
./run.sh hetzner_teardown.yml -e remove_volume=true  # Destroy everything
```

### Incus

```bash
# Full workflow (provision + configure)
./run.sh incus_site.yml -e container_name=dev1

# Or step by step
./run.sh incus_provision.yml -e container_name=dev1
./run.sh incus_configure.yml -e container_ip=<ip>

# Teardown
./run.sh incus_teardown.yml -e container_name=dev1

# Bootstrap Incus on a host (prerequisite)
./run.sh incus_bootstrap.yml -i "host," -e "target_hosts=all ansible_user=user"
```

## Roles

| Role | Description |
|------|-------------|
| `hetzner_server` | Creates Hetzner Cloud server, firewall, volume, and SSH key |
| `duckdns` | Updates DuckDNS with the server's IP address |
| `incus_bootstrap` | Installs and configures Incus with storage and networking |
| `incus_container` | Creates and configures an Incus container |
| `incus_container_teardown` | Destroys an Incus container |
| `docker` | Installs Docker CE and docker-compose plugin |
| `user_setup` | Creates user with sudo and docker access |
| `nodejs` | Installs Node.js LTS from NodeSource |
| `devcontainers` | Installs @devcontainers/cli globally |
| `github_cli` | Installs GitHub CLI (`gh`) |
| `fzf` | Installs fzf fuzzy finder |
| `zellij` | Installs and configures Zellij terminal multiplexer |

## Configuration

### Environment Variables (Hetzner)

Set these in `.env` at the repository root:

| Variable | Required | Description |
|----------|----------|-------------|
| `HETZNER_API_TOKEN` | Yes | Hetzner Cloud API token |
| `DUCKDNS_TOKEN` | Yes | DuckDNS authentication token |
| `DUCKDNS_DOMAIN` | Yes | Subdomain (without `.duckdns.org`) |
| `SSH_PRIVATE_KEY_PATH` | No | Path to SSH private key (default: `~/.ssh/id_rsa`) |
| `SSH_PUBLIC_KEY_PATH` | No | Path to SSH public key (default: `~/.ssh/id_rsa.pub`) |

### Incus Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `container_name` | (required) | Name of the container |
| `container_image` | `images:ubuntu/24.04/cloud` | Cloud image to use |
| `container_domain` | (empty) | Domain for FQDN, enables DHCP hostname registration |
| `container_ssh_user` | `remo` | SSH user created in container |
| `container_ip` | (required for configure) | IP address of container to configure |

## Project Structure

```
ansible/
├── hetzner_site.yml          # Hetzner: full workflow
├── hetzner_provision.yml     # Hetzner: create server
├── hetzner_configure.yml     # Hetzner: install dev tools
├── hetzner_teardown.yml      # Hetzner: destroy server
├── incus_site.yml            # Incus: full workflow
├── incus_provision.yml       # Incus: create container
├── incus_configure.yml       # Incus: install dev tools
├── incus_teardown.yml        # Incus: destroy container
├── incus_bootstrap.yml       # Incus: install Incus on host
├── tasks/
│   └── configure_dev_tools.yml  # Shared dev tools configuration
├── roles/
│   ├── hetzner_server/
│   ├── duckdns/
│   ├── incus_bootstrap/
│   ├── incus_container/
│   ├── incus_container_teardown/
│   ├── docker/
│   ├── user_setup/
│   ├── nodejs/
│   ├── devcontainers/
│   ├── github_cli/
│   ├── fzf/
│   └── zellij/
├── inventory/
│   └── hosts.yml
└── group_vars/
    └── all.yml
```

## Adding New Dev Tools

To add a new tool to both Hetzner and Incus environments:

1. Create the role in `roles/mytool/tasks/main.yml`

2. Add it to `tasks/configure_dev_tools.yml`:
   ```yaml
   - name: Install mytool
     ansible.builtin.include_role:
       name: mytool
     when: configure_mytool | default(true) | bool
   ```

Both environments will automatically get the new tool.
