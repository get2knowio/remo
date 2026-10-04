---
description: "Task list for 028-tab-resume"
---

# Tasks: `remo resume` — Put Each Terminal Tab Back Where It Was

**Input**: Design documents from `specs/028-tab-resume/` — plan.md, spec.md,
research.md (R1–R13), data-model.md (decision table), contracts/
(cli-resume.md, remo-host-sessions-lookup.md, ssh-env-forwarding.md),
quickstart.md.

**Tests**: Required — Constitution Principle VI (every error/skip/fail path
covered; refactors pinned by characterization tests first).

**Organization**: by user story (spec.md). Test commands: targeted
`uv run pytest <paths>`; full gate `uv run pytest`; lint/types
`uv run ruff check src/remo_cli && uv run mypy src/remo_cli`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4 from spec.md

---

## Phase 1: Setup

- [X] T001 (baseline: 3056 passed, 2 failed, 21 skipped — the 2 failures were test_docs_structure tripping on the not-yet-documented new modules, transient; clean baseline 3058/21) Confirm baseline: run `uv run pytest -q` on branch `028-tab-resume` and record the pass/skip counts (expected ≈3058 passed / 21 skipped) at the top of the implementation report; no file changes.

---

## Phase 2: Foundational (blocks every story)

**Purpose**: shared client plumbing — the `remo shell` refactor, tab identity, the workstation store, key forwarding.

### Characterize, then refactor `remo shell` (Principle VI: pin first)

- [X] T002 Add characterization tests to tests/unit/cli/test_shell.py pinning the current `shell` flow end to end with mocks: host resolution → `auto_start_aws_if_stopped` → version check/upgrade offer (`--no-update-check` skips it) → `shell_connect` called with exactly `(host, tunnels, no_open, project=, detach=, exec_cmd=)`; include the no-`-p`, `-p`, `-p --exec --detach`, and `-L` cases. Must pass against the UNCHANGED code.
- [X] T003 Refactor src/remo_cli/cli/shell.py: extract everything after `resolve_remo_host(name)` into a public `connect_to_host(host, *, tunnels, no_open, no_update_check, project=None, exec_cmd=None, detach=False, tab_key=None) -> None` (tab_key passed through to `shell_connect`, unused by callers yet); rename `_upgrade_command_hint` → public `upgrade_command_hint` (update every internal reference and tests in tests/unit/cli/test_shell.py / test_shell_added.py). T002's tests must stay green unchanged except for the renamed symbol.

### Tab identity (research R3/R4, data-model TabIdentity/TabKey)

- [X] T004 [P] Write tests/unit/core/test_tab_identity.py: precedence order (`TMUX_PANE` > `WEZTERM_PANE` > `KITTY_WINDOW_ID` > `ITERM_SESSION_ID` > `TERM_SESSION_ID` > `WT_SESSION`); empty values count as unset; none set → `None`; tmux identity uses the first comma-separated field of `TMUX` plus `TMUX_PANE` and ignores the pid field; `TMUX_PANE` without `TMUX` still yields an identity; same value under different variable names → different identity; `derive_tab_key` returns `^[0-9a-f]{32}$`, is deterministic for (identity, secret), and differs for a different secret.
- [X] T005 [P] Implement src/remo_cli/core/tab_identity.py: frozen dataclass `TabIdentity(variable: str, value: str)` with property `identity -> str` (`f"{variable}={value}"`); `TAB_VARIABLES` tuple in precedence order; `detect_tab_identity(env: Mapping[str, str]) -> TabIdentity | None`; `derive_tab_key(identity: TabIdentity, secret: bytes) -> str` (HMAC-SHA256 hexdigest[:32]); `TAB_KEY_RE = re.compile(r"^[0-9a-f]{32}$")`. Stdlib only, `from __future__ import annotations`, no I/O.

### Workstation store (research R5, data-model WorkstationSecret/WorkstationTabRecord)

