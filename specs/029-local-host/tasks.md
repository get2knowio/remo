---

description: "Task list for 029-local-host — local machine as a Remo host"
---

# Tasks: Local Machine as a Remo Host

**Input**: Design documents from `specs/029-local-host/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R13), data-model.md, contracts/ (host-access, project-index, cli-local, registry-local-entry), quickstart.md

**Tests**: Required. Constitution v2.1.0 (Principles VI, VII) and spec FR-007, FR-013, FR-031/032 demand test-backed verification; characterization tests pin today's SSH behaviour BEFORE it is refactored.

**Organization**: Grouped by user story (spec.md US1–US4) after shared setup and foundational phases, following plan.md Phasing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4 from spec.md
- Test command (core, serial — no pytest-xdist): `uv run --no-sync pytest <files>`; full suite: `uv run --no-sync pytest` in the core-only env (`uv sync --all-extras --no-extra hetzner --no-extra ec2`)

---

## Phase 1: Setup — characterize today's SSH behaviour (Principle VI)

**Purpose**: Pin the exact argv/env remo produces for every existing host type so the host-access refactor (Phase 2) is provably byte-identical (FR-007, SC-006).

- [X] T001 [P] Add characterization tests in tests/unit/core/test_host_access_ssh_baseline.py pinning `core/ssh.shell_connect`'s full subprocess argv and env for one host of each type (incus, proxmox, the fixture plugin type, an `ssh` added host with port + identity, and the test SSM provider from tests/ssm_provider.py) across: bare menu, `-p NAME`, `-p NAME --exec CMD`, `-p NAME --detach`, each with and without a tab key (SendEnv=REMO_TAB_KEY + env), and with `-L` tunnels; mock `subprocess.run`/`termios` the same way tests/unit/core/test_ssh.py does
- [X] T002 Add characterization tests in tests/unit/core/test_host_access_ssh_baseline.py (same file, separate class) for `core/ssh.check_remote_version` argv (ConnectTimeout/BatchMode/`cat ~/.remo-version`) and for `core/resume._ssh_prefix` / `run_lookup` / `is_project_live` argv passed to `core/remo_host_client` for the same host set
- [X] T003 Run `uv run --no-sync pytest tests/unit/core/test_host_access_ssh_baseline.py` and confirm it passes on the unmodified code (record the count in the task note)

**Checkpoint**: Baseline green on unmodified code — any later change in SSH argv fails these tests.

---

## Phase 2: Foundational — seams every story needs

**⚠️ No user-story work begins until this phase is complete.**

### Host-access seam (research R1, contracts/host-access.md)

- [X] T004 Add `LOCAL_HOST_TYPE = "local"` beside `ADDED_HOST_TYPE` in src/remo_cli/core/config.py
- [X] T005 Write tests first in tests/unit/core/test_host_access.py for `get_host_access()` selection (local vs SSH for every other type), LocalAccess `interactive_argv` (menu: `[BASH, "-c", 'exec "$HOME/.local/bin/project-menu"']`; with a remote_cmd), `env` (PATH prefixed with ~/.local/bin, REMO_TAB_KEY present iff given), `supports_tunnels()` False, `command_prefix` joining words exactly like ssh (a `REMOTE_PATH_PREFIX` word followed by remo-host argv runs as one shell command), and the PreconditionError when `~/.local/state/remo/local.env` or `~/.local/bin/project-menu` is missing
- [X] T006 Implement src/remo_cli/core/host_access.py per contracts/host-access.md: `HostAccess` Protocol, `SshAccess` (delegating to the existing builders in core/ssh.py and the BatchMode prefix currently in core/resume.py, unchanged), `LocalAccess` (reads REMO_BASH from ~/.local/state/remo/local.env; `command_prefix` = `[BASH, "-c", 'eval "$*"', "remo-local"]`, which joins the trailing argv words with single spaces exactly like ssh), `get_host_access(host)`; no provider names, no Click
- [X] T007 Route src/remo_cli/core/ssh.py `shell_connect` and `check_remote_version` through `get_host_access()` (argv + env), keeping SSH argv byte-identical; for LocalAccess, `-L` tunnels raise `PreconditionError("port tunnels do not apply to the local host: its ports are already on this machine")` and the Linux-only `ss -tlnH` port check is skipped
- [X] T008 Route src/remo_cli/core/resume.py `_ssh_prefix` through `get_host_access(host).command_prefix(host, batch=True)`
- [X] T009 Run `uv run --no-sync pytest tests/unit/core/test_host_access_ssh_baseline.py tests/unit/core/test_host_access.py tests/unit/core/test_ssh.py tests/unit/core/test_resume.py tests/unit/core/test_remo_host_client.py tests/unit/cli/test_shell.py tests/unit/cli/test_resume.py` — all green, baseline unchanged

### `local` pseudo-type (research R2/R3, contracts/registry-local-entry.md)

- [X] T010 [P] Write tests first in tests/unit/core/test_registry_local_entry.py: round-trip with and without `projects_dir` (byte-stable write→read→write), `is_known_type("local")` True / `is_provider_type("local")` False, rejection of `access: ssm` and a relative `projects_dir` (tolerant-read warning, entry skipped), `canonicalize_entry` leaves it untouched, and an older-reader simulation (unknown type preserved verbatim)
- [X] T011 Implement the `local` pseudo-type in src/remo_cli/core/registry.py (is_known_type, known_host_to_entry nested `"local": {"projects_dir"}` only when set, entry_to_known_host parse, legacy path untouched, validation) and add `KnownHost.projects_dir` (type local → region) in src/remo_cli/models/host.py; `display_region` blank for local
- [X] T012 [P] Update src/remo_cli/core/known_hosts.py: `upgrade_command_for(local)` → `remo local setup`; add `guard_not_local_host(name, provider)` mirroring `guard_not_added_ssh_host`, and call it from src/remo_cli/core/lifecycle.py next to the existing guard; tests in tests/unit/core/test_known_hosts.py and tests/unit/core/test_lifecycle.py
- [X] T013 [P] Write tests in tests/unit/core/test_web_local_filter.py: `build_adoption_payload` never contains a local entry; `run_push` never processes (keyscan/authorize) a local host; `web_sync._sync_flow` local list excludes it; `parse_remote_registry` drops a received `local` entry with a warning (FR-004a)
- [X] T014 Implement the FR-004a filter (one helper, e.g. `is_portable_host`) at src/remo_cli/core/web_adopt.py `run_push` host list (~:1993) and `build_adoption_payload` (~:453), and src/remo_cli/core/web_sync.py `_sync_flow` local list (~:829) and `parse_remote_registry` (~:668)

### Shared script rendering + shebang (research R5/R6)

- [X] T015 Move the four script-render tasks from ansible/roles/user_setup/tasks/main.yml (~:492-522) into a new ansible/roles/user_setup/tasks/host_scripts.yml parametrized by `remo_scripts_home` (default `/home/{{ remo_user }}`) and owner/group omitted when `remo_local_install | default(false)`; `include_tasks` it from main.yml so remote renders are identical; update tests/ansible/test_remo_host_idempotency.py to pin the task shape in its new file
- [X] T016 Make each template's shebang `#!{{ remo_bash_path | default('/bin/bash') }}` in ansible/roles/user_setup/templates/{remo-host,project-menu,project-launch,devshell}.sh.j2; add a test in tests/unit/test_ansible_templates.py that rendering without `remo_bash_path` yields `#!/bin/bash` (remote byte-identical) and with it yields the given path

