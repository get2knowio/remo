# Research: Reach a Local Host over SSH Too

All anchors are against `main` at cb5d865 (Explore audit, 2026-11-27).

## R1 — One probe, three facts (FR-001/002, FR-010–013)

**Decision**: Extend the existing pre-connect probe instead of adding a
capabilities round trip. New remote command (SSH and local alike):

    cat ~/.remo-version 2>/dev/null && printf '\n#remo-host-info\n' && cat ~/.local/state/remo/host-info 2>/dev/null || test -f ~/.remo-version

`cat` of a missing marker still exits non-zero → `(None, None)` exactly as
today. Everything before the `#remo-host-info` separator line is the version
(stripped); everything after is `key=value` lines from the host-info record
(R3). A new `core/ssh.probe_host_tools(host) -> HostToolsProbe(version, error,
info: HostInfo)` does the work; `check_remote_version` becomes a thin wrapper
returning the same 2-tuple (its timeout/OSError/255 branches move into the
probe unchanged). `connect_to_host` switches to the probe and passes
`menu_command=info.menu_command` and `host_kind=info.host_kind` onward.

**Rationale**: `connect_to_host` already pays this round trip before every
connect (`cli/shell.py:265-268`); `get_capabilities` is never called from the
CLI and would add ~1 RTT plus a JSON parse to every `remo shell`. The version
file itself cannot carry a second line — older clients read the whole stdout
as the version.

**Gate edges**: the probe runs when `not no_update_check`, now even when the
local version is `unknown` (dev builds) — the version *comparison* is still
skipped then. With `--no-update-check` no probe runs, so a bare menu falls back
to today's plain login; documented.

**Alternatives**: version-number gating (`remote >= 5.x`) — breaks for dev
builds and `--remo-source`; `remo-host capabilities` — extra round trip.

**Pins that change**: `test_host_access_ssh_baseline.py:154-157`,
`test_ssh.py:442`, `test_shell_local.py:139` (probe string). Pins that must NOT
change: every other `TestShellArgv`/`TestResumeArgv` case.

## R2 — Explicit menu command over SSH (FR-001/003, US1 AS4)

**Decision**: When the probe reports `menu_command=1` and the connection is a
bare `remo shell` to a remote (`access.remote`) host, `shell_connect` passes
`tty=True` and the remote command

    REMO_MENU=1 "$HOME/.local/bin/project-menu" && exec env REMO_MENU=1 /bin/sh -c 'exec "${SHELL:-/bin/sh}" -l'

- `-tt` is required: with a command, ssh does not allocate a pty and fzf needs
  one.
- "Exit to shell" (menu exit 0) still yields a login shell, as the hook does
  today; Esc/Ctrl-C (exit 1) ends the connection, as the hook's `exit` does.
- The `.bashrc` PROJECT MENU block (`roles/user_setup/tasks/main.yml:326-349`)
  gains `&& [[ -z "$REMO_MENU" ]]`, so the login shell after "Exit to shell"
  does not start a second menu. The hook does not fire for the menu command
  itself anyway (`bash -c` is non-interactive).
- Remote hosts get the guarded hook and the host-info `menu_command=1` from
  the same role run, so a host never advertises the command without the guard.
- The local host is unchanged (`MENU_COMMAND`, `host_access.py:33`).

## R3 — Host-info record (FR-009/010)

**Decision**: `~/.local/state/remo/host-info`, mode 0644, `key=value` lines,
rendered by the shared `roles/user_setup/tasks/host_scripts.yml` (so remote and
local both write it):

    host_kind=local          # or remote (from remo_local_install)
    menu_command=1
    installed_by=<remo_version or empty>

`remo-host capabilities --json` adds `"host_kind":"<value>"` when the file has
it (omitted otherwise). `RemoteCapability.host_kind: str | None = None`;
`from_dict` already ignores unknown keys, so older clients are unaffected.
`local.env` stays the local-only bash/projects record; `_state_signature()`
(`providers/local.py:391-410`) adds host-info so "Already up to date" stays
correct. The web `CapabilityOut` is **not** changed (web out of scope;
Principle IV artifacts untouched).

**Alternative**: infer "local" from the presence of `local.env` (the menu
already does, `project-menu.sh.j2:78-85`) — workable but implicit; an explicit
record also carries `installed_by` (FR-009).

## R4 — Advice and refusal for a locally managed host (FR-011/012)

**Decision**:
- `known_hosts.upgrade_command_for(host, *, host_kind=None)`: when
  `host_kind == "local"` and the host is not itself the `local` entry, return
  `"remo local setup (run it on that machine)"`. Existing callers pass nothing
  → unchanged.
- `connect_to_host`: for such a host, the "no marker"/"older" branches print a
  warning naming that command and do not `confirm`/run `_run_tools_upgrade`.
- `providers/added.configure`: after `guard_added_ssh_host_only` and the
  root/field checks, and **before** the apt-upgrade confirm prompt, run the
  probe through `SshAccess`; `host_kind == "local"` → `PreconditionError("…is
  managed by `remo local setup` — run that on <host> instead. Nothing was
  changed.")`. A failed probe falls through to today's flow (which then fails
  at `verify_reachable` as before).
- `remo resume` uses `upgrade_command_hint` via `connect_to_host`, so it gets
  the same wording.

## R5 — One session directory on macOS (FR-005–008)

