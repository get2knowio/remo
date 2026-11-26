# Research: Local Machine as a Remo Host

Grounded in a read-only audit of `main` at 6a7d86a (2026-11-26). Every
decision below records the choice, why, and what was rejected.

## R1 — Host-access seam (`core/host_access.py`)

**Decision**: A new `core/host_access.py` defines one small interface,
`HostAccess`, with exactly two implementations chosen by host type:

| Operation | SSH (unchanged) | Local |
|---|---|---|
| `interactive_argv(host, remote_cmd=None, *, tty, tunnels, tab_key)` | today's `build_ssh_base_cmd` + optional `remote_cmd` (`shell_connect`) | `[bash, "-c", remote_cmd or 'exec "$HOME/.local/bin/project-menu"']` with the real TTY |
| `command_prefix(host, *, batch)` | today's ssh prefix (`resume._ssh_prefix`, `check_remote_version`) | `[bash, "-c", 'eval "$*"', "remo-local"]` — joins the trailing words with single spaces, exactly as ssh does |
| `env(host, tab_key)` | `{**os.environ, REMO_TAB_KEY}` + `SendEnv` opt | `{**os.environ, PATH=~/.local/bin:…, REMO_TAB_KEY}` |

`get_host_access(host)` returns the local implementation for
`type == LOCAL_HOST_TYPE` and the SSH one for everything else. Consumers routed
through it: `core/ssh.shell_connect`, `core/ssh.check_remote_version`,
`core/resume._ssh_prefix` (hence `run_lookup`/`is_project_live`), and
`cli/shell.connect_to_host`'s upgrade step.

**Why**: `core/remo_host_client.py` already takes an argv *prefix* and never
builds SSH itself (`run_remo_host_json` :454 builds
`[*prefix, REMOTE_PATH_PREFIX, *argv]`). SSH joins those words into one remote
shell command; the local prefix does the same by passing one joined string to
`bash -c`, so `REMOTE_PATH_PREFIX` (`PATH="$HOME/.local/bin:$PATH"`) keeps
working unchanged (`eval "$*"` reproduces ssh's word joining) and no remo-host call site needs a second code path.

**SSH byte-identity**: the SSH implementation calls the existing builders with
the existing arguments; characterization tests pin today's argv for every host
type *before* the refactor (Principle VI).

**Rejected**: (a) `ssh localhost` — needs sshd, contradicts the feature.
(b) A per-call `if host.type == "local"` in each consumer — scatters the rule
over five modules and gives #223 nothing to build on. (c) Routing
`core/attach.py` and the web terminal — web is out of scope; `attach.py` stays
SSH-only and documents that.

**#223**: the interface is public-shaped (documented in
`contracts/host-access.md`) so a future non-SSH transport can implement it, but
no descriptor field is added now — only the two built-in implementations exist,
selected by type. #223 stays open.

## R2 — The `local` pseudo-type

**Decision**: `LOCAL_HOST_TYPE = "local"` in `core/config.py`, beside
`ADDED_HOST_TYPE`. Like `ssh` it is never a registered provider; `registry.py`
accepts it in `is_known_type`, serializes a nested `"local": {"projects_dir":
"…"}` only when set, and parses it back. On `KnownHost`, `projects_dir` rides
the `region` field (the same per-type positional reuse `ssh` makes of
`instance_id`/`region` for port/identity), exposed through a `projects_dir`
property mirroring `ssh_port`/`ssh_identity`. `host` is the literal
`localhost`, `user` the invoking user, `access` `direct`.

**Why**: The `ssh` pseudo-type already demonstrates every needed hook
(`is_known_type`, nested object, `upgrade_command_for`, the lifecycle guards,
`display_region`). Reusing `region` avoids a `KnownHost` schema change; the
registry file stays v2 and an older remo preserves the entry as an unknown type
(027 FR-009).

**One per machine**: `remo local setup` searches for an existing `local` entry
and updates it (renaming if `--name` differs) instead of adding a second one.

**Rejected**: a `ProviderDescriptor` for `local` — it has no lifecycle verbs,
and the factory would generate create/destroy/snapshot commands that make no
sense.

## R3 — Never pushed or synced (FR-004a)

**Decision**: One helper `is_portable_host(host)` (False for `local`) applied
at the four seams the audit found: `web_adopt.run_push` host list (:1993),
`build_adoption_payload` (:453, belt-and-braces), `web_sync._sync_flow` local
list (:829), and `web_sync.parse_remote_registry` (:668, drops a received
`local` entry with a warning).

**Why**: A deployment cannot reach another machine's local host, and a second
workstation pulling the entry would treat its own machine as that host.

## R4 — `remo local` CLI