### Project index resolver (research R7, contracts/project-index.md)

- [X] T017 Write rendered-script tests first in tests/unit/test_project_index.py (render with jinja2 like tests/unit/test_ansible_templates.py; run with a temp HOME and PATH shims): `remo_project_list` = explicit ∪ implicit, explicit wins a clash with the implicit one reported `shadowed`, missing path → `missing`, empty/missing index file → only implicit, malformed line → error naming file+line and file not rewritten, empty `PROJECTS_DIR` → no implicit entries, `remo_project_path` resolves explicit before implicit and exits 3 when unknown
- [X] T018 Create ansible/roles/user_setup/templates/remo-lib.sh.j2 with `validate_project_name` MOVED here from ansible/roles/user_setup/templates/remo-host.sh.j2 (~:258; remo-host then sources remo-lib.sh and uses it — one bash copy, still the twin of core/validation.validate_project_name), `remo_project_list`, `remo_project_path`, `remo_project_add`, `remo_project_remove` (index at `${REMO_PROJECT_INDEX:-$HOME/.local/state/remo/projects.tsv}`, dir 0700 / file 0600, mktemp+mv atomic writes, name validated by `validate_project_name`, path absolute/existing/no control characters, duplicate-path refusal) plus portable helpers used in Phase 3 (`remo_iso_now`, `remo_file_mtime_iso`, `remo_sha256`, `remo_strip_ansi`, `remo_detach`); add it to ansible/roles/user_setup/tasks/host_scripts.yml
- [X] T019 Replace every `"$PROJECTS_DIR/$name"`-style path build with `remo_project_path` and every directory scan with `remo_project_list` in ansible/roles/user_setup/templates/remo-host.sh.j2 (~:132,189,242,247,296,529,824,829,846,888,962), project-menu.sh.j2 (~:52,107,131,261,297,388), project-launch.sh.j2 (~:82) and devshell.sh.j2 (~:58), sourcing remo-lib.sh; `sessions list` JSON shape unchanged
- [X] T020 Run `uv run --no-sync pytest tests/unit/test_project_index.py tests/unit/test_ansible_templates.py tests/unit/test_project_launch_tab_record.py tests/ansible/test_remo_host_idempotency.py` — green; existing remote-host behaviour (projects-dir only) unchanged