- [X] T006 [P] Write tests/unit/core/test_tab_records.py using a tmp `REMO_HOME` (monkeypatch env): secret created on first use with mode 0600 and 64 hex chars, reused thereafter; `record(key, host, project)` then `load(key)` round-trips (`recorded_at` UTC ISO); re-record overwrites; `forget(key)` removes one, returns whether it existed; `forget_all()` empties records AND regenerates the secret; prune drops >30-day entries and keeps the newest 500; missing / non-JSON / wrong `version` / wrong-shape file reads as empty and is rewritten cleanly on next record; a write into an unwritable REMO_HOME raises `TabRecordError` (not OSError); concurrent writers (≥8 `multiprocessing` processes × 20 records) leave valid JSON containing every last write per key; lock timeout raises `TabRecordError`.
- [X] T007 [P] Implement src/remo_cli/core/tab_records.py: `TabRecord` frozen dataclass (`host: str`, `project: str | None`, `recorded_at: datetime`); `TabRecordError(Exception)`; `get_secret() -> bytes`; `load(key) -> TabRecord | None`; `record(key, host, project) -> None`; `forget(key) -> bool`; `forget_all() -> None`; constants `RETENTION_DAYS = 30`, `MAX_RECORDS = 500`. Paths via `core/config.get_remo_home()` (`tab-records.json`, `tab-secret`, `tab-records.lock`). Private `_atomic_write_text` (tempfile in same dir + `os.replace`, `chmod 0600` for the secret) and a private flock context manager modelled on `core/registry.py:715-756` (5 s bounded wait, degrade on ENOLCK/EOPNOTSUPP). No Click, no provider knowledge.

### Key forwarding (contracts/ssh-env-forwarding.md)

- [X] T008 [P] Add tests to tests/unit/core/test_ssh.py: `shell_connect(..., tab_key="0"*32)` runs ssh with `-o SendEnv=REMO_TAB_KEY` in argv and `REMO_TAB_KEY` in the `env=` passed to `subprocess.run`, without mutating `os.environ`; `tab_key=None` produces argv and env identical to today (assert no `REMO_TAB_KEY` anywhere); works together with `-p` and `-L`.
- [X] T009 Implement in src/remo_cli/core/ssh.py: `shell_connect` gains `tab_key: str | None = None`; when set, append `["-o", "SendEnv=REMO_TAB_KEY"]` to the `extra_opts` passed to `build_ssh_base_cmd` and call `subprocess.run(ssh_cmd, env={**os.environ, "REMO_TAB_KEY": tab_key})`; when `None`, call exactly as today. (Depends on T008.)

**Checkpoint**: `uv run pytest tests/unit/cli/test_shell.py tests/unit/cli/test_shell_added.py tests/unit/core/test_tab_identity.py tests/unit/core/test_tab_records.py tests/unit/core/test_ssh.py` green.

---

## Phase 3: User Story 1 — Each tab returns to its exact session (P1) 🎯 MVP

**Goal**: `remo resume` in a tab reattaches that tab's live project session with zero prompts on an upgraded host.

**Independent Test**: quickstart.md steps 1–4.

### Host side (contracts/remo-host-sessions-lookup.md, research R6–R8)