**Decision**: `cli/local.py` (Click wiring only) mounting
`remo local {setup, check, add, remove, list}`; business logic in
`providers/local.py` (no Click, no `sys.exit`, raises `core/errors.py`).
`add/remove/list` run `remo-host projects …` through `HostAccess` so the index
is owned by the host scripts, exactly as on a remote host.

**Refusals** (FR-009): `remo cp` to a local host, `remo shell local -L …`, and
`remo configure <local>` raise `PreconditionError` with the alternative
(`copy files directly`, `ports are already local`, `use remo local setup`).
`guard_not_added_ssh_host`'s sibling `guard_not_local_host` blocks provider
lifecycle verbs by name collision, mirroring the `ssh` guard.

## R5 — Local setup via Ansible (single source of truth)

**Decision**: Extract the four script-render tasks (user_setup main.yml
:492-522) into `ansible/roles/user_setup/tasks/host_scripts.yml`, parametrized
by `remo_scripts_home` (default `/home/{{ remo_user }}`) and owner/group
(omitted when `remo_local_install` is true). The remote role
`include_tasks` it (same rendered files, same paths, same modes). A new
`ansible/local_setup.yml` play — `hosts: localhost`, `connection: local`,
`become: false`, `gather_facts: false` — includes the same task file with
`remo_scripts_home={{ lookup('env','HOME') }}`, then writes `~/.remo-version`
and creates `~/.local/state/remo/{tabs}`. `run_playbook` is called with
`-e ansible_python_interpreter=<sys.executable>` and `ANSIBLE_BECOME=False`.

**Why**: The prompt demands one source for local and remote scripts;
`ansible.cfg` sets `become=True` globally, which the play overrides; the
inventory's `/usr/bin/python3` is wrong on macOS, so the interpreter is passed
explicitly. Idempotency comes from the `template`/`copy` modules
(changed=0 on a second run).

**Rejected**: rendering the templates in Python with `jinja2` directly —
duplicates Ansible's template semantics and the task list, so local and remote
could drift.

## R6 — bash ≥ 4

**Decision**: Keep the scripts bash-4 (`declare -A`, `mapfile`). Their shebang
becomes `#!{{ remo_bash_path | default('/bin/bash') }}` — remote renders stay
byte-identical — and local setup passes the detected bash ≥ 4 absolute path
(e.g. `/opt/homebrew/bin/bash`). Scripts that exec each other inherit it via
their shebangs; the local transport also runs `bash -c` with that path.

**Why**: Porting ~1,900 lines to bash 3.2 risks every remote host; Homebrew is
already a prerequisite on macOS for Zellij and fzf.

**Rejected**: `#!/usr/bin/env bash` — on macOS `/bin/bash` (3.2) is often
first on `PATH`, giving non-deterministic failures.

## R7 — Project index