**Checkpoint**: Host access, `local` type, web filters, shared rendering and the index resolver exist; SSH behaviour pinned and unchanged.

---

## Phase 3: User Story 1 — Work on this machine's projects with `remo shell` (Priority: P1) 🎯 MVP

**Goal**: `remo local setup` + `remo shell` opens the menu locally; choosing a project lands in its Zellij session inside its devcontainer, with no SSH server.

**Independent Test**: In a temp HOME with shimmed zellij/fzf/devcontainer/docker, run `remo local setup --projects-dir <tmp>/projects` with a project folder inside it, run `remo shell local` and assert the local argv runs project-menu with the detected bash and that launching uses `zellij attach --create NAME options --default-shell …/remo-session-shell --default-cwd <path>`; manual Mac gate per quickstart.md §3.

### Tests for User Story 1

- [X] T021 [P] [US1] Rendered-script tests in tests/unit/test_session_shell.py for remo-session-shell: devcontainer project → runs `<devcontainer_cli_bin> up` then `exec` into the container with REMO_DEVCONTAINER_CMD honoured; non-devcontainer project → execs the user's login shell; rebuild-hash behaviour preserved (sha256sum or `shasum -a 256` shim); exports REMO_SESSION_SHELL=1; project resolved via the index (a project outside PROJECTS_DIR works); container engine unreachable (`docker info` shim failing) → prints the start-engine hint and drops to the login shell without running `devcontainer up`
- [X] T022 [P] [US1] Rendered-script tests in tests/unit/test_ansible_templates.py for macOS portability with PATH shims standing in for BSD tools: no `/run/user/$UID` → XDG_RUNTIME_DIR not pinned (extend tests/unit/test_zellij_socket_dir.py accordingly); `$'\e'` ANSI stripping; ISO timestamps without `date -Iseconds`; detached jobs start without `setsid`; `git fetch` without `timeout`; `df -Pk` sizing; Linux output byte-identical to before for each construct
- [X] T023 [P] [US1] Tests in tests/unit/test_host_stats_darwin.py (new file) for `remo-host host stats --json` on a simulated Darwin (`uname` shim, no /proc, `sysctl`/`vm_stat` shims): real cpu/memory values where shimmed, temperatures reported unavailable, contract shape unchanged, never exits non-zero
- [X] T024 [P] [US1] Test in tests/unit/cli/test_shell_local.py: `remo shell local` (and picking `local` from the picker) runs the LocalAccess menu argv with the user's env and resets the terminal afterwards; no `ssh` in any subprocess argv

### Implementation for User Story 1

