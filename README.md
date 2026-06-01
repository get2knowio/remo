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

## Notifier

The **notifier** is a small approval bridge that runs as a hardened container on
a remo host. When an agentsh-secured devcontainer needs human sign-off for an
operation, it POSTs an approval request to the notifier, which messages you on
Telegram with **Approve** / **Deny** buttons and returns your decision. It fails
secure: a timeout, a shutdown, or a lost connection all mean *deny*. The wire
protocol is documented in
[`src/remo_cli/notifier/docs/wire-protocol.md`](src/remo_cli/notifier/docs/wire-protocol.md).

The notifier's runtime dependencies (FastAPI, python-telegram-bot, …) live in an
optional `notifier` extra and are installed only inside the container — a normal
`remo` install does not pull them in.

### Notifier setup

1. **Create a Telegram bot**: message [`@BotFather`](https://t.me/BotFather), run
   `/newbot`, follow the prompts, and save the bot token.
2. **Find your chat id**: message `@userinfobot` from your own account; it
   replies with your numeric user id — that's your `authorized_chat_id`.
3. **Message your new bot once** (any message) so it can DM you.
4. **Export credentials** on the machine where `remo` runs:
   ```bash
   export REMO_NOTIFIER_TELEGRAM_BOT_TOKEN="12345:ABC...your-token"
   export REMO_NOTIFIER_TELEGRAM_CHAT_ID="987654321"
   ```
5. **Deploy**: `remo notifier deploy <host>` (omit `<host>` to fuzzy-pick).
6. **Verify**: `remo notifier test <host>` — you should get a Telegram message
   within ~2 s; tapping Approve or Deny returns the decision to the CLI.

### Notifier commands

```bash
remo notifier deploy  <host> [--rebuild]   # apply the role; --rebuild forces a fresh image
remo notifier status  <host>               # GET /v1/health
remo notifier logs    <host> [-f] [-n N]   # journalctl -u remo-notifier.service
remo notifier test    <host>               # push a test approval, print the decision
remo notifier restart <host>               # systemctl restart remo-notifier.service
```

### "Always" — standing grants

Approval messages offer **✅ Approve · ⏩ Always… · ❌ Deny**. Tapping **Always…**
lets you auto-approve a *class* of operation (e.g. `git push *` in this project)
so matching requests are approved instantly without pinging you again. Grants are
held in memory only (cleared on restart → you're asked again — fail-closed),
expire after a default 8h, and default to the narrowest scope. Manage them from
Telegram: `/rules` (list + revoke), `/revoke <id>`, `/pause` and `/resume`.
Auto-approvals are logged and summarized in a periodic digest. Tune via the
`[grants]` config block (see `src/remo_cli/notifier/docs/config-schema.md`).

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
remo incus sync [--host H]          # Discover existing containers
remo incus update --name <n>        # Update dev tools
remo incus update --name <n> --volume-size 40 --cores 4 --memory 4096
remo incus destroy --name <n> [--yes]    # Destroy container
remo incus bootstrap                # Initialize Incus on host

# Proxmox VE LXC Containers
remo proxmox create --name <n> --host <node>  # Create LXC container
remo proxmox list                   # List registered containers
remo proxmox info --name <n>        # Show cores, memory, rootfs size
remo proxmox sync --host <node>     # Discover existing containers
remo proxmox update --name <n>      # Update dev tools
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

---

## Troubleshooting

**Installed remo on a new machine with existing instances?**
```bash
remo aws sync                          # Discover AWS instances with 'remo' tag
remo hetzner sync                      # Discover Hetzner VMs with 'remo' label
remo incus sync                        # Discover Incus containers
remo proxmox sync --host <node>        # Discover Proxmox LXC containers
```

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
