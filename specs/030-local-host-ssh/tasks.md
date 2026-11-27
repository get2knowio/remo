# Tasks: Reach a Local Host over SSH Too

**Input**: `specs/030-local-host-ssh/` — plan.md, research.md (R1–R9), data-model.md, contracts/, quickstart.md

**Tests**: Required (Constitution VI; spec FR-023). Characterization pins come before the code they pin.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 Confirm the baseline: run `uv sync --all-extras --no-extra hetzner --no-extra ec2 && uv run --no-sync pytest -q` and record the pass/fail counts (pre-existing failures are recorded, not chased) in the implementer's report.

## Phase 2: Foundational (blocking)

- [X] T002 [P] Characterize today's probe in `tests/unit/core/test_host_probe.py`: for each host type in the `test_host_access_ssh_baseline.py` fixture, assert `check_remote_version`'s current argv/kwargs and every outcome row (timeout, OSError, rc 255, rc 0 + text, rc 1/empty) — these keep passing after T006 with only the command string updated.
- [X] T003 [P] Create `src/remo_cli/models/host_info.py`: frozen `HostInfo(host_kind: str | None = None, menu_command: bool = False, installed_by: str | None = None)`, `HostInfo.parse(text) -> HostInfo` (never raises; key=value; unknown keys/malformed lines ignored; `host_kind` only `local`/`remote`), `is_local_managed` property; tests in `tests/unit/test_host_info.py` (parse cases: empty, full, junk, unknown values, CRLF).
- [X] T004 Add `host_kind: str | None = None` to `RemoteCapability` in `src/remo_cli/models/capability.py` (`from_dict` reads it when it is a str in {local, remote}); extend the capability model tests.
- [X] T005 Host-info record: in `ansible/roles/user_setup/tasks/host_scripts.yml` add a task creating `{{ remo_scripts_home | default('/home/' ~ remo_user) }}/.local/state/remo` (0700) and rendering `host-info` (0644, owner/group omitted when `remo_local_install | default(false)`) per `contracts/host-probe.md`; `| default()` on every var. Extend `tests/unit/test_local_setup_playbook.py` (file content `host_kind=local`, run-twice still changed=0) and the remote host_scripts render test (`host_kind=remote`).
- [X] T006 Probe: in `src/remo_cli/core/ssh.py` add `PROBE_COMMAND`, `HostToolsProbe`, `probe_host_tools(host)` per `contracts/host-probe.md`; make `check_remote_version` a wrapper returning `(version, error)`. Update the three probe-string pins (`tests/unit/core/test_host_access_ssh_baseline.py:154-157`, `tests/unit/core/test_ssh.py:442`, `tests/unit/cli/test_shell_local.py:139`) and add separator-parsing cases (no separator, empty info, info without version ⇒ None) to `tests/unit/core/test_host_probe.py`.

**Checkpoint**: `uv run --no-sync pytest tests/unit/core tests/unit/test_host_info.py tests/unit/test_local_setup_playbook.py -q` green.

---

## Phase 3: User Story 1 — Menu from another machine (P1) 🎯 MVP

**Goal**: bare `remo shell` to a capable SSH host opens the menu; everything else byte-identical.

**Independent test**: baseline argv tests on both sides of the gate.

- [X] T007 [P] [US1] Tests first in `tests/unit/core/test_host_access_ssh_baseline.py`: keep `test_bare_menu`/`test_menu_with_tab_key` as the `menu_command=False` pins; add `menu_command=True` variants asserting the exact argv in `contracts/ssh-menu.md` (with/without tab key, with `-L`) for every host type; assert `-p`, `--exec`, `--detach`, tunnel and resume cases are unchanged when `menu_command=True` is passed; assert the local host ignores `menu_command`.
- [X] T008 [US1] Implement: `SSH_MENU_COMMAND` in `src/remo_cli/core/host_access.py`; `shell_connect(..., menu_command: bool = False)` in `src/remo_cli/core/ssh.py` passes `tty=True` + `SSH_MENU_COMMAND` only when no project, `access.remote`, and `menu_command`.
- [X] T009 [US1] `src/remo_cli/cli/shell.py` `connect_to_host`: call `probe_host_tools` (runs whenever `not no_update_check`, including when the local version is `unknown`; the version comparison still skipped then), keep every existing branch's behaviour, pass `menu_command=probe.info.menu_command` to `shell_connect`. Update the mocks in `tests/unit/cli/test_shell.py`, `test_shell_added.py`, `test_resume.py` that patch `check_remote_version` to patch `probe_host_tools`; add tests: probe says menu → `menu_command=True`; probe error/timeout → False; `--no-update-check` → no probe, False; unknown local version → probe runs.
- [X] T010 [P] [US1] Hook guard: add `&& [[ -z "$REMO_MENU" ]]` to the PROJECT MENU block condition in `ansible/roles/user_setup/tasks/main.yml`; add a test (new `tests/unit/test_bashrc_menu_hook.py`) that extracts the block, and runs it under bash with a stub `project-menu`: fires interactively with SSH_CONNECTION; does not fire with `REMO_MENU=1`; does not fire non-interactively.
- [X] T011 [US1] Execution test (same new file or `tests/unit/test_ansible_templates.py`): run `SSH_MENU_COMMAND` with `HOME` pointing at a temp dir containing a stub `project-menu` exiting 0 then 1 and `SHELL` a stub; assert exit 0 execs the shell with `REMO_MENU=1`, exit 1 returns non-zero without a shell.

