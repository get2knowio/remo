# Quickstart: Bootstrap Incus Host

## Prerequisites

- OpenSUSE Tumbleweed (primary) or Ubuntu 24.04+ (secondary)
- Sudo privileges
- At least 10GB free disk space
- Internet connectivity (for package installation)

## Usage

### Basic Bootstrap

From the remo repository root:

```bash
./run.sh incus_bootstrap.yml
```

This will:
1. Install the Incus package
2. Enable and start the Incus socket service
3. Add your user to the `incus-admin` group
4. Initialize Incus with a directory-based storage pool
5. Configure a NAT bridge network for container connectivity

### After Bootstrap

**Important**: You must log out and log back in (or start a new shell session) for group membership to take effect.

Quick alternative without logout:
```bash
newgrp incus-admin
```

### Verify Installation

```bash
# Check Incus version
incus version

# List storage pools
incus storage list

# List networks
incus network list

# Launch a test container
incus launch images:alpine/edge test-container

# Verify container is running
incus list

# Clean up test container
incus delete test-container --force
```

## Common Options

### Custom Storage Pool Name

```bash
./run.sh incus_bootstrap.yml -e incus_storage_pool_name=my-pool
```

### Different User

```bash
./run.sh incus_bootstrap.yml -e incus_user=developer
```

### Skip Initialization (Install Only)

```bash
./run.sh incus_bootstrap.yml -e incus_skip_init=true
```

### Custom Network Configuration

```bash
./run.sh incus_bootstrap.yml -e incus_network_ipv4=10.10.10.1/24
```

## Troubleshooting

### "Permission denied" when running incus commands

You need to re-login or run `newgrp incus-admin` to activate group membership.

### Service not starting

Check the service status:
```bash
sudo systemctl status incus.socket
sudo journalctl -u incus.service
```

### Re-running the bootstrap

Safe to run multiple times. Existing containers, storage pools, and networks are preserved.

## What Gets Installed

| Component | Purpose |
|-----------|---------|
| `incus` package | Container/VM management daemon and CLI |
| `incus.socket` | Socket-activated service |
| `incus-user.socket` | Unprivileged user delegation |
| `default` storage pool | Directory-based container storage |
| `incusbr0` network | NAT bridge for container networking |