- [X] T025 [US1] Create ansible/roles/user_setup/templates/remo-session-shell.sh.j2 from the `.bashrc` devcontainer block body (ansible/roles/user_setup/tasks/main.yml ~:351-470): resolve the project via remo-lib.sh, run the devcontainer up/exec/stop logic with `| default()`-safe templating, `remo_sha256` instead of `sha256sum`, export REMO_SESSION_SHELL=1, and before `devcontainer up` check the container engine (`docker info`, short timeout) — when it is not reachable print how to start it (Docker Desktop / OrbStack / `colima start` / `systemctl start docker`) and fall back to the login shell instead of a raw engine error (FR-018); otherwise fall back to `exec "${SHELL:-/bin/bash}" -l`; render it from ansible/roles/user_setup/tasks/host_scripts.yml
- [X] T026 [US1] Launch sessions with `zellij attach --create "$name" options --default-shell "$HOME/.local/bin/remo-session-shell" --default-cwd "$path"` in ansible/roles/user_setup/templates/project-menu.sh.j2 (`launch_session` ~:415, keep the retry/cleanup) and ansible/roles/user_setup/templates/project-launch.sh.j2 (~:170)
- [X] T027 [US1] Replace the `.bashrc` devcontainer block in ansible/roles/user_setup/tasks/main.yml with a guarded shim that runs `remo-session-shell` only when ZELLIJ_SESSION_NAME is set and REMO_SESSION_SHELL is unset (sessions created before the upgrade), so the logic lives only in the script (FR-020); keep the SSH-login project-menu block unchanged
- [X] T028 [US1] Apply the macOS-portability replacements from research R9 in ansible/roles/user_setup/templates/{remo-host,project-menu,project-launch,devshell}.sh.j2 using the remo-lib.sh helpers: guarded XDG pin, ANSI strip, ISO dates (incl. `-r` file mtime), setsid fallback, timeout/gtimeout fallback, `df -Pk`, Darwin host stats via `sysctl`/`vm_stat`/`uptime` with temperatures unavailable
- [X] T029 [US1] Create ansible/local_setup.yml: `hosts: localhost`, `connection: local`, `become: false`, `gather_facts: false`; vars `remo_local_install: true`, `remo_scripts_home: "{{ lookup('env','HOME') }}"`, `dev_workspace_dir: "{{ remo_local_projects_dir | default('') }}"`, `remo_bash_path`, devcontainer runtime vars; `include_tasks` roles/user_setup/tasks/host_scripts.yml; create `~/.local/state/remo` (0700) and `tabs/`; write `~/.local/state/remo/local.env` (`REMO_BASH=…`) and `~/.remo-version` (when `remo_version` non-empty); no rc-file tasks
- [X] T030 [US1] Implement `setup()` in src/remo_cli/providers/local.py (no Click, no sys.exit): resolve bash ≥ 4 (research R10 candidates), run `run_playbook("local_setup.yml", extra_vars)` with `-e ansible_python_interpreter=<sys.executable>`, `remo_bash_path`, `remo_version`, devcontainer runtime and env `ANSIBLE_BECOME=False`; then create/update the single `local` registry entry via core/registry.py (default name `local`, rename on `--name`, set/clear `projects_dir`); raise `OperationFailedError` on playbook failure and `PreconditionError` on a name clash with a non-local entry
- [X] T031 [US1] Create src/remo_cli/cli/local.py (Click wiring only) with the `remo local` group and `setup [--name] [--projects-dir] [--yes] [-v]` calling providers.local.setup through the existing error boundary; mount it in src/remo_cli/cli/main.py; update tests/unit/cli/surface_baseline.py for the new group
- [X] T032 [US1] Run the US1 tests plus `uv run --no-sync pytest tests/unit/test_ansible_templates.py tests/unit/test_zellij_socket_dir.py tests/unit/test_session_shell.py tests/unit/cli/test_shell_local.py tests/ansible` — green

**Checkpoint**: US1 works end to end against shims; the Mac manual gate can be run with projects in a configured projects folder.

---

## Phase 4: User Story 2 — Add existing checkouts in place (Priority: P1)

**Goal**: `remo local add/remove/list` manage the per-host index; names, collisions and missing paths behave per contracts/project-index.md; remote hosts list exactly what they did.

**Independent Test**: Add `~/code/a` and `~/work/b`; second `a` refused with `--name` hint; remove `b` leaves its folder; move `a`'s folder → listed missing with the fix; a projects-dir-only remote layout lists the same projects as before.

### Tests for User Story 2

