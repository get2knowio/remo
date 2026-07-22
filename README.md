# Remo

Spin up a fully-configured remote development environment in minutes. One command gives you a persistent, secure coding environment with Dev Containers support — perfect for long-running AI agents that keep working after you disconnect.

## Installation

```bash
# From PyPI (recommended)
uv tool install remo-cli

# Or with pip
pip install remo-cli

# Initialize (installs Ansible collections)
remo init
```

### Prerequisites

- Python 3.11+
- SSH key pair (`~/.ssh/id_rsa`)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Shell completion (optional)

Tab completion for subcommands, flags, and known instance/container names is available for bash, zsh, and fish:

```bash
# bash
remo completion bash >> ~/.bashrc

# zsh
remo completion zsh >> ~/.zshrc

# fish
remo completion fish > ~/.config/fish/completions/remo.fish
```

After re-loading your shell, `remo proxmox info --name <TAB>` will suggest registered container names from your `known_hosts` registry.

---

## Quick Start

```bash
remo hetzner create             # Provision a VM (or: remo aws create / remo incus create / remo proxmox create)
remo shell                      # Connect to your environment
```

You land in an interactive project menu. Pick a project, and you're in a persistent Zellij session with your DevContainer already running. Disconnect anytime — your session survives.

```
  Remote Coding Server
  --------------------

> my-project - active
  another-project
  [Clone new repo]
  [Exit to shell]
```

---

## Choose Your Platform

| | [Hetzner Cloud](docs/hetzner.md) | [AWS](docs/aws.md) | [Incus](docs/incus.md) | [Proxmox](docs/proxmox.md) |
|---|---|---|---|---|
| **Type** | Cloud VM | Cloud VM | Local container | Local container |
| **Location** | EU/US datacenters | Global regions | Your hardware | Your hardware |
| **Cost** | ~€4/month | ~$30/month (~$10 spot) | Your electricity | Your electricity |
| **Storage** | Block volume | EBS / root volume | Host mounts | LVM / ZFS / dir |
| **Access** | Server IP | SSM (no inbound ports) or SSH | LAN hostname | LAN IP |
| **Best for** | EU, budget hosting | US, enterprise, spot instances | Local dev, homelab | Proxmox homelab |

All platforms give you the same dev workflow and tooling described below.

---

## The Dev Workflow

### Persistent Sessions