- [X] T010 [P] [US1] Write tests/ansible/test_sshd_accept_env.py: parse ansible/tasks/configure_dev_tools.yml, find the task writing `/etc/ssh/sshd_config.d/accept-tz.conf`; assert its content has an `AcceptEnv` line containing both `TZ` and `REMO_TAB_KEY`, it still `register`s `sshd_tz_config`, and the restart task is still gated on `sshd_tz_config.changed | default(false)`; and (FR-020) the drop-in is written by `ansible.builtin.copy` (idempotent: a second run reports `changed=false` and triggers no restart).
- [X] T011 [US1] Update ansible/tasks/configure_dev_tools.yml: drop-in content becomes `# Allow clients to propagate their timezone and remo's per-tab resume key\nAcceptEnv TZ REMO_TAB_KEY\n`; task name updated to mention the resume key; file path unchanged.
- [X] T012 [P] [US1] Add execution tests to tests/unit/test_ansible_templates.py (reuse `rendered_script`, `_run_remo_host`, `_write_shim`; set `REMO_HOST_TABS_DIR` to a tmp dir; fake `zellij` printing chosen `list-sessions` output): `sessions record --key <32hex> --project <existing dir>` writes `<dir>/<key>` = `name\t<epoch>` and exits 0; invalid keys (`../x`, uppercase hex, 31 chars, 33 chars, empty, missing flag) exit 3 or 2 and write nothing anywhere; non-existent / traversal project exits 3; prune deletes a file with mtime 31 days ago and keeps only the newest 500 of 502; `sessions lookup --key K --json` returns `project:null, recorded_at:null, zellij_state:null` with no record, and `active` / `exited` / `absent` per the fake zellij output with a record; lookup without `--json` exits 2; invalid key exits 3; `capabilities --json` `operations` contains `sessions.lookup` and does not contain `sessions.record`; `sessions` usage line mentions `lookup`; `--help` still prints the full header (adjust the `sed -n` range if the header grows); 10 parallel `sessions record` invocations for distinct keys (FR-017) all succeed, leave 10 well-formed files and no leftover temp files.
- [X] T013 [US1] Implement in ansible/roles/user_setup/templates/remo-host.sh.j2: `TABS_DIR="${REMO_HOST_TABS_DIR:-$HOME/.local/state/remo/tabs}"` beside `JOBS_DIR`; `validate_tab_key` (`^[0-9a-f]{32}$`, exit 3 on failure); factor the per-project zellij-state logic out of `cmd_sessions_list` into `zellij_state_for NAME` (active/exited/absent; same ANSI-strip + EXITED rule) and use it in both; `cmd_sessions_record` (flags `--key`, `--project`; `validate_project_name`; `mkdir -p -m 0700`; `mktemp` in TABS_DIR + `mv`; `find "$TABS_DIR" -type f -mtime +30 -delete`; keep newest 500 via `ls -t | tail -n +501`); `cmd_sessions_lookup` (flags `--key`, `--json`; prints the single-line JSON per the contract using `json_escape`); dispatch arms `record` and `lookup` under `sessions`; add `"sessions.lookup"` to the `operations` list in `cmd_capabilities`; update the header usage comment, the `sessions` usage string and the `--help` `sed` range. Keep `set -e` safety (`|| true` where a failure must not abort) and avoid the Jinja2 brace-hash trap (no `${#...}`).
- [X] T014 [P] [US1] Write tests/unit/test_project_launch_tab_record.py: render ansible/roles/user_setup/templates/project-launch.sh.j2 (same Jinja env/vars as tests/unit/test_ansible_templates.py), run it with a fake `zellij` (records argv, exits 0) and a fake `$HOME/.local/bin/remo-host` (records argv) in a tmp HOME with a project dir: with `REMO_TAB_KEY=<32hex>` → remo-host called `sessions record --key <key> --project <name>` BEFORE zellij `attach --create <name>`; without `REMO_TAB_KEY` → remo-host never called; `--detach --exec cmd` → never called; a failing fake remo-host (exit 1) still lets zellij attach run. Static test for project-menu.sh.j2: inside `launch_session`, a guarded (`|| true`, `-n "${REMO_TAB_KEY:-}"`) `sessions record` call appears before `zellij attach --create "$project_name"`.
- [X] T015 [US1] Implement recording in ansible/roles/user_setup/templates/project-launch.sh.j2 (just before the final `exec zellij attach --create "$PROJECT"`, not in the detach or non-zellij branches) and ansible/roles/user_setup/templates/project-menu.sh.j2 (`launch_session`, after `cd "$project_dir"`, before `zellij attach --create`): `if [ -n "${REMO_TAB_KEY:-}" ]; then "$HOME/.local/bin/remo-host" sessions record --key "$REMO_TAB_KEY" --project "<name>" >/dev/null 2>&1 || true; fi`, with a short comment citing spec 028.

### Client lookup (data-model ResumeLookup)

- [X] T016 [P] [US1] Add tests to tests/unit/core/test_remo_host_client.py: `build_remo_host_argv("sessions lookup", key=K, json=True)` → `["remo-host","sessions","lookup","--key",K,"--json"]`; `lookup_tab(prefix, K, timeout=5.0)` parses a hit into `TabLookup(project, recorded_at, zellij_state)` and a miss into `TabLookup(None, None, None)`; malformed JSON → `MalformedResponseError`; exit 4 and exit 2 → `TabLookupUnsupported`; timeout → `SshTransportError`; an invalid key passed by a caller raises `ValueError` before any subprocess runs.
- [X] T017 [US1] Implement in src/remo_cli/core/remo_host_client.py: `TabLookup` frozen dataclass; `TabLookupUnsupported(RemoHostClientError)`; a `key` parameter and a `sessions lookup` branch in `build_remo_host_argv`; `lookup_tab(ssh_argv_prefix, key, *, timeout=5.0) -> TabLookup` via `run_remo_host_json`, mapping `RemoHostCommandError` with returncode 4 or 2 to `TabLookupUnsupported`; validate the key with `TAB_KEY_RE` from core/tab_identity.py.

### `remo shell` records, `remo resume` attaches