- [X] T033 [P] [US2] Rendered-script tests in tests/unit/test_project_index.py for `remo-host projects add|remove|list --json`: success JSON shapes, exit 3 + exact stderr messages for invalid name, name taken, path already indexed, missing directory, removing an implicit project; `projects clone` appends a record (target `PROJECTS_DIR/<name>`, or `~/projects/<name>` created when PROJECTS_DIR is empty); `projects delete` drops the record; `capabilities` advertises projects.add/remove/list; protocol_version still 1
- [X] T034 [US2] Rendered-script test in tests/unit/test_project_index.py: a remote-style host (PROJECTS_DIR set, no index file) produces identical `sessions list --json` output before and after this change (FR-027, SC-006)
- [X] T035 [P] [US2] Tests in tests/unit/providers/test_local.py and tests/unit/cli/test_local_cmd.py for `add/remove/list`: argv sent through LocalAccess to `remo-host projects …`, table rendering (source added/folder, missing row with the re-add hint), remo-host exit 3 → PreconditionError with remo-host's message, "setup not completed" refusal

### Implementation for User Story 2

- [X] T036 [US2] Implement `projects add|remove|list` verbs (and the clone/delete/capabilities changes) in ansible/roles/user_setup/templates/remo-host.sh.j2 using remo-lib.sh, per contracts/project-index.md
- [X] T037 [US2] Show `missing` and `shadowed` entries in ansible/roles/user_setup/templates/project-menu.sh.j2 (`✗ missing` label; selecting it prints the fix instead of launching)
- [X] T038 [US2] Implement `add_project`, `remove_project`, `list_projects` in src/remo_cli/providers/local.py (via `core/remo_host_client.run_remo_host_json` with the LocalAccess prefix) and wire `remo local add PATH [--name]`, `remove NAME`, `list` in src/remo_cli/cli/local.py; resolve PATH to absolute on the workstation side
- [X] T039 [US2] Run `uv run --no-sync pytest tests/unit/test_project_index.py tests/unit/providers/test_local.py tests/unit/cli/test_local_cmd.py` — green

**Checkpoint**: US1 + US2 = MVP: install, setup, add checkouts from anywhere, `remo shell` into them.

---

## Phase 5: User Story 3 — `-p`, `--exec`, `--detach`, `remo resume` and refusals locally (Priority: P2)

**Goal**: Every day-to-day command matches the remote outcome; `cp`, `-L`, `configure` refuse clearly.

**Independent Test**: Against the local host: `-p a`, `--exec "git status"`, `--detach`, `remo resume` in a tab attached to `a` (tab key reaches the scripts via env), and the three refusals exit non-zero with the actionable messages.

### Tests for User Story 3

- [X] T040 [P] [US3] Tests in tests/unit/cli/test_shell_local.py: `remo shell local -p NAME`, `--exec CMD`, `--detach` produce LocalAccess argv running `~/.local/bin/project-launch --project …` via the detected bash; REMO_TAB_KEY in env when a tab key exists (FR-008); `--exec` exit status propagates
- [X] T041 [P] [US3] Tests in tests/unit/cli/test_resume_local.py: `remo resume` against a local host runs the lookup through the LocalAccess prefix and attaches; the 5 s budget and fallbacks behave as for SSH hosts
- [X] T042 [US3] Refusal tests: `remo cp` to/from a local host (tests/unit/cli/test_cp.py), `remo shell local -L 8080:8080` (tests/unit/cli/test_shell_local.py), `remo configure local` (tests/unit/cli/test_configure_cmd.py) — each exits 1 with the contracts/cli-local.md message and runs no subprocess; plus `remo remove local` deregisters the local entry (scripts and index untouched) instead of the provider-managed refusal (tests/unit/providers/test_added_update_remove.py)
- [X] T043 [US3] Test in tests/unit/cli/test_shell_local.py: version check against a local host reads `~/.remo-version` through LocalAccess; an older marker offers `remo local setup`, and accepting runs providers.local.setup (FR-015)

### Implementation for User Story 3