**Checkpoint**: US1 tests green.

---

## Phase 4: User Story 2 — One session namespace (P1)

- [X] T012 [P] [US2] Tests first in `tests/unit/test_session_dir_pin.py` (executes rendered `remo-lib.sh` with a `uname` shim): Darwin + unset → `ZELLIJ_SOCKET_DIR=$HOME/.local/state/remo/zellij` created 0700; already set → untouched; existing dir with 0755 → error, exit 1; Linux → no-op, variable unset; for each of project-menu, project-launch, remo-host, devshell the helper call appears after the XDG block and before the first non-comment `zellij` line. Existing `tests/unit/test_zellij_socket_dir.py` must still pass unchanged.
- [X] T013 [US2] Implement `remo_pin_session_dir` in `ansible/roles/user_setup/templates/remo-lib.sh.j2` per research R5 and call it immediately after the `XDG_RUNTIME_DIR` block in `project-menu.sh.j2`, `project-launch.sh.j2`, `remo-host.sh.j2`, `devshell.sh.j2` (XDG blocks byte-identical).
- [X] T014 [P] [US2] FR-008a tests in `tests/unit/test_session_dir_pin.py`: with a `local.env` containing `REMO_PATH=/a:/b`, `remo_load_local_path` prepends missing dirs once (idempotent across two calls); no `local.env` → PATH unchanged.
- [X] T015 [US2] Implement `remo_load_local_path` in `remo-lib.sh.j2` and call it next to the pin in the four scripts; `providers/local.py` computes `REMO_PATH` (dirs of detected zellij, fzf, git, devcontainer runtime, container engine CLI, plus `/opt/homebrew/bin` / `/usr/local/bin` when present; de-duplicated, order kept) and passes it as `remo_local_path`; `ansible/local_setup.yml` writes `REMO_PATH={{ remo_local_path | default('') }}` into `local.env`. Update `tests/unit/test_local_setup_playbook.py` exact-content pin and `tests/unit/providers/test_local.py`.
- [X] T016 [US2] Old-session note (FR-008): in `providers/local.setup`, on Darwin only, after the playbook, run `zellij list-sessions --short` with `ZELLIJ_SOCKET_DIR` removed from env (injectable runner, 5 s timeout); if ≥1 name, print the note from research R5 once; zellij missing/erroring → silent. Tests in `tests/unit/providers/test_local.py` (sessions found, none, error, non-Darwin skipped).
- [X] T017 [US2] Add `_state_signature()` coverage for `host-info` in `providers/local.py` and a test that a changed host-info makes setup report a change.

---

## Phase 5: User Story 3 — Locally managed host says so (P2)

- [X] T018 [P] [US3] `remo-host capabilities --json` emits `host_kind` from host-info (`remo-host.sh.j2`); tests in `tests/unit/test_ansible_templates.py`: with `host_kind=local`, with `remote`, without file (key absent), and that the rest of the JSON is byte-identical to today's output.
- [X] T019 [US3] `upgrade_command_for(host, *, host_kind=None)` in `src/remo_cli/core/known_hosts.py` (returns `remo local setup (run it on that machine)` when `host_kind == "local"` and the host is not the `local` entry) and `upgrade_command_hint` in `cli/shell.py` forwards it; tests in `tests/unit/core/test_known_hosts*.py` incl. every existing caller unchanged.
- [X] T020 [US3] `connect_to_host`: when `probe.info.is_local_managed` and the host is remote, the no-marker/older branches print a warning naming that command and never call `confirm`/`_run_tools_upgrade`; tests in `tests/unit/cli/test_shell_added.py` (older, no marker, and remote newer unaffected) and `tests/unit/cli/test_resume.py` (wording via resume).
- [X] T021 [US3] `providers/added.configure`: after the guard/root/field checks and before the confirm prompt, probe via `probe_host_tools`; local-managed → `PreconditionError` per research R4; probe failure → today's flow. Tests in `tests/unit/providers/test_added*.py`: refused before any prompt/playbook; probe fails → continues; remote kind → continues.