- [X] T018 [P] [US1] Add tests to tests/unit/cli/test_shell.py: with a tab variable set and a tmp REMO_HOME, `remo shell H` records `{host: H, project: None}` and passes the derived `tab_key` to `shell_connect`; `remo shell -p A H` records project `A`; `--exec X --detach` records nothing and passes `tab_key=None`; no tab variable → nothing recorded, `tab_key=None`; a `TabRecordError` from the store prints one warning line and the connection still happens with the key forwarded; and (FR-012a) `remo shell` never calls `run_lookup`, `decide_resume` or `is_project_live` — with a live record present it still connects exactly as before apart from the forwarded key.
- [X] T019 [US1] Implement in src/remo_cli/cli/shell.py: after host resolution, derive identity/key (`detect_tab_identity(os.environ)`, `get_secret()`, `derive_tab_key`), unless `--detach`; best-effort `tab_records.record(key, host.name, project)` (catch `TabRecordError` → `core/output` warn line); pass `tab_key` into `connect_to_host`.
- [X] T020 [P] [US1] Write tests/unit/core/test_resume.py (US1 rows): `decide_resume(...)` table rows 5 (lookup entry active → attach entry.project, reason None) and 7 (lookup unsupported, record.project active per sessions list → attach record.project); host lookup entry overrides the workstation project when both exist; the success message is `Resuming <project> on <host>`; and (SC-006/FR-015) `run_lookup` calls `lookup_tab` with `timeout=5.0` and an ssh prefix containing `-o BatchMode=yes` (mock `lookup_tab` and `build_ssh_base_cmd`).
- [X] T021 [US1] Implement src/remo_cli/core/resume.py: `ResumeReason` enum (NO_IDENTITY, NO_RECORD, NAME_MISMATCH, HOST_GONE, HOST_NOT_UPGRADED, SESSION_NOT_LIVE, LOOKUP_FAILED); `ResumeDecision` frozen dataclass (`action: Literal["attach","menu","shell"]`, `host_name`, `project`, `reason`); pure `decide_resume(*, identity_present, record, requested_name, host_registered, lookup: TabLookup | None, lookup_status: Literal["ok","unsupported","failed","skipped"], record_project_live: bool | None) -> ResumeDecision` implementing EVERY row of data-model.md's decision table in order; `resume_message(decision, *, upgrade_hint: str | None) -> str` with the exact wording from contracts/cli-resume.md; `run_lookup(host, key) -> tuple[TabLookup | None, status]` (prefix = `build_ssh_base_cmd(host, multiplex=True)` + `-o BatchMode=yes`, `lookup_tab(..., timeout=5.0)`, all `RemoHostClientError` → "failed", `TabLookupUnsupported` → "unsupported"); `is_project_live(host, project) -> bool` via `list_sessions` (any error → False). No Click, no provider literals.
- [X] T022 [P] [US1] Write tests/unit/cli/test_resume.py (US1): with a record for registered host H and mocked `run_lookup` → active entry, `remo resume` prints `Resuming A on H` and calls `connect_to_host(H, project="A", tab_key=<key>, …)` with no picker; `-L 8080:80` and `--no-open` / `--no-update-check` pass through; and (SC-004) on this fast path `is_project_live` is NOT called — the lookup is the only extra round-trip.
- [X] T023 [US1] Implement src/remo_cli/cli/resume.py: `@click.command("resume")` with `NAME` (optional), `-L/--tunnel` (multiple), `--no-open`, `--no-update-check`, `--forget`, `--forget-all`; gathers identity/record/host-registered (exact-name scan of `get_known_hosts()` — never `resolve_remo_host_by_name` on a possibly-missing name), runs `run_lookup` / `is_project_live` only when the decision needs them, prints `resume_message`, then dispatches: attach → `connect_to_host(host, project=…, tab_key=key)`; menu → `connect_to_host(host, tab_key=key)`; shell → the same path as `remo shell [NAME]` (`resolve_remo_host(name)` then `connect_to_host(..., tab_key=key if identity else None)`). `upgrade_hint` from `cli/shell.upgrade_command_hint(host)`.
- [X] T024 [US1] Register the command in src/remo_cli/cli/main.py `_register_commands()` (lazy import, `cli.add_command(resume)`) and add `"resume"` to `EXPECTED_COMMANDS` in tests/unit/cli/test_main.py.

**Checkpoint**: `uv run pytest tests/unit/core/test_resume.py tests/unit/cli/test_resume.py tests/unit/cli/test_shell.py tests/unit/core/test_remo_host_client.py tests/unit/test_ansible_templates.py tests/unit/test_project_launch_tab_record.py tests/ansible/test_sshd_accept_env.py tests/unit/cli/test_main.py` green.