- [X] T044 [US3] Make src/remo_cli/cli/shell.py `connect_to_host` handle the local host: a missing `~/.remo-version` marker means setup never completed → `PreconditionError("remo local setup has not completed on this machine — run: remo local setup")` (unlike the silent `ssh` added-host case); `_run_tools_upgrade` → `providers.local.setup(assume_yes=True)`; surface the `-L` PreconditionError cleanly; add the no-marker case to tests/unit/cli/test_shell_local.py
- [X] T045 [US3] Refuse the local host in src/remo_cli/cli/cp.py (before `build_ssh_opts`) and in src/remo_cli/providers/added.py `configure` / src/remo_cli/core/known_hosts.py `guard_added_ssh_host_only` with the contracts/cli-local.md messages (PreconditionError); extend src/remo_cli/providers/added.py `remove` to deregister a `local` entry (confirm prompt; message says scripts and the project index stay, re-run `remo local setup` to restore)
- [X] T046 [US3] Run `uv run --no-sync pytest tests/unit/cli/test_shell_local.py tests/unit/cli/test_resume_local.py tests/unit/cli/test_cp.py tests/unit/cli/test_configure_cmd.py` — green

**Checkpoint**: US3 complete — session commands match remote outcomes locally.

---

## Phase 6: User Story 4 — Local setup is safe on your own computer (Priority: P2)

**Goal**: Prerequisites detected (never installed) with per-platform hints; setup is user-only, rc-file-free, idempotent; `remo local check` is read-only.

**Independent Test**: Temp HOME with snapshot of rc files; run setup with fzf missing → fails listing fzf + brew/apt hint, registers nothing; with fzf present → succeeds; second run changes zero files; rc files byte-identical; no `sudo`/become invoked.

### Tests for User Story 4

- [X] T047 [P] [US4] Tests in tests/unit/providers/test_local.py for `check_prerequisites()` with PATH shims: each required tool missing (bash 3.2 only, git, zellij, fzf, no devcontainer runtime, no docker) → missing with the right hint per platform (Darwin → brew; Debian/Fedora/Arch via fake /etc/os-release → apt/dnf/pacman; unknown → URL); docker present but `docker info` failing → `not_running`; gh missing → optional warning; deacon preferred over devcontainer per resolve_devcontainer_runtime precedence
- [X] T048 [P] [US4] Test in tests/unit/test_local_setup_playbook.py: run ansible/local_setup.yml twice via `run_playbook` in a temp HOME (shims for nothing needed beyond python/ansible) — first run installs scripts to $HOME/.local/bin, local.env, ~/.remo-version, state dirs; second run reports changed=0; ~/.bashrc, ~/.zshrc, ~/.profile, ~/.bash_profile byte-identical (pre-seeded); no file outside $HOME written; play has `become: false`
- [X] T049 [P] [US4] Tests in tests/unit/cli/test_local_cmd.py: `remo local setup` with a required prerequisite missing exits 1, prints the table, registers nothing and does not run the playbook; with only optional missing / engine not running it warns and succeeds; `remo local check` exits 0/1 accordingly and never writes; a second `setup` prints "Already up to date"; `--name` renames the single local entry instead of adding a second

### Implementation for User Story 4

- [X] T050 [US4] Implement `check_prerequisites()` and the `Prerequisite` result type in src/remo_cli/providers/local.py per data-model.md and research R10 (bash ≥ 4 candidate search with version probe, `shutil.which`, `docker info` with a 10 s timeout, platform install hints via `platform.system()` and /etc/os-release)
- [X] T051 [US4] Gate `providers.local.setup()` on prerequisites (fail before the playbook when a required one is missing; warn otherwise), report "Already up to date" when the playbook reports no changes and the registry entry is unchanged; implement `remo local check` in src/remo_cli/cli/local.py rendering the table from contracts/cli-local.md
- [X] T052 [US4] Run `uv run --no-sync pytest tests/unit/providers/test_local.py tests/unit/cli/test_local_cmd.py tests/unit/test_local_setup_playbook.py` — green

**Checkpoint**: All four stories complete and independently testable.

---

## Phase 7: Polish & cross-cutting