[Zellij](https://zellij.dev/) keeps your terminal sessions alive across SSH disconnects:

- **Detach**: `Ctrl+d` returns to the project menu
- **Reconnect**: SSH back in, select the same project to resume exactly where you left off

### Project Menu

The `fzf`-powered menu shows your projects from `~/projects`:

- **Arrow keys** or **1-9**: Select a project
- **Enter**: Launch/attach to the project's Zellij session
- **c**: Clone a new repository
- **x**: Exit to shell

### Jump Straight to a Project

Skip the menu and land directly in a project (devcontainer auto-launches):

```bash
remo shell -p my-app
```

Run a one-shot command inside the project's devcontainer instead of opening
a shell — quote the command as a single string:

```bash
remo shell -p my-app --exec 'pytest -x'
remo shell -p my-app --exec 'claude --remote-control'
```

Fire-and-forget — kick off a command on the remote and exit SSH immediately:

```bash
remo shell -p my-app --detach --exec 'claude remote-control --name remo-rc'
remo shell -p my-app --detach --exec './long-build.sh'
```

Detached output is captured to `~/.local/state/remo/<project>.log` on the
remote, so you can tail it later (`remo shell -p my-app --exec 'tail -f
~/.local/state/remo/my-app.log'`). The command's environment gets
`REMO_INSTANCE` and `REMO_PROJECT` exported automatically — handy for
deterministic naming, e.g.:

```bash
remo shell -p my-app --detach --exec \
  'claude remote-control --name "remo-$REMO_INSTANCE-$REMO_PROJECT"'
```

Then on your phone, open claude.ai/code and pick the session by name.

### Port Forwarding

Forward remote ports to your local machine during SSH sessions:

```bash
remo shell -L 8080                  # Forward remote :8080 to local :8080
remo shell -L 9000:8080             # Forward remote :8080 to local :9000
remo shell -L 8080 -L 3000          # Forward multiple ports
remo shell -L 8080 --no-open        # Skip auto-opening browser
```

Web ports automatically open in your browser when the tunnel is established.

### File Transfer

Copy files between your local machine and any remote environment:

```bash
remo cp ./file.txt :/tmp/           # Upload
remo cp :/var/log/app.log ./        # Download
remo cp -r ./my-dir :/home/remo/    # Recursive upload
remo cp --progress big-file.tar :/tmp/  # Show transfer progress
```

Uses colon notation — a bare `:path` targets your default environment, or `name:path` for a specific one.

### Version Checking

Remo checks that your local CLI and remote environment are running compatible versions before connecting. If the remote is behind, you'll be prompted to update it. Use `--no-update-check` to skip.

---

## What's Installed

Every remo environment includes:

| Tool | Description |
|------|-------------|
| **Docker + Compose** | Official Docker CE with compose plugin |
| **Dev Containers CLI** | `devcontainer up`, `devcontainer exec`, etc. |
| **Node.js 24 LTS** | From NodeSource repository |
| **GitHub CLI** | `gh` for GitHub workflow integration |
| **Zellij** | Terminal multiplexer for persistent sessions |
| **fzf** | Fuzzy finder powering the project menu (server-side) |

---

## Snapshots

Capture a point-in-time copy of an instance before a risky change, then roll
back if it breaks. Available on every provider, with the same command surface:

```bash
remo <provider> snapshot create <instance> [--name NAME] [--description TEXT]
remo <provider> snapshot list    [INSTANCE]
remo <provider> snapshot restore <instance> <snapshot> [-y]
remo <provider> snapshot delete  <instance> <snapshot> [-y]
```

`--name` defaults to `remo-YYYYMMDD-HHMMSS`. `-y` / `--yes` bypasses the
confirm prompt on destructive operations.

| Provider | Create | Restore | Notes |
|---|---|---|---|
| **Incus**   | seconds | in-place rollback (container stopped briefly) | Free; uses native `incus snapshot`. |
| **Proxmox** | seconds | in-place rollback (container stopped briefly) | Free; requires snapshot-capable rootfs storage (ZFS, LVM-thin, Btrfs, Ceph, NFS, CIFS). `dir` storage is rejected pre-flight. |
| **AWS**     | async — several minutes | in-place EBS volume swap; stops the instance, swaps the root volume, restarts. Typically 2-5 min downtime. | Costs $ per GB-month in EBS. Pre-restore root volume is preserved as a tagged orphan — delete it manually once you've verified the restore. |
| **Hetzner** | async — several minutes | server rebuild from the snapshot image, in-place. Typically 1-2 min downtime. | Costs € per GB-month. |

`remo` does not estimate storage cost — check your provider's billing console.

The `destroy` command on each provider checks for existing snapshots first and
offers to clean them up. Decline and the snapshots remain (you'll be warned
they continue to incur storage costs on AWS/Hetzner).

---

## Register an SSH-reachable host

Already have SSH access to a box — a VM, a bare-metal server, someone else's
container — but no hypervisor host access, cloud credentials, or API token?
`remo add` registers it directly into your registry. Provider `sync` is bulk
discovery that needs provider/host access; `add` is a single manual
registration that needs only SSH reachability.

```bash
remo add NAME TARGET [--user USER] [--port PORT] [--identity PATH] [--verify] [--yes]
```

`TARGET` is `[user@]host[:port]`. `--user`, `--port`, and `--identity` override
(or fill in) the corresponding parts of `TARGET`. When no user is given, the
default SSH user is `remo` (reported back to you); the default port is `22`.

- **`--identity PATH`** records a private key that is persisted and passed to
  `ssh -i` on connect — no `~/.ssh/config` editing needed for a host that
  requires a non-default key.
- **`--verify`** does an opt-in, fail-closed SSH reachability check *before*
  registering: on failure it surfaces the SSH error, writes nothing, and exits
  non-zero. Without `--verify` there is no network round-trip at all.
- **Re-running `add`** with an existing added-host name and a changed target
  updates it in place (confirm unless `--yes`) — it never creates a duplicate.
  It refuses to overwrite a provider-managed entry (incus/proxmox/aws/hetzner)
  of the same name.
- **IPv6:** un-bracketed IPv6 literals are rejected — use a hostname or an
  `~/.ssh/config` alias instead (bracketed `[::1]:22` is not supported in this
  release).

Once added, `remo shell NAME` and `remo cp` work over the same direct SSH path
as any other host.

```bash
remo add mybox 10.0.0.5 --port 2222 --identity ~/.ssh/mybox_ed25519 --verify
remo shell mybox                    # open a shell over direct SSH
remo cp ./deploy.sh mybox:/tmp/     # copy a file up
remo remove mybox                   # deregister when you're done
```

`remo remove NAME [--yes]` deregisters an added host by deleting **only** the
local registry entry — it makes no connection to and no change on the remote
environment (unlike a provider `destroy`, which tears down infrastructure). It
refuses to act on a provider-managed host and points you at that provider's
`destroy` instead.

## CLI Reference

```bash
# Connect to environment
remo shell                          # Auto-connect (or picker if multiple)
remo shell my-env                   # Connect to a specific environment
remo shell -p my-app                # Skip the menu, jump to ~/projects/my-app
remo shell -p my-app --exec 'pytest -x'              # Run command in devcontainer
remo shell -p my-app --detach --exec 'claude remote-control --name rc'  # Fire and exit
remo shell -L 8080                  # Shell + forward remote :8080 to local :8080
remo shell -L 9000:8080             # Shell + forward remote :8080 to local :9000
remo shell -L 8080 -L 3000          # Shell + forward multiple ports
remo shell -L 8080 --no-open        # Skip auto-opening browser
remo shell --no-update-check        # Skip version check

# File transfer
remo cp ./file.txt :/tmp/           # Upload file
remo cp :/var/log/app.log ./        # Download file
remo cp -r ./dir :/home/remo/       # Recursive copy
remo cp --progress big.tar :/tmp/   # Show progress

# Setup
remo init                           # Install Ansible collections

# Register an SSH-reachable host (provider-neutral; needs only SSH access)
remo add NAME [user@]host[:port]    # Register a single SSH host
remo add NAME host --port 2222 --identity ~/.ssh/key   # Custom port + key
remo add NAME host --verify         # Fail-closed SSH reachability check first
remo add NAME host --user alice     # Override default SSH user (default: remo)
remo remove NAME [--yes]            # Deregister an added host (local-only)

# Hetzner Cloud
remo hetzner create                 # Provision VM
remo hetzner list                   # List registered VMs
remo hetzner info [--name N]        # Show type, cores, memory, volume size
remo hetzner sync                   # Discover existing VMs
remo hetzner update                 # Update dev tools
remo hetzner update --volume-size 100   # Grow persistent volume + FS
remo hetzner destroy [--yes]        # Tear down (keeps volume)

# AWS (SSM access — no inbound ports)
remo aws create                     # Provision EC2 via SSM
remo aws create --spot              # Use spot instance (~70% savings)
remo aws list                       # List registered instances
remo aws sync                       # Discover existing instances
remo aws update                     # Update dev tools
remo aws update --volume-size 100   # Grow EBS volume + FS in place
remo aws stop [--yes]               # Stop instance (pause billing)
remo aws start                      # Start a stopped instance
remo aws reboot                     # Reboot instance
remo aws destroy [--yes]            # Tear down (keeps storage)
remo aws info [--name N]            # Show type, cores, memory, EBS size

# Incus Containers
remo incus create --name <n> [--host H]  # Create container
remo incus list                     # List registered containers
remo incus info --name <n>          # Show cores, memory, root size
remo incus sync [--host H]          # Discover remo-managed containers
remo incus sync [--host H] --all    # Also adopt non-remo containers on the host
remo incus update --name <n>        # Update dev tools (also marks as remo-managed)
remo incus update --name <n> --volume-size 40 --cores 4 --memory 4096
remo incus destroy --name <n> [--yes]    # Destroy container
remo incus bootstrap                # Initialize Incus on host

# Proxmox VE LXC Containers
remo proxmox create --name <n> --host <node>  # Create LXC container
remo proxmox list                   # List registered containers
remo proxmox info --name <n>        # Show cores, memory, rootfs size
remo proxmox sync --host <node>     # Discover remo-managed containers
remo proxmox sync --host <node> --all   # Also adopt non-remo containers on the node
remo proxmox update --name <n>      # Update dev tools (also marks as remo-managed)
remo proxmox update --name <n> --volume-size 40 --cores 4 --memory 4096
remo proxmox destroy --name <n> [--yes] [--purge]   # Destroy container
remo proxmox bootstrap --host <node>  # Verify node + download LXC template

# Snapshots (all four providers)
remo <provider> snapshot create <instance>                       # Auto-named
remo <provider> snapshot create <instance> --name pre-x --description "before upgrade"
remo <provider> snapshot list                                    # All instances
remo <provider> snapshot list <instance>                         # One instance
remo <provider> snapshot restore <instance> <snap-name> [-y]     # In-place rollback
remo <provider> snapshot delete <instance> <snap-name> [-y]      # Remove
# `<provider> destroy` will list existing snapshots and offer to clean them up first.

# Updates
uv tool upgrade remo-cli            # Update CLI to latest version
remo <platform> update              # Update dev tools on remote

# Help
remo --help
remo <command> --help

# Shell completion
remo completion bash                # Print bash activation script (also: zsh, fish)
```

See platform-specific docs for full options:
- [Hetzner Cloud](docs/hetzner.md)
- [AWS](docs/aws.md)
- [Incus Containers](docs/incus.md)
- [Proxmox VE LXC Containers](docs/proxmox.md)

### Environment Variables

| Variable | Description |
|----------|-------------|
| `REMO_HOME` | Config directory for remo state (default: `~/.config/remo`) |
| `REMO_DEVCONTAINER_RUNTIME` | Default devcontainer runtime for new deployments: `devcontainer` (default) or `deacon` (experimental). Overridden per-deployment by `--devcontainer-runtime`. See [Proxmox docs](docs/proxmox.md#experimental-deacon-runtime). |

---

## Web Session Interface

Don't have the `remo` CLI or an SSH setup on the device in front of you? `remo web` runs a small
home-lab Docker service that discovers every project across all of your registered instances
(Proxmox, AWS, Hetzner, Incus) and streams a real interactive terminal to any browser — no local
CLI, no SSH keys on the client. It connects to your instances server-to-instance over SSH, the same
way the CLI does, and attaches to the exact same persistent Zellij/devcontainer session `remo shell`
would.

> ⚠️ **Security boundary:** `remo web` is a **single-trusted-user MVP with no login**. Anyone who can
> reach the service can open a shell on **every instance in your registry**. There is no
> authentication, no per-user isolation, and no public-internet exposure story. Bind it only to a
> trusted LAN interface, a Tailscale/tailnet address, or a loopback reverse proxy — **never expose it
> to the public internet.**

### Quick install

```bash
uv sync --extra web             # installs the FastAPI/Uvicorn web extra (not part of the normal CLI)
uv run remo web check           # validates registry, SSH identity, runtime dir, executables, reachability
uv run remo web serve --host 127.0.0.1 --port 8080   # local dev
```

For a home-lab install, use Docker Compose — see [`docker/compose.example.yml`](docker/compose.example.yml)
for a ready-to-adapt file covering both deployment modes (tmpfs runtime dir, healthcheck,
non-root/read-only hardening in either case):

- **Bind-mount mode**: mount your existing registry and SSH key read-only — the container runs with
  your identity, on the same box as your config.
- **Adopted mode**: mount nothing. The container generates its own SSH identity in a writable state
  volume, and a single `remo web adopt` from your workstation pushes your registry and authorizes
  that identity on every instance — your personal private key never leaves the workstation
  (`remo web push` re-syncs later changes).

Full architecture, security model, Compose walkthrough, adoption workflow, credentials/SSM setup,
discovery states, terminal limits, troubleshooting, and upgrade notes:
**[docs/web-session-interface.md](docs/web-session-interface.md)**.

---

## Troubleshooting

**Installed remo on a new machine with existing instances?**
```bash
remo aws sync                          # Discover AWS instances with 'remo' tag
remo hetzner sync                      # Discover Hetzner VMs with 'remo' label
remo incus sync                        # Discover remo-managed Incus containers
remo proxmox sync --host <node>        # Discover remo-managed Proxmox LXC containers
```

All four providers now filter `sync` to the containers/instances **remo created**.
On Incus/Proxmox, `remo`-created containers are marked at provision time (an Incus
`user.remo=true` config key or a Proxmox `remo` guest tag), and a default `sync`
registers only those. To adopt containers `remo` did not create, use
`sync --all` (a one-time, unmarked adoption) or `remo <provider> update <name>`
(permanently marks one). Containers created before this feature are unmarked;
the first default `sync` after upgrading names them and prints both remedies
rather than silently dropping or re-marking them.

**SSH connection fails?**
```bash
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa
```

**Ansible collection not found?**
```bash
remo init  # Reinstalls dependencies
```

**Platform-specific issues?**
See troubleshooting sections in:
- [Hetzner troubleshooting](docs/hetzner.md#troubleshooting)
- [AWS troubleshooting](docs/aws.md#troubleshooting)
- [Incus troubleshooting](docs/incus.md#troubleshooting)
- [Proxmox troubleshooting](docs/proxmox.md#troubleshooting)

---

## Uninstalling

```bash
# Remove the CLI
uv tool uninstall remo-cli      # or: pip uninstall remo-cli

# Remove remo config and state
rm -rf ~/.config/remo
```

| Path | Contents |
|------|----------|
| `~/.config/remo/` | Runtime state: `known_hosts` (environment registry) |

**Note:** Uninstalling remo does not destroy any cloud resources (EC2 instances, Hetzner VMs, Incus or Proxmox containers). Run `remo <platform> destroy` first if you want to tear those down.

---

## License

MIT License - see LICENSE file for details.
