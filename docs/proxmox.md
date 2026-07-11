# Proxmox VE LXC Container Setup

Spin up a lightweight LXC container on your Proxmox VE node. Containers attach to a Linux bridge (`vmbr0` by default), pull a LAN IP via DHCP, and are reachable from your workstation just like a Hetzner VM or an Incus container.

## Prerequisites

- A Proxmox VE 8.x node reachable via SSH (`root` or a sudoer)
- A Linux bridge on the node (`vmbr0` by default — created automatically by the Proxmox installer)
- A storage pool that supports container rootfs volumes (`local-lvm` by default; `local-zfs` and `local` directory storage also work)
- An SSH key pair on your workstation (`~/.ssh/id_rsa` and `~/.ssh/id_rsa.pub`)

## Quick Start

```bash
# Install remo (on your workstation)
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash

# One-time: verify the node and download the default LXC template
remo proxmox bootstrap --host prox01 --user root

# Create and configure a container
remo proxmox create --name dev1 --host prox01 --user root

# Connect
remo shell
```

## CLI Commands

```bash
# Create a container on a remote Proxmox node
remo proxmox create --name dev1 --host prox01 --user root

# Override resources
remo proxmox create --name dev2 --host prox01 --user root \
  --cores 4 --memory 4096 --volume-size 40

# Use a different storage / bridge
remo proxmox create --name dev3 --host prox01 --user root \
  --storage local-zfs --bridge vmbr1

# Use a different LXC template (must be downloaded via pveam first)
remo proxmox create --name dev4 --host prox01 --user root \
  --template local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst

# List registered containers
remo proxmox list

# Update dev tools on an existing container
remo proxmox update --name dev1

# Update only specific tools
remo proxmox update --name dev1 --only zellij --only fzf

# Skip specific tools during update
remo proxmox update --name dev1 --skip docker --skip nodejs

# Resize the rootfs (grow only) on an existing container
remo proxmox update --name dev1 --volume-size 40

# Live-tune CPU and/or memory limits (cgroup v2)
remo proxmox update --name dev1 --cores 4 --memory 4096

# Sync remo's registry with the node (rebuild known_hosts entries)
remo proxmox sync --host prox01 --user root

# Destroy a container (rootfs is removed regardless)
remo proxmox destroy --name dev1 --yes

# Destroy and also clean up backup/replication/HA job configs (pct destroy --purge)
remo proxmox destroy --name dev1 --yes --purge

# Bootstrap (verify) a Proxmox node
remo proxmox bootstrap --host prox01 --user root

# Inspect resources on an existing container (cores, memory, rootfs size)
remo proxmox info --name dev1
```

### Create Options