- [X] T053 [P] Add a `host-scripts-macos` job to .github/workflows/ci.yml on `macos-latest`: setup-uv, `brew install bash zellij fzf`, `uv sync --all-extras --no-extra hetzner --no-extra ec2`, run tests/unit/test_ansible_templates.py, tests/unit/test_project_index.py, tests/unit/test_session_shell.py, tests/unit/test_project_launch_tab_record.py, tests/unit/test_zellij_socket_dir.py, tests/unit/test_local_setup_playbook.py; pin actions by SHA like the existing jobs
- [X] T054 [P] Tier 2 wheel check: extend scripts/check_plugin_wheels.sh (scenario 1) to assert the built remo-cli wheel contains `remo_cli/ansible/local_setup.yml`, `roles/user_setup/tasks/host_scripts.yml`, and the remo-lib.sh / remo-session-shell.sh templates, and that `remo local --help` lists setup/check/add/remove/list
- [X] T055 [P] Write docs/local.md (install, `remo local setup`, adding existing checkouts, prerequisites + install commands per platform with the required/optional split, differences from a remote host: cp/-L/configure, web push/sync exclusion, retiring a VM: copy/clone projects, `remo local add`, `remo remove <vm>`)
- [X] T056 [P] Update README.md (local host in the provider/hosts overview and command reference), docs/web-session-interface.md (local host never pushed/synced) and ansible/README.md (local_setup.yml, tasks/host_scripts.yml, remo-lib.sh, remo-session-shell)
- [X] T057 Update CLAUDE.md and AGENTS.md: structure diagram (core/host_access.py, cli/local.py, providers/local.py, ansible/local_setup.yml, user_setup tasks/host_scripts.yml, templates remo-lib.sh.j2 and remo-session-shell.sh.j2), Commands section (`remo local …`), Quality Gates table (host-scripts-macos), and a Recent Changes entry for 029-local-host; move the displaced oldest Recent Changes entry into docs/feature-history.md; run `uv run --no-sync pytest tests/unit/test_docs_structure.py`
- [X] T058 [P] Ansible pre-commit checklist: `grep -rn '\.rc ==\|\.stdout' ansible/local_setup.yml ansible/roles/user_setup/tasks/` — every registered-var access uses `| default()` (Principle V)
- [X] T059 File the deferred follow-ups with `gh issue create` (filed: (a) #276, (b) #277, (c) #278): (a) web console (`remo web`) support for listing/attaching local-host projects; (b) project migration between hosts (local ↔ remote) building on the project index; (c) any CI-only gap found while implementing (e.g. a real macOS end-to-end run of `remo shell` against a live Zellij)
- [X] T060 Gates: `uv run --no-sync ruff check src/remo_cli`, `uv run --no-sync mypy src/remo_cli`, `uv run --no-sync pytest tests/unit/test_architecture.py tests/unit/test_architecture_provider_literals.py tests/unit/test_schema_drift.py`, then the full core suite `uv run --no-sync pytest` and both plugin suites separately (`UV_PROJECT_ENVIRONMENT=.venv-hetzner uv run --no-sync pytest plugins/ec2/tests`, then `… plugins/hetzner/tests`); record counts and any pre-existing baseline failures

---

## Dependencies & Execution Order

- Phase 1 (characterization) → Phase 2 (foundational) → user stories.
- US1 (Phase 3) depends on Phase 2. US2 (Phase 4) depends on Phase 2 (index resolver) and US1's `remo local` group (T031). US3 depends on US1. US4 depends on US1's setup (T029–T031).
- Within each story: tests before implementation; implementation tasks in listed order (same files).
- Phase 7 after the stories it documents; T060 last.

### Parallel opportunities

- Phase 1: T001 ∥ T002.
- Phase 2: T010, T012, T013 in parallel once T004 is done; T015/T016 ∥ T017 (different files) before T018/T019.
- US1: T021–T024 in parallel; US2: T033–T035; US3: T040–T043; US4: T047–T049.
- Polish: T053–T056 and T058 in parallel.

### Parallel example (US1 tests)

```text
Task: "T021 session-shell tests in tests/unit/test_session_shell.py"
Task: "T022 portability tests in tests/unit/test_ansible_templates.py"
Task: "T023 Darwin host-stats tests in tests/unit/test_host_stats_darwin.py"
Task: "T024 local shell tests in tests/unit/cli/test_shell_local.py"
```

## Implementation Strategy

- **MVP** = Phases 1–4 (US1 + US2): install, set up, add checkouts from anywhere, `remo shell` into them on a Mac with no VM.
- Then US3 (shortcuts, resume, refusals) and US4 (prerequisite UX, safety guarantees), each independently testable.
- SSH byte-identity is guarded throughout by the Phase 1 baseline; run it after every Phase 2+ change to core/ssh.py, core/resume.py or core/remo_host_client.py.