---

## Phase 4: User Story 2 — Never worse than `remo shell` (P1)

**Goal**: every missing piece falls back to `remo shell` behaviour with one explanatory line; never a new session, never another tab's project.

**Independent Test**: quickstart.md step 5.

- [X] T025 [P] [US2] Extend tests/unit/core/test_resume.py with every fallback row of the decision table: 1 NO_IDENTITY → shell; 2 NO_RECORD → shell; 3 NAME_MISMATCH → shell(NAME); 4 HOST_GONE → shell; 6 lookup entry not active → menu SESSION_NOT_LIVE; 8 record.project not active → menu SESSION_NOT_LIVE; 9 unsupported + no record.project → menu HOST_NOT_UPGRADED (message contains the passed upgrade hint verbatim); 10 failed + no record.project → menu LOOKUP_FAILED; 11 supported, no entry, no record.project → menu NO_RECORD; `is_project_live` error → treated as not live (row 8). Assert each `resume_message` matches contracts/cli-resume.md exactly. Assert no row ever yields `attach` for a non-active project.
- [X] T026 [P] [US2] Extend tests/unit/cli/test_resume.py for each fallback through the CLI: no tab variable → message + `resolve_remo_host(None)` path (picker when several hosts); no record → same; record host removed from the registry → message, no `SystemExit` from name resolution; `remo resume OTHER` with a record for H → `remo shell OTHER` path; lookup unsupported for an added `ssh` host → message names `remo configure H`; for a provider host → `remo <type> upgrade …` (from `upgrade_command_hint`); lookup timeout (`run_lookup` returns failed) → menu with LOOKUP_FAILED; session not live → `connect_to_host` called WITHOUT `project`; every path prints exactly one line before connecting.
- [X] T027 [US2] Make T025/T026 pass by completing src/remo_cli/core/resume.py and src/remo_cli/cli/resume.py (fallback dispatch, message emission via `core/output`, `tab_key` still forwarded on menu/shell paths so the host records the user's next pick).
- [X] T028 [P] [US2] Add tests to tests/unit/cli/test_resume.py for `--forget` (removes only this tab's record, prints one line, exit 0, no connection, no-op line when nothing recorded / no identity) and `--forget-all` (clears all, rotates the secret, exit 0, no connection); `--forget --forget-all`, `--forget NAME`, and `--forget -L x` are usage errors (exit 2).
- [X] T029 [US2] Implement `--forget` / `--forget-all` in src/remo_cli/cli/resume.py on top of `tab_records.forget` / `forget_all` (mutual-exclusion via Click usage errors; `TabRecordError` → one error line, exit 1).

**Checkpoint**: `uv run pytest tests/unit/core/test_resume.py tests/unit/cli/test_resume.py` green.

---

## Phase 5: User Story 3 — Right host even before the host is upgraded (P2)

**Goal**: with only the workstation upgraded, resume skips the host picker; `-p` launches reattach via the workstation's remembered project.

**Independent Test**: quickstart.md step 5, last bullet, plus a `remo shell -p A` tab against an old host.

- [X] T030 [P] [US3] Add tests to tests/unit/cli/test_resume.py: several registered hosts, record for H, lookup unsupported, no record.project → `connect_to_host(H, …)` called directly and the picker (`pick_environment`) is never invoked; record.project `A` with lookup unsupported and `list_sessions` reporting A active → attach A; reporting A exited → menu (never `project="A"`); `remo resume H` with record for H behaves as plain `remo resume`.
- [X] T031 [US3] Wire `is_project_live` into src/remo_cli/cli/resume.py for decision rows 7–8 (only called when lookup status is not "ok-with-entry" and the record has a project), ensuring no extra round-trip on the row-5 fast path (SC-004).

---

## Phase 6: User Story 4 — Documented and discoverable (P3)

- [X] T032 [P] [US4] README.md: add `### Resume After a Dropped Connection` under `## The Dev Workflow` (after `### Jump Straight to a Project`): what is remembered and where (`<REMO_HOME>/tab-records.json`, `tab-secret`; hosts keep only salted digests under `~/.local/state/remo/tabs`), the recognised terminals and precedence, the fallback lines, the host-upgrade requirement for exact resume (`remo configure NAME` / `remo <provider> upgrade NAME`), `--forget` / `--forget-all`; add `remo resume` lines to the `## CLI Reference` block.
- [X] T033 [P] [US4] specs/010-web-session-interface/contracts/remo-host-protocol.md: document `sessions.lookup` (operation, request, response JSON, exit codes, old-host exit 4) and the internal `sessions record`, cross-referencing specs/028-tab-resume/contracts/remo-host-sessions-lookup.md; docs/web-session-interface.md: add `sessions.lookup` to the operations summary.
- [X] T034 [P] [US4] Write the `remo resume --help` docstring in src/remo_cli/cli/resume.py naming the recognised terminals, the fallback behaviour and the host-upgrade requirement; add a test in tests/unit/cli/test_resume.py asserting the help text mentions `TERM_SESSION_ID`, `remo shell`, and `upgrade`.
- [X] T035 [US4] CLAUDE.md and AGENTS.md: add structure-diagram entries for src/remo_cli/cli/resume.py, src/remo_cli/core/tab_identity.py, src/remo_cli/core/tab_records.py, src/remo_cli/core/resume.py (and amend the shell.py / ssh.py / remo_host_client.py / remo-host.sh.j2 lines); add an Active Technologies line and a `028-tab-resume` Recent Changes entry, moving the displaced oldest Recent Changes entry to docs/feature-history.md (keep exactly three in CLAUDE.md, per its own note). Run `uv run pytest tests/unit/test_docs_structure.py`.
- [X] T036 [P] [US4] tests/integration/fish_completion.sh: add `resume` to the expected top-level command names checked at lines ~156-157.

---

## Phase 7: Polish & Cross-Cutting

- [X] T037 (filed as #243) File follow-up issue: `gh issue create --title "Consolidate the three upgrade-command hint implementations" --body "<cli/shell.py upgrade_command_hint (handles host-scoped --host/--host-user), web/discovery.py configure_remediation, core/known_hosts.py:276 inline string — move one descriptor-driven helper into core/ and use it everywhere. Found while building spec 028.>"`; record the issue number in this file.
- [X] T038 (filed as #244) File follow-up issue: `gh issue create --title "Shared atomic JSON write + lock helper" --body "<core/registry.py, core/web_adopt.py, web/trust_store.py, web/mirror_meta.py, web/jobs.py and now core/tab_records.py each carry a private tmp+os.replace (and some an flock) copy — factor one public helper in core/. Found while building spec 028.>"`; record the issue number in this file.
- [X] T039 Run the Ansible pre-commit checks from CLAUDE.md: `grep -rn '\.rc ==' ansible/` and `grep -rn '\.stdout' ansible/` — every match in files touched by this feature uses `| default()`; confirm no `${#` in any touched .j2 / ansible YAML (tests/unit/test_devcontainer_stop_cleanup.py guard passes).
- [X] T040 Run `uv run ruff check src/remo_cli && uv run mypy src/remo_cli` and fix every finding in touched files.
- [X] T041 (3219 passed, 21 skipped, 0 failed; fish not installed so the fish script was not run) Run the full gate `uv run pytest` (foreground) and `./tests/integration/fish_completion.sh` if fish is installed; record pass/skip counts vs the T001 baseline; any new failure is fixed, any pre-existing one is listed.
- [X] T042 (automated section green; manual end-to-end remains a pre-merge gate) Walk quickstart.md's automated section and confirm each command passes; note that the manual end-to-end section requires a configured host and is a pre-merge manual gate.

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 → Phase 3 (US1) → Phase 4 (US2) → Phase 5 (US3) → Phase 6 (US4) → Phase 7.
- Within Phase 2: T002 → T003; T004 ∥ T006 ∥ T008 (tests) then T005, T007, T009; T003 before T019.
- Within US1: host side (T010–T015) ∥ client lookup (T016–T017) ∥ shell recording (T018–T019); T021 needs T017 + T007; T023 needs T021 + T003; T024 after T023.
- US2 and US3 extend `core/resume.py` / `cli/resume.py` — sequential after US1.
- US4 docs can start once the CLI surface (T023, T029) is fixed.

## Parallel Examples

- Phase 2 tests: T004, T006, T008 together (three different new/extended test files).
- US1: T010 + T012 + T014 (host tests) ∥ T016 (client test) ∥ T018 (shell test) ∥ T020 + T022.
- US4: T032, T033, T034, T036 together.

## Implementation Strategy

MVP = Phases 1–3 (US1): exact resume on an upgraded host. US2 hardens every
fallback (required before release — a resume that can land in the wrong place
must not ship). US3 adds the workstation-project path for old hosts. US4 docs
ship in the same change (Principle VIII).