| Option | Default | Description |
|--------|---------|-------------|
| `--host <host>` | (required) | SSH host for the Proxmox node |
| `--user <user>` | (current user) | SSH user for the Proxmox host |
| `--node <node>` | `--host` | Proxmox cluster node name (only differs in clusters) |
| `--bridge <name>` | `vmbr0` | Linux bridge to attach the container to |
| `--storage <name>` | `local-lvm` | Storage pool for the rootfs volume |
| `--template <ref>` | `local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst` | LXC template to use |
| `--cores <n>` | `2` | CPU cores |
| `--memory <MiB>` | `2048` | RAM |
| `--volume-size <GiB>` | `20` | Rootfs size. When the container exists, grows the rootfs via `pct resize`. |
| `--unprivileged/--privileged` | `--unprivileged` | Container privilege mode |
| `--domain <domain>` | (none) | FQDN suffix for the container |
| `--devcontainer-runtime <name>` | `devcontainer` | Devcontainer runtime to install/use: `devcontainer` or `deacon` (experimental). See [Experimental: Deacon runtime](#experimental-deacon-runtime). |

### Update Options

| Option | Description |
|--------|-------------|
| `--only <tool>` | Only update the specified tool (can repeat) |
| `--skip <tool>` | Skip the specified tool (can repeat) |
| `--volume-size <GiB>` | Grow the rootfs via `pct resize` (grow only) |
| `--cores <n>` | Set CPU core count via `pct set` (live; cgroup v2) |
| `--memory <MiB>` | Set memory limit via `pct set` (live) |
| `--host <host>` | Proxmox host (auto-detected from registry if omitted) |
| `--user <user>` | SSH user for the Proxmox host |
| `--devcontainer-runtime <name>` | `devcontainer` or `deacon` (experimental). Re-provisions the launcher scripts to use the chosen runtime. |

Available tools: `docker`, `user_setup`, `nodejs`, `devcontainers`, `github_cli`, `fzf`, `zellij`

### Destroy Options

| Option | Description |
|--------|-------------|
| `--yes`, `-y` | Skip confirmation prompt |
| `--purge` | Pass `--purge` to `pct destroy`: also remove the container from backup/replication/HA job configs. The rootfs is destroyed regardless of this flag. |
| `--host <host>` | Proxmox host |
| `--user <user>` | SSH user for the Proxmox host |

## Features

| Feature | Description |
|---------|-------------|
| **LXC Container** | Lightweight, near-native performance |
| **LAN IP via DHCP** | Reachable from any LAN device including the Proxmox host |
| **Unprivileged + Nesting** | Default security posture; Docker-in-Docker works out of the box |
| **Auto-start on boot** | `--onboot 1` — survives node reboots |
| **Same dev tools as Incus/Hetzner** | Docker, Node.js, fzf, github_cli, devcontainers, zellij, user_setup |

## Experimental: Deacon runtime

By default remo installs the Node-based [`@devcontainers/cli`](https://github.com/devcontainers/cli)
and invokes `devcontainer up` / `devcontainer exec` from the project launcher
scripts. You can opt a deployment into
[**Deacon**](https://github.com/get2knowio/deacon) instead — a single-binary
Rust reimplementation of the devcontainer CLI that needs no Node.js runtime:

```bash
# Per deployment (overrides the global default)
remo proxmox create --name dev1 --host prox01 --user root --devcontainer-runtime deacon

# Switch an existing container's runtime
remo proxmox update --name dev1 --devcontainer-runtime deacon

# As a global default for every new deployment
export REMO_DEVCONTAINER_RUNTIME=deacon
```

Resolution order: `--devcontainer-runtime` flag → `REMO_DEVCONTAINER_RUNTIME`
env → built-in default (`devcontainer`).

When `deacon` is selected, the `deacon` binary is installed (in place of the
npm CLI) and the launcher scripts call `deacon up` / `deacon exec`. remo's
container lifecycle (config-hash rebuild, container-stop on exit) works
unchanged because Deacon sets the spec-canonical `devcontainer.local_folder`
label. Because Deacon's non-interactive workspace-trust gate would otherwise
block host-side lifecycle hooks (`initializeCommand`, dotfiles), remo passes
`--trust-workspace-persist` on `up`.

> **Experimental.** Deacon is opt-in and not yet the default. Known gaps versus
> the reference CLI: feature installation is supported for Dockerfile-based
> configs only (Docker-Compose / image-reference configs with `features` error
> out), and GPU passthrough on Podman is unwired. Validate your projects before
> relying on it.

### Switching an existing deployment

You do **not** need to recreate a container to try Deacon — `update`
re-provisions the runtime in place. Data, projects, and container config are
untouched.

```bash
# Flip to Deacon (installs the binary + re-points the launcher scripts)
remo proxmox update --name dev1 --devcontainer-runtime deacon

# Revert to the Node CLI (symmetric; the switch is just a re-provision)
remo proxmox update --name dev1 --devcontainer-runtime devcontainer
```

After switching, force one clean rebuild per project the first time you open it,
so Deacon builds a container it fully owns rather than adopting one the previous
runtime started:

```bash
touch ~/projects/<project>/.devcontainer-rebuild   # honored on next launch
remo shell -p <project>                             # Deacon rebuilds fresh
```

Notes:

- The previously-installed runtime is left in place (not uninstalled), so
  reverting only re-points the launcher scripts — low risk, fully reversible.
- `update` re-runs the dev-tools roles idempotently; expect it to take about as
  long as the original configure step.

## Bootstrap

**Skip this if your Proxmox node is already configured the way you want it and the Ubuntu 24.04 LXC template is already downloaded.**

```bash
remo proxmox bootstrap --host prox01 --user root
```

### What Bootstrap Does

- Verifies `pct`, `pveam`, `pvesh`, and `pvesm` are available
- Confirms the configured bridge exists (`vmbr0` by default)
- Confirms the configured storage pool exists (`local-lvm` by default)
- Runs `pveam update` and downloads the Ubuntu 24.04 LXC template if it isn't already present

Unlike the Incus bootstrap, this does **not install Proxmox itself** — Proxmox is the host operating system and you set it up at install time. Bootstrap is verify-only plus a template download.

### Bootstrap Options

```bash
# Use a non-default bridge / storage / template
remo proxmox bootstrap --host prox01 --user root \
  --bridge vmbr1 \
  --storage local-zfs \
  --template debian-12-standard_12.7-1_amd64.tar.zst
```

## Networking

### Bridged DHCP (default)

Containers attach to `vmbr0` and pull a LAN IP from your upstream DHCP server:

```
Your LAN (192.168.1.0/24)
├── Router (192.168.1.1)
├── Your PC (192.168.1.10)
├── Proxmox Host (192.168.1.20)
├── Container dev1 (192.168.1.103)  ← Direct LAN IP
└── Container dev2 (192.168.1.104)
```

**Bonus over Incus macvlan**: the Proxmox host can talk to its containers directly. With Incus macvlan, the host can't reach its own containers; with Proxmox bridges, it can.

### Hostname Resolution

If your router registers DHCP hostnames (common with OpenWrt, pfSense, etc.):

```bash
ssh remo@dev1
ssh remo@dev1.int.example.com   # if --domain set
```

Otherwise, `remo proxmox list` shows IPs you can use.

## Templates

Browse what's available:

```bash
ssh root@prox01 "pveam available --section system | grep ubuntu"
```

Common choices (download then pass to `--template`):

| Template | Distro |
|---|---|
| `local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst` | Ubuntu 24.04 LTS (default) |
| `local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst` | Ubuntu 22.04 LTS |
| `local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst` | Debian 12 |

> The Proxmox **standard** templates include `openssh-server`, which the role expects. Avoid the `minimal` variants unless you also customize the role to install SSH.

## Differences from the Incus Provider

| Aspect | Incus | Proxmox |
|---|---|---|
| Provisioning CLI | `incus` | `pct` |
| Image source | On-demand pull from `images:` | Pre-downloaded via `pveam` |
| User provisioning | cloud-init | `pct exec` from the host |
| Network default | macvlan (host can't reach CT) | Bridge (host can reach CT) |
| Identifier | Name | VMID (numeric); hostname stored separately |
| Bootstrap | Installs Incus | Verify-only + template download |

## Troubleshooting

**`Bridge 'vmbr0' not found`**
Your node is configured with a different bridge. Pass `--bridge` to `create` and `bootstrap`. List bridges on the node with `ip -br link show type bridge`.

**`Storage 'local-lvm' not found`**
Your node uses a different storage backend (e.g. `local-zfs`). Pass `--storage`. List with `pvesm status`.

**`LXC template not found`**
Run `remo proxmox bootstrap --host prox01 --user root` to download the default. To use a different one, run `pveam download local <filename>` on the node and pass `--template local:vztmpl/<filename>`.

**Container does not get an IPv4 address**
- Check the bridge is correctly enslaving the upstream NIC: `ip -br link`
- Check the upstream DHCP server actually has free leases
- Check inside the container: `pct exec <vmid> -- ip addr` and `pct exec <vmid> -- journalctl -u networking`

**SSH connection times out after create**
- The container may have come up but `sshd` isn't ready yet. Try again in a few seconds.
- Check `pct exec <vmid> -- systemctl status ssh`.
- Confirm the container's IP from `pct exec <vmid> -- ip addr` matches what `remo proxmox list` shows.

**`pct: command not found`**
You're SSHed to the wrong host — the `--host` should be the Proxmox node itself, not a workstation in front of it.

**Permission errors when creating containers**
Your SSH user needs sudo (or root) on the Proxmox node. The role uses `become: true` so a passwordless sudoer works.
