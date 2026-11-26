# Your own machine as a remo host

`remo local` makes the machine you are sitting at a remo host: the same project
menu, Zellij sessions and devcontainers as a remote host, with **no SSH server,
no VM and no root**. Nothing is installed system-wide, no user is created and no
shell startup file (`~/.zshrc`, `~/.bashrc`, `~/.profile` …) is touched.

It is built for a Mac that already has Docker Desktop or OrbStack, but works on
any Linux workstation too.

## Install

```bash
remo local check     # which prerequisites are present (read-only)
remo local setup     # install the host scripts and register the host
```

`remo local setup` is safe to re-run: a second run changes nothing and says
`Already up to date`. It writes only under your home directory
(`~/.local/bin`, `~/.local/state/remo`, `~/.remo-version`) and registers one
host called `local` (`--name` picks another name; there is only ever one local
host per machine).

Option: `--projects-dir DIR` registers a folder whose subfolders are all
projects (the same convention remote hosts use for `~/projects`). Pass `''` to
clear it later. It is optional: you can add checkouts one by one instead.
The folder is also recorded in `~/.local/state/remo/local.env`, so after
`remo remove local` a plain `remo local setup` restores it (an explicit
`--projects-dir ''` clears it there too).

### Prerequisites

remo **detects** these; it never installs them. A missing required tool makes
`remo local setup` stop, name it with the install command for your platform,
and register nothing.

| Tool | Required | macOS | Debian/Ubuntu | Fedora | Arch |
|------|----------|-------|---------------|--------|------|
| bash 4 or newer (macOS ships 3.2) | yes | `brew install bash` | `sudo apt install bash` | `sudo dnf install bash` | `sudo pacman -S bash` |
| git | yes | `brew install git` | `sudo apt install git` | `sudo dnf install git` | `sudo pacman -S git` |
| zellij | yes | `brew install zellij` | [install guide](https://zellij.dev/documentation/installation) | `sudo dnf install zellij` | `sudo pacman -S zellij` |
| fzf | yes | `brew install fzf` | `sudo apt install fzf` | `sudo dnf install fzf` | `sudo pacman -S fzf` |
| devcontainer runtime: `deacon` (preferred) or `devcontainer` | yes | `brew install deacon` or `npm install -g @devcontainers/cli` | same | same | same |
| container engine (`docker` on PATH) | yes | Docker Desktop, OrbStack or Colima | Docker Engine | Docker Engine | Docker Engine |
| gh | no | `brew install gh` | `sudo apt install gh` | `sudo dnf install gh` | `sudo pacman -S github-cli` |

If the engine is installed but not running, setup warns and carries on; the
project session then falls back to your login shell with a hint to start it
(Docker Desktop / OrbStack / `colima start` / `systemctl start docker`).

## Add your existing checkouts in place

Nothing is copied or moved: a project is a name pointing at a folder.

```bash
remo local add ~/code/api              # name defaults to the folder name: "api"
remo local add ~/work/site --name web  # pick another name
remo local list
remo local remove web                  # forget it; the folder is untouched
```

`remo local list` shows each project's source (`added` for an explicit
checkout, `folder` for one found under `--projects-dir`) and status. A folder
that has been moved or deleted is shown as `missing` with the fix
(`remo local add <new-path> --name <name>`); it is never removed
automatically. A name already in use, a path already added under another name,
or a missing directory is refused with a message saying how to fix it.

## Work

```bash
remo shell                              # project menu (pick `local` if you have several hosts)
remo shell local -p api                 # straight into api's Zellij session / devcontainer
remo shell local -p api --exec 'git status'   # run a command in its devcontainer; its exit status is yours
remo shell local -p api --detach --exec 'make long-job'
remo resume                             # put this terminal tab back where it was
```

Choosing a project lands you in its Zellij session, inside its devcontainer
when it has one, otherwise in your login shell in the project folder. The
devcontainer is started by a Zellij session shell (`remo-session-shell`), not by
a `.bashrc` block, which is why no rc file needs to change.

## How it differs from a remote host

| | Remote host | Local host |
|---|---|---|
| Transport | SSH | none: the scripts run on your own terminal |
| `remo cp` | works | refused: copy files directly |
| `remo shell -L PORT` | tunnels a port | refused: the ports are already on this machine |
| `remo configure NAME` | installs tools over SSH | refused: use `remo local setup` |
| `remo web push` / `remo web sync` | mirrored to the console | never sent or accepted (a deployment cannot reach your machine); a `local` entry received from a deployment is ignored with a warning |
| Upgrading tools | `remo <type> upgrade` / `remo configure` | `remo local setup` (offered automatically when `remo shell` finds older scripts) |
| Deregister | `remo remove NAME` | `remo remove local` — scripts, the project list and the projects folder stay; `remo local setup` registers it again |

An older remo that does not know the `local` host type keeps its registry entry
untouched.

## Retiring a VM

If you were using an OrbStack/Colima VM only to get remo, move to the local host
project by project:

1. For each project, clone it on your Mac (or copy the checkout) and
   `remo local add <path>`.
2. Check `remo local list` and `remo shell`.
3. Deregister the VM host: `remo remove <vm-name>` (this only edits your
   registry; the VM itself is untouched).

## Troubleshooting

- `remo local setup has not completed on this machine` — run `remo local setup`.
- `required tools are missing` — install what the table lists, then re-run.
- A project shows `missing` — the folder moved; re-add it with the new path.
- Sessions that start in a plain shell instead of the devcontainer — start the
  container engine; `remo shell` prints the hint.

See also: [README](../README.md), [web console](web-session-interface.md)
(the local host is never part of it).