---

## Phase 6: User Story 4 — Resume opt-in (P2)

- [X] T022 [P] [US4] Tests first in `tests/unit/providers/test_ssh_resume_optin.py` with a fake runner and temp `/etc` root (injectable paths): unsupported config → manual text, no runner calls; enable fresh + consent → install, `sshd -t`, reload (Linux) / no reload (Darwin); enable re-run → no runner calls; disable present → rm + validate; disable absent → no calls; declined → no calls; non-interactive without `--yes` → no calls + manual text; `sshd -t` fails → revert + `OperationFailedError`; `--yes` → no prompt.
- [X] T023 [US4] Implement `configure_ssh_resume(enable, *, assume_yes, runner=…, etc_root=…, interactive=…)` in `src/remo_cli/providers/local.py` per `contracts/ssh-resume-optin.md`; `setup(..., ssh_resume: bool | None = None)` calls it after registration.
- [X] T024 [US4] `src/remo_cli/cli/local.py`: `--allow-ssh-resume/--no-allow-ssh-resume` (default None) forwarded as `ssh_resume`; update `tests/unit/cli/test_local_cmd.py` `test_forwards_every_option`/`test_defaults`; assert setup without the flag never calls `configure_ssh_resume`.

---

## Phase 7: User Story 5 — Projects from either side (P3)

- [X] T025 [P] [US5] Tests first: `tests/unit/providers/test_projects.py` (generic ops over a fake SSH host: verbatim PATH, quoting, exit 3 → PreconditionError, exit 4/127 → PreconditionError naming the upgrade command, other → OperationFailedError, hints name `remo projects add HOST`) and `tests/unit/cli/test_projects_cmd.py` (three commands, host resolution, unknown host).
- [X] T026 [US5] Create `src/remo_cli/providers/projects.py` (move `_run_projects`/`add_project`/`remove_project`/`list_projects`/`render_projects` from `providers/local.py`, now taking a `KnownHost`); keep `providers/local.py` wrappers so `remo local add|remove|list` and their tests are unchanged.
- [X] T027 [US5] Create `src/remo_cli/cli/projects.py` (Click only, lazy provider import) and mount it in `src/remo_cli/cli/main.py`; add `"projects"` to `EXPECTED_COMMANDS` in `tests/unit/cli/test_main.py`.
- [X] T028 [US5] Host side: `cmd_projects_add` in `remo-host.sh.j2` expands a leading `~/` (and bare `~`) to `$HOME`; test in `tests/unit/test_project_index.py`.

---

## Phase 8: Polish & cross-cutting

- [X] T029 [P] `docs/local.md`: "Reach this machine from another one" (Remote Login, `remo add`, menu, shared sessions + the old-session note and the manual-`zellij` caveat, `--allow-ssh-resume`, `remo projects`, what `remo configure` says); README local section cross-link; `docs/` mention of `remo projects`.
- [X] T030 [P] `CLAUDE.md` and `AGENTS.md`: structure diagram entries for `cli/projects.py`, `providers/projects.py`, `models/host_info.py` (docs-structure gate), Active Technologies line, Recent Changes entry for 030 — move the displaced oldest entry to `docs/feature-history.md`.
- [X] T031 [P] `.github/workflows/ci.yml` `host-scripts-macos`: add `tests/unit/test_session_dir_pin.py`, `tests/unit/test_host_info.py`, `tests/unit/test_bashrc_menu_hook.py`.
- [X] T032 [P] Tier 2: confirm `scripts/check_plugin_wheels.sh` asserts the templates still ship (no new template file is added; extend only if one is).
- [X] T033 Comment on #276 (web console: `host_kind` is now in capabilities but not in `CapabilityOut`) and on #278 (live macOS e2e should cover desk↔SSH session sharing) with `gh issue comment`; record the URLs.
- [X] T034 Gates: `uv run ruff check src/remo_cli`, `uv run mypy src/remo_cli`, `uv run --no-sync pytest tests/unit/test_architecture.py tests/unit/test_docs_structure.py -q`, then the full core suite; Ansible grep checklist (`.rc ==`, `.stdout`) clean.

## Dependencies

Phase 2 blocks all stories. US1 needs T003/T006. US2 independent of US1. US3 needs T004–T006. US4 independent. US5 independent. Phase 8 last.

## Parallel examples

T002, T003 together; T007, T010, T012, T022, T025 (tests-first files) together once Phase 2 is done.

## Implementation strategy

MVP = Phases 1–4 (menu + shared sessions); then US3, US4, US5; polish.
