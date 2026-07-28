# Incus Container Setup

Spin up a lightweight system container on your own hardware. Containers get IPs from your LAN's DHCP and are accessible by hostname from any machine on your network.

## Prerequisites

- Incus installed and bootstrapped on your host (see [Bootstrap](#bootstrap) below)
- SSH key pair (`~/.ssh/id_rsa`)

## Quick Start

```bash
# Install remo (on your workstation)
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash

# Create and configure container
remo incus create dev1 --host incus-host --host-user youruser

# Connect
remo shell
```

## Configuration

No additional configuration needed for Incus. Authentication uses your SSH key.

## CLI Commands

```bash
# Create container on remote host
remo incus create dev1 --host myserver --host-user paul

# Create container on localhost
remo incus create dev1

# Create with domain for FQDN
remo incus create dev1 --domain int.example.com

# Override resources at create time
remo incus create dev1 --cores 4 --memory 4096 --volume-size 40

# List registered containers
remo incus list

# Refresh dev tools on existing container
remo incus upgrade dev1 --host myserver --host-user paul

# Refresh only specific tools
remo incus upgrade dev1 --only zellij --only fzf

# Skip specific tools during a tools refresh
remo incus upgrade dev1 --skip docker --skip nodejs

# Resize the root disk on an existing container
remo incus resize dev1 --volume-size 40

# Live-tune CPU and/or memory limits (cgroup v2)
remo incus resize dev1 --cores 4 --memory 4096

# Write the remo-managed marker on an existing container
remo incus tag dev1 --host myserver --host-user paul

# Destroy container
remo incus destroy dev1 --host myserver --host-user paul --yes

# Destroy and also remove host mount directories
remo incus destroy dev1 --host myserver --host-user paul --yes --remove-storage

# Bootstrap Incus on a host
remo incus host bootstrap myserver --host-user paul

# Inspect resources on an existing container
remo incus info --name dev1
```

### Create Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host <host>` | `localhost` | Incus host to connect to |
| `--host-user <user>` | (current user) | SSH user on the **Incus host**, for host-side `incus` commands — not the container login (always `remo`) |
| `--domain <domain>` | (none) | Domain for FQDN (e.g., `int.example.com`) |
| `--image <image>` | `images:ubuntu/24.04/cloud` | Cloud image to use |
| `--volume-size <GiB>` | (profile default) | Override the root disk size via `incus config device override root size=...` |
| `--cores <n>` | (profile default) | Set CPU core limit (`limits.cpu`) |
| `--memory <MiB>` | (profile default) | Set memory limit (`limits.memory`) |

### Upgrade Options

`remo incus upgrade` refreshes dev tools only. It makes zero provider-side
writes.

| Option | Description |
|--------|-------------|
| `--only <tool>` | Only refresh specified tool (can repeat) |
| `--skip <tool>` | Skip specified tool (can repeat) |
| `--host <host>` | Incus host |
| `--host-user <user>` | SSH user on the **Incus host**, for host-side `incus` commands — not the container login (always `remo`) |
| `-v` | Verbose output |

Available tools: `docker`, `user_setup`, `nodejs`, `devcontainers`, `github_cli`, `fzf`, `zellij`

### Resize Options

`remo incus resize` resizes resources only; it never runs the configure play.
At least one of `--volume-size`, `--cores`, or `--memory` is required — the
command errors listing those three flags if none is given.

| Option | Description |
|--------|-------------|
| `--volume-size <GiB>` | Resize the root disk (grow only). May require a container restart depending on the storage backend. |
| `--cores <n>` | Set CPU core limit (`limits.cpu`); live on cgroup v2 |
| `--memory <MiB>` | Set memory limit (`limits.memory`); live on cgroup v2 |
| `--host <host>` | Incus host |
| `--host-user <user>` | SSH user on the **Incus host**, for host-side `incus` commands — not the container login (always `remo`) |

### Tag Options

`remo incus tag` writes the remo-managed marker only. If the container is
already tagged, this is a reported no-op (exit `0`, zero writes); a write
failure is a hard error.

| Option | Description |
|--------|-------------|
| `--host <host>` | Incus host |
| `--host-user <user>` | SSH user on the **Incus host**, for host-side `incus` commands — not the container login (always `remo`) |

### Destroy Options

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation prompt |
| `--remove-storage` | Also remove host mount directories (e.g. `/home`, `/workspace`) bound into the container. Without this flag, mount directories on the host are preserved. |
| `--host <host>` | Incus host |
| `--host-user <user>` | SSH user on the **Incus host**, for host-side `incus` commands — not the container login (always `remo`) |

### Sync

```bash
# Reconcile the registry with what's actually on the host (default project only)
remo incus sync --host myserver --host-user paul

# Also adopt containers without the remo managed marker
remo incus sync --host myserver --host-user paul --all

# Skip the removal confirmation prompt
remo incus sync --host myserver --host-user paul --yes

# Preview the plan without changing the registry or prompting
remo incus sync --host myserver --host-user paul --dry-run
```

`sync` reconciles the registry against every container on the given host's
**default project** (`--all-projects` is deliberately not queried, so
containers in other projects are outside this run's scope and are never
read, matched, or touched). Containers and instances still present at the
provider are never removed just because they lack the marker — only a
container that has genuinely disappeared, in a fully-enumerated scope, is
proposed for removal, and that removal always requires confirmation (or
`--yes`) before it's written. `--dry-run` prints the same plan — additions,
updates, removals, and any unmarked containers skipped or retained — without
prompting or changing anything, and always exits `0`.

By default only containers carrying the `user.remo=true` managed marker are
*added*; `--all` widens that to every container on the host for this run
only. To mark one permanently instead, use `remo incus tag <n>
--host <h>`.

> **`remo shell` does not tag.** When `remo shell` offers a tools update, it
> configures the instance only — it never writes provider-side state, because
> tagging means reaching the hypervisor (a machine you did not name at the
> prompt). Only explicit `remo incus tag` (and `remo incus create`) write the
> marker — `remo incus sync` never does. If `sync` reports instances as
> unmarked, `remo incus tag` fixes it permanently.

| Option | Description |
|--------|-------------|
| `--host <host>` | Incus host (default: `localhost`) |
| `--host-user <user>` | SSH user on the **Incus host**, for host-side `incus` commands — not the container login (always `remo`) |
| `--use-ip` | Store each container's resolved IP instead of its name |
| `--all` | Also register containers without the managed marker (this run only) |
| `--yes`, `-y` | Skip the removal confirmation prompt |
| `--dry-run` | Show what would change without writing to the registry |

## Features

| Feature | Description |
|---------|-------------|
| **System Container** | Lightweight, near-native performance |
| **LAN IP via DHCP** | Accessible from any machine on your network |
| **Hostname DNS** | Works if your router registers DHCP hostnames |
| **Host Mounts** | Optional persistent data directories from the Incus host |
| **macvlan Network** | Containers appear as separate devices on your LAN |

## Bootstrap

**Skip this if you already have Incus installed and initialized.**

To use Incus containers, first bootstrap Incus on your host machine:

### Bootstrap a Remote Host

```bash
remo incus host bootstrap 192.168.1.100 --host-user paul
```

### Bootstrap Localhost

```bash
remo incus host bootstrap
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
remo incus host bootstrap -v

# For advanced options, use the ansible playbook directly:
cd ~/.remo/ansible
./run.sh incus_bootstrap.yml -e "incus_network_parent=eth0"
./run.sh incus_bootstrap.yml -e "incus_network_type=bridge"
```

## Networking

### macvlan (Default)

Containers get IPs directly from your LAN's DHCP server and appear as separate devices:

```
Your LAN (192.168.1.0/24)
├── Router (192.168.1.1)
├── Your PC (192.168.1.10)
├── Incus Host (192.168.1.20)
├── Container dev1 (192.168.1.101)  ← Direct LAN IP
└── Container dev2 (192.168.1.102)
```

**Limitation**: The Incus host cannot directly reach containers via macvlan. Access containers from a different machine on your LAN.

### Hostname Resolution

If your router registers DHCP hostnames (common with OpenWrt, pfSense, etc.):

```bash
ssh remo@dev1                    # Short hostname
ssh remo@dev1.int.example.com    # FQDN (if --domain set)
```

## Host Mounts

Mount directories from the Incus host into containers for persistent storage:

```bash
# Via ansible directly (advanced)
cd ~/.remo/ansible
./run.sh incus_site.yml \
  -e "container_name=dev1" \
  -e 'incus_container_mounts=[{"source": "/data/projects", "path": "/home/remo/projects"}]'
```

## Cloud Images

| Image | Description |
|-------|-------------|
| `images:ubuntu/24.04/cloud` | Ubuntu 24.04 LTS (default) |
| `images:ubuntu/22.04/cloud` | Ubuntu 22.04 LTS |
| `images:debian/12/cloud` | Debian 12 Bookworm |
| `images:rockylinux/9/cloud` | Rocky Linux 9 |

Browse available images: `incus image list images:`

## Troubleshooting

**Container not accessible by hostname?**
- Verify your router/DHCP server registers hostnames
- DNS registration may take a few seconds after container boot
- Try by IP first: `incus list` shows container IPs

**Can't reach container from Incus host?**
This is a known macvlan limitation. Access containers from a different machine on your LAN.

**"Permission denied" on incus commands?**
Log out and back in after bootstrap, or run `newgrp incus-admin`.

**Container stuck in "Starting"?**
Check Incus logs: `incus info dev1 --show-log`

**DHCP not assigning IP?**
Verify your network has a DHCP server and the macvlan interface is configured correctly:
```bash
incus network show incusbr0
```

**SSH connection fails?**
Ensure your SSH public key exists at `~/.ssh/id_rsa.pub` and is readable.