**Decision**: A per-host TSV at `~/.local/state/remo/projects.tsv`, one
`name<TAB>absolute-path` per line, owned by `remo-host`. A new sourced helper
template `remo-lib.sh` (installed beside the scripts) provides
`remo_project_list` (explicit entries ∪ subdirectories of `PROJECTS_DIR` when
set; explicit wins on a name clash, which is reported) and
`remo_project_path NAME`. Every `"$PROJECTS_DIR/$name"` site in the four
scripts and in the session shell (R8) is replaced by the resolver. New
additive remo-host verbs: `projects add --path PATH [--name N] --json`,
`projects remove NAME --json`, `projects list --json`; `clone` appends a
record, `delete` drops it. Names are validated by the existing bash
`validate_project_name` in `remo-host`, the twin of
`core/validation.validate_project_name` (which rejects control characters, so
a name can never contain the TSV's tab or newline). Writes use `mktemp` + `mv` in the
index's directory (atomic on both Linux and macOS). Protocol stays v1;
`sessions list` keeps its shape and gains nothing it would not have from a
`PROJECTS_DIR` project.

**Missing paths**: listed with `"status": "missing"` in `projects list` and
marked `✗ missing` in the menu with the fix; never removed automatically.

**Why TSV**: bash can parse it without `jq` (not a prerequisite), and a tab
cannot appear in a validated name.

## R8 — Devcontainer start-up without `.bashrc`

**Decision**: Move the `.bashrc` devcontainer block body (main.yml :351-470)
into a new script template `remo-session-shell`. `project-menu` and
`project-launch` start sessions with
`zellij attach --create NAME options --default-shell <~/.local/bin/remo-session-shell> --default-cwd <project path>`
(verified on zellij 0.45.1; `options` has existed well before the role's
`zellij_min_version` 0.40.0). The session shell resolves the project through
the index (not `dev_workspace_dir/$ZELLIJ_SESSION_NAME`), runs the existing
devcontainer up/exec logic, and otherwise `exec`s the user's login shell.

**Compatibility**: Zellij applies `options` only when it creates the session,
so sessions created before the upgrade still rely on the `.bashrc` block. The
remote `.bashrc` block is therefore kept as a one-line shim that calls
`remo-session-shell` only when `REMO_SESSION_SHELL` is unset (the new script
exports it), so logic lives in one place and nothing runs twice. Local setup
never touches rc files (FR-012); local sessions always use `--default-shell`.

**Portability inside the moved block**: `sha256sum` → `shasum -a 256`
fallback; `mapfile` stays (bash ≥ 4).

## R9 — macOS portability of the scripts

| Construct (file:line) | Replacement |
|---|---|
| `XDG_RUNTIME_DIR=/run/user/$UID` pin (remo-host :50, menu :20, launch :44, devshell :24) | pin only when `/run/user/$UID` exists; otherwise leave zellij's default |
| `sed 's/\x1b…'` (remo-host :167, menu :33…) | `$'\e'` in the pattern (portable across GNU and BSD sed) |
| `date -Iseconds` (:633, launch :115) and `-r` (:720) | `date -u +%Y-%m-%dT%H:%M:%SZ`; `-r FILE` → a `stat` helper with GNU/BSD branches |
| `nohup setsid` (:643, launch :116) | `setsid` when present, else plain `nohup` (detached job survives the parent) |
| `timeout 10 git fetch` (menu :108) | `timeout`/`gtimeout` when present, else run without a limit in the background |
| `df -B1 -P` (:529) | `df -Pk` × 1024 |
| `/proc`, `nproc`, `/sys` hwmon (:460-590) | on Darwin: `sysctl -n hw.ncpu/hw.memsize`, `vm_stat`, `uptime`; temperatures reported unavailable — through the existing JSON contract |
| `find -mmin/-mtime -delete` (:357, :614) | portable as-is (BSD find supports both) |

Every replacement keeps today's Linux output byte-identical; the template tests
pin that.

## R10 — Prerequisite detection

**Decision**: `providers/local.py::check_prerequisites()` (pure, PATH-driven,
testable with shims) returns a list of `Prerequisite` results:

| Tool | Required | Detection |
|---|---|---|
| bash ≥ 4 | yes | candidates: `$HOMEBREW_PREFIX/bin/bash`, `/opt/homebrew/bin/bash`, `/usr/local/bin/bash`, every `bash` on PATH; first whose `BASH_VERSINFO[0] ≥ 4` |
| git, zellij, fzf | yes | `shutil.which` |
| devcontainer runtime | yes | same precedence as `tasks/resolve_devcontainer_runtime.yml`: deacon, then `devcontainer` |
| container engine | yes (installed) | `docker` on PATH; running = `docker info` exits 0 within 10 s → else warning |
| gh | no | `shutil.which` |

Install hints: Homebrew on Darwin; on Linux by `/etc/os-release` `ID`/`ID_LIKE`
(apt, dnf, pacman), falling back to the project URL.

## R11 — Version check and upgrade offer

**Decision**: `check_remote_version` reads `~/.remo-version` through
`HostAccess.command_prefix` (local: `cat` via the local runner). The upgrade
offer's command for a local host is `remo local setup` (via
`upgrade_command_for`), and `_run_tools_upgrade` calls
`providers.local.setup(assume_yes=True)` for it.

## R12 — Tests and CI

- Characterization first: pin today's ssh argv for every host type
  (`test_ssh*.py`, `test_resume.py`, `test_remo_host_client.py`) before
  routing through `HostAccess`.
- Local transport: argv/env tests for menu, `-p`, `--exec`, `--detach`,
  version check, resume lookup, `REMO_TAB_KEY`.
- Index + macOS portability: execute rendered scripts with PATH shims (the
  existing `tests/unit/test_ansible_templates.py` pattern).
- `tests/unit/test_zellij_socket_dir.py` currently assumes `/run/user`: it
  gains the "absent → not pinned" case.
- `tests/ansible/test_remo_host_idempotency.py` pins the remo-host render task
  shape; it moves to `host_scripts.yml`.
- Local setup: run `ansible/local_setup.yml` twice in a temp `HOME` and assert
  the second run reports `changed=0` and that no rc file was touched.
- New CI job `host-scripts-macos` on `macos-latest` with `brew install bash
  zellij fzf` running the host-script test modules.

## R13 — Constitution IX

New templates and the new playbook ship as package data inside the wheel
(`ansible/` is force-included). That is a packaging surface: the PR validates
with `scripts/check_plugin_wheels.sh` plus a wheel-level check that
`ansible/local_setup.yml` and the new templates are in the built wheel (Tier 2,
CI-built wheel).