**Decision**: Zellij honours `ZELLIJ_SOCKET_DIR`. A new `remo_pin_session_dir`
in `remo-lib.sh.j2` (sourced before every zellij call in all four scripts):
on Darwin only, and only if `ZELLIJ_SOCKET_DIR` is unset, use
`$HOME/.local/state/remo/zellij`; create it 0700; if it exists but is not owned
by the user or is group/other-accessible, print an error and exit 1 (never
share). Export `ZELLIJ_SOCKET_DIR`. The existing `XDG_RUNTIME_DIR` block stays
byte-identical (its regex test, `test_zellij_socket_dir.py:59-66`), and the
call is placed right after it in each script. On Linux the helper is a no-op.

- Home-folder state survives logout; sessions live as long as the zellij
  server does.
- Socket path length (macOS `sun_path` 104): `/Users/<u>/.local/state/remo/zellij/<contract>/<session>` stays well under it for normal names; the helper does not check length (zellij reports its own error).
- Pre-existing sessions (FR-008): `remo local setup` runs `zellij list-sessions
  --short` with `ZELLIJ_SOCKET_DIR` unset (default dir), and if any exist
  prints once: "N session(s) started before this upgrade are still running
  outside remo's session directory; reach them with `zellij attach NAME`
  (outside remo) until they end." Best-effort; zellij missing/erroring →
  silent.
- A user's own manual `zellij` at the desk won't see remo's sessions unless
  they export the same variable — documented.

## R6 — Tools on PATH over SSH on macOS (FR-008a)

**Decision**: `remo local setup` records `REMO_PATH=<dirs>` in `local.env`:
the de-duplicated directories of the detected `zellij`, `fzf`, `git`, the
devcontainer runtime and the container engine CLI (plus `/opt/homebrew/bin`
or `/usr/local/bin` when present). `remo-lib.sh.j2` gains
`remo_load_local_path`: if `local.env` exists, prepend each `REMO_PATH` dir
not already on `PATH`. Called at the same point as the session-dir pin. On
remote hosts `local.env` does not exist → no-op. No rc file is touched.

`test_local_setup_playbook.py:137-140` pins `local.env`'s exact content and is
updated to include the new line.

## R7 — Resume opt-in (FR-014–018)

**Decision**: Implemented in Python in `providers/local.py`
(`configure_ssh_resume(enable, *, runner, interactive)`), not Ansible — the one
sudo step needs an explicit prompt, and `become` with a password prompt inside
`run_playbook` is awkward and untestable.

- Drop-in: `/etc/ssh/sshd_config.d/remo-accept-env.conf`, content
  `# Added by remo local setup --allow-ssh-resume\nAcceptEnv TZ REMO_TAB_KEY\n`
  (same words as the remote `accept-tz.conf`).
- Supported iff `/etc/ssh/sshd_config` is readable and has an `Include`
  covering `/etc/ssh/sshd_config.d/*` (macOS 13+ and modern Debian/Ubuntu/
  Fedora do). Otherwise: print the line to add by hand; setup still succeeds.
- Idempotent: read the drop-in (world-readable); identical content → "already
  enabled", no sudo. Opt-out with no file → "already disabled", no sudo.
- Change path: explain, `confirm()` (refuse with manual instructions when
  `not sys.stdin.isatty()` and not `--yes`), then via `sudo`: `install -m 0644
  <tmp> <dropin>` (or `rm -f <dropin>`), `sshd -t` (on failure: revert the
  file, raise `OperationFailedError`), then reload — Linux `systemctl reload
  ssh || systemctl reload sshd`; macOS none needed (launchd starts sshd per
  connection and re-reads config).
- All subprocess calls go through an injectable runner, so tests cover add /
  re-run / remove / decline / non-interactive / validate-failure with no real
  sudo.
- CLI: `remo local setup --allow-ssh-resume/--no-allow-ssh-resume` (tri-state,
  default untouched), applied after the playbook. `--yes` counts as consent.

## R8 — `remo projects` (FR-019–021)

**Decision**: New `cli/projects.py` (`remo projects list|add|remove HOST …`)
and `providers/projects.py` holding the generic `_run_projects(host, verb,
**kw)` / `add_project(host, path, name)` / `remove_project` / `list_projects` /
`render_projects`, moved out of `providers/local.py` and taking a resolved
`KnownHost` (`get_host_access(host)` picks SSH or local). `providers/local.py`
keeps its `add/remove/list` as thin wrappers (local host + client-side
`abspath(expanduser)` of PATH), so `remo local …` is unchanged.

- Remote PATH is sent verbatim; `remo-host projects add` gains leading-`~/`
  expansion host-side (the client quotes it, so the remote shell won't).
- Exit 4 (`unsupported subcommand`) or 127 → `PreconditionError` naming
  `upgrade_command_for(host)`.
- Hints in `render_projects` are parametrised by host (`remo projects add HOST
  …` vs `remo local add …`).
- `tests/unit/cli/test_main.py` `EXPECTED_COMMANDS` gains `projects`; the
  structure diagram lists the two new modules.

## R9 — Tests and CI

New host-script test modules (`test_session_dir_pin.py`,
`test_host_info.py`) are added to the `host-scripts-macos` job list. Darwin
behaviour is exercised on Linux too with a `uname` shim (precedent:
`test_host_stats_darwin.py`).
