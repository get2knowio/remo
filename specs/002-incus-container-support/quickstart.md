# Quickstart: Incus Container Support

**Feature Branch**: `002-incus-container-support`
**Date**: 2026-01-07

## Prerequisites

1. **Incus host bootstrapped**: Run the `001-bootstrap-incus-host` feature first
   ```bash
   ./run.sh incus_bootstrap.yml
   ```

2. **User in incus-admin group**: Log out and back in after bootstrap
   ```bash
   groups  # Should show incus-admin
   ```

3. **SSH key pair exists**:
   ```bash
   ls ~/.ssh/id_rsa.pub  # Must exist
   ```

## Quick Commands

### Create a Development Container

```bash
# Create container named 'dev' with default Ubuntu 24.04
./run.sh incus_container.yml -e container_name=dev
```

### Configure Container with Standard Roles

```bash
# Apply docker, nodejs, user_setup roles
./run.sh incus_container_configure.yml -e container_name=dev
```

### Connect to Container

```bash
# SSH into the container from a separate workstation (not the Incus host)
# Containers get LAN IPs via DHCP (macvlan networking)
ssh ubuntu@<container_ip>

# IMPORTANT: The Incus host CANNOT SSH to containers directly (macvlan limitation)
# Use incus exec on the host instead:
incus exec dev -- bash
```

### Destroy Container (Keep Data)

```bash
./run.sh incus_container_teardown.yml -e container_name=dev
```

### Destroy Container (Remove Data)

```bash
./run.sh incus_container_teardown.yml -e container_name=dev -e preserve_data=false
```

## Common Workflows

### Workflow 1: Fresh Development Environment

```bash
# 1. Create container
./run.sh incus_container.yml -e container_name=myproject

# 2. Configure with development tools
./run.sh incus_container_configure.yml -e container_name=myproject

# 3. SSH and start working
ssh ubuntu@$(incus list myproject -c 4 --format=csv | cut -d, -f1)
```

### Workflow 2: Container with Persistent Storage

```bash
# Create with host directory mounted
./run.sh incus_container.yml \
  -e container_name=dev \
  -e 'container_mounts=[{"source": "/home/user/projects", "target": "/projects"}]'

# Data in /projects persists across container destruction
```

### Workflow 3: Multiple Containers

```bash
# Create multiple containers
./run.sh incus_container.yml -e container_name=frontend
./run.sh incus_container.yml -e container_name=backend
./run.sh incus_container.yml -e container_name=database

# Configure all at once (requires inventory setup)
./run.sh incus_container_configure.yml --limit incus_containers
```

### Workflow 4: Quick Teardown and Rebuild

```bash
# Destroy and recreate (data preserved by default)
./run.sh incus_container_teardown.yml -e container_name=dev -e auto_confirm=true
./run.sh incus_container.yml -e container_name=dev
```

## Variable Reference

### Container Provisioning

| Variable | Default | Description |
|----------|---------|-------------|
| `container_name` | **required** | Unique container identifier |
| `container_image` | `images:ubuntu/24.04/cloud` | Cloud-enabled container image |
| `container_ssh_user` | `ubuntu` | SSH user for access |
| `container_ssh_key_path` | `~/.ssh/id_rsa.pub` | Public key to inject |
| `container_mounts` | `[]` | Host directory mounts |

### Container Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `container_name` | **required** | Container to configure |
| `configure_roles` | (see below) | Roles to apply |

Default roles: `docker`, `user_setup`, `nodejs`, `fzf`, `zellij`

### Container Teardown

| Variable | Default | Description |
|----------|---------|-------------|
| `container_name` | **required** | Container to destroy |
| `preserve_data` | `true` | Keep mount directories |
| `force` | `false` | Force delete running container |
| `auto_confirm` | `false` | Skip confirmation prompt |

## Comparison with Hetzner Workflow

| Action | Hetzner | Incus |
|--------|---------|-------|
| Provision | `./run.sh provision.yml` | `./run.sh incus_container.yml -e container_name=X` |
| Configure | `./run.sh configure.yml` | `./run.sh incus_container_configure.yml -e container_name=X` |
| Teardown | `./run.sh teardown.yml` | `./run.sh incus_container_teardown.yml -e container_name=X` |
| Connect | `ssh g2k@<ip>` | `ssh ubuntu@<ip>` |
| Default user | `root` → `g2k` | `ubuntu` |

## Troubleshooting

### "Permission denied" on Incus commands

```bash
# Verify group membership
groups
# If incus-admin not shown, log out and back in
```

### SSH connection timeout

```bash
# Check container is running
incus list

# Check cloud-init status
incus exec <container> -- cloud-init status

# View cloud-init logs
incus exec <container> -- cat /var/log/cloud-init.log
```

### Container IP not assigned

```bash
# Verify network exists
incus network list

# Check container network config
incus config show <container> | grep -A5 eth0
```

### Docker not working inside container

Docker-in-Docker may require privileged mode (future enhancement):
```bash
# Current workaround: use incus exec instead of SSH for docker commands
incus exec <container> -- docker ps
```
