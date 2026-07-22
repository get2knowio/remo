---
description: "Task list for feature 014-register-ssh-host"
---

# Tasks: Register an SSH-Reachable Host (`remo add`)

**Input**: Design documents from `/specs/014-register-ssh-host/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: INCLUDED. The feature is branch-heavy and Constitution Principle II
(Test All Conditional Paths) applies; each conditional branch gets a test, and
each user story owns its own test file(s) so stories stay independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 / US2 / US3 (Setup, Foundational, Polish carry no story label)
- Paths are repo-relative; single-project layout per plan.md.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Fixed constants used across every layer.

- [X] T001 [P] Add `ADDED_HOST_TYPE = "ssh"`, `DEFAULT_ADDED_HOST_USER = "remo"`, and `DEFAULT_SSH_PORT = 22` as fixed module constants in `src/remo_cli/core/config.py` (single definition site, alongside the 013 marker constants)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The registry model + SSH-argv substrate that both the connect path
(US1) and the verify path (US3) depend on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 [P] Write serialization/property tests in `tests/unit/test_host_ssh_type.py`: `ssh`-type 4/6/7-field `to_line`/`from_line` round-trip; `ssh_port` returns 22 by default and the parsed port when set; `ssh_identity` returns the identity or `None`; non-`ssh` types return neutral values (22 / `None`); pre-existing provider lines still parse (SC-007)
- [X] T003 Add type-gated `ssh_port -> int` and `ssh_identity -> str | None` properties to `src/remo_cli/models/host.py` (per data-model.md; makes T002 pass — no change to `to_line`/`from_line`)
- [X] T004 [P] Write SSH-builder tests in `tests/unit/core/test_ssh_added.py`: for `type=="ssh"`, `build_ssh_opts` emits `-o Port=<n>` only when port ≠ 22, uses the stored identity, and an explicit `identity_file=` argument still wins; assert incus/proxmox/aws/hetzner argv is byte-identical to before (proxmox numeric vmid in `instance_id` must NOT become a port). NOTE: `build_ssh_opts` is the exact builder `remo cp` uses (`cli/cp.py`), so these opts also cover file transfer to an added host (FR-005) — no separate cp builder exists.
- [X] T005 Wire type-gated `-o Port=` + stored-identity handling into `build_ssh_opts` in `src/remo_cli/core/ssh.py` (gated on `host.type == "ssh"`, using `ssh_port`/`ssh_identity`; explicit `identity_file` param retains precedence — research D3; makes T004 pass)

**Checkpoint**: Registry model and connection substrate ready.

---

## Phase 3: User Story 1 - Register a host I can already SSH to (Priority: P1) 🎯 MVP

**Goal**: `remo add <name> <target>` registers an SSH-reachable host; `remo shell <name>` and `remo cp` then work via the existing direct path — including a plain login shell when `remo-host` is absent.

**Independent Test**: On a box you can SSH to (no hypervisor access), run `remo add mybox user@host` then `remo shell mybox` and land in a shell (quickstart Scenarios 1–3, 7).

### Tests for User Story 1

- [X] T006 [P] [US1] Target-parse + add provider tests in `tests/unit/providers/test_added_add.py`: parse `[user@]host[:port]`; default user applied & reported; `--user`/`--port` override; un-bracketed IPv6 and bracketed `[::1]:22` rejected with no write (D4); identity path containing `:` rejected (D5); create writes a single `ssh:` entry; a name already held by a provider entry is refused with no write (FR-010/SC-005); and, after add, assert the host resolves via `resolve_remo_host_by_name(name)` and is included in `get_known_hosts()` (the picker source) so it is selectable alongside provider hosts (FR-006/US1 scenario 5)
- [X] T007 [P] [US1] CLI add tests in `tests/unit/cli/test_add_cmd.py` (Click `CliRunner`): argument/flag wiring, success message names `remo shell <name>` + effective user, non-zero exit on collision and on malformed target
- [X] T008 [P] [US1] Shell-degradation test in `tests/unit/cli/test_shell_added.py`: a `type="ssh"` host skips the pre-connect tools/version check (no "has no version info / Update tools?" prompt) and proceeds to `shell_connect` (FR-011/SC-006)

### Implementation for User Story 1

- [X] T009 [US1] Create `src/remo_cli/providers/added.py` (no Click imports): `parse_ssh_target(target, user_override, port_override)` (D4 colon-count IPv6 rejection), and `add(...)` performing name validation (`validate_name`), port validation (`validate_port`), identity `:` rejection (D5), whole-registry collision scan refusing provider-managed names (FR-010, D6), effective-user resolution/report (FR-003), and `save_known_host(KnownHost(type="ssh", access_mode="direct", instance_id=port, region=identity))`
- [X] T010 [US1] Create `src/remo_cli/cli/added.py`: `add` Click command with `NAME` + `TARGET` args and `--user/--port/--identity/--yes` options, delegating to `providers.added.add` (the `--verify` option is added in US3/T020 so the US1 increment never ships a non-functional flag — F1)
- [X] T011 [US1] Register the `add` command in `_register_commands()` in `src/remo_cli/cli/main.py`
- [X] T012 [US1] Gate the pre-connect tools/version check on `host.type != "ssh"` in `src/remo_cli/cli/shell.py` so added hosts skip it and drop into a plain login shell (FR-011)

**Checkpoint**: MVP — add a host and `remo shell`/`remo cp` into it; unmanaged hosts land in a plain shell.

---

## Phase 4: User Story 2 - Update or remove a manually-added host (Priority: P2)

**Goal**: Re-running `add` with the same name updates in place (no duplicate); `remo remove <name>` deregisters an added host locally, refusing provider-managed names.

**Independent Test**: Add a host, re-`add` with a changed target (one line, updated), then `remo remove` it and confirm it's gone from the registry and picker (quickstart Scenarios 4–5).

### Tests for User Story 2

- [X] T013 [P] [US2] Update/remove provider tests in `tests/unit/providers/test_added_update_remove.py`: re-adding an existing `ssh` name with a changed target updates in place with exactly one line (FR-007/SC-003, `--yes` bypasses confirm); `remove()` deletes an `ssh` entry making **no** network call (SC-004); `remove()` refuses a provider-managed name (FR-009); removing an absent name is a clear no-op/not-found
- [X] T014 [P] [US2] CLI remove tests in `tests/unit/cli/test_remove_cmd.py` (`CliRunner`): remove success with confirmation and with `--yes`; refusal (non-zero) on a provider host; not-found (non-zero)

### Implementation for User Story 2

- [X] T015 [US2] Extend `add()` in `src/remo_cli/providers/added.py` with the same-name `ssh` in-place-update branch (confirm via `core.output.confirm` unless `--yes`; relies on `save_known_host` `(type,name)` replacement — FR-007/D6)
- [X] T016 [US2] Add `remove(name, assume_yes)` to `src/remo_cli/providers/added.py`: resolve by name; refuse when the resolved `type != "ssh"` with a message distinguishing deregister from provider `destroy` (FR-009/D8); otherwise confirm (unless `--yes`) and `remove_known_host("ssh", name)` — no SSH/network call
- [X] T017 [US2] Add the `remove` Click command (`NAME` arg, `--yes`) to `src/remo_cli/cli/added.py`, delegating to `providers.added.remove`
- [X] T018 [US2] Register the `remove` command in `_register_commands()` in `src/remo_cli/cli/main.py`

**Checkpoint**: Update-in-place and deregister both work; US1 still passes.

---

## Phase 5: User Story 3 - Verify reachability at add time (Priority: P3)

**Goal**: `remo add --verify` performs a fail-closed SSH connectivity check before registering.

**Independent Test**: `remo add --verify` against an unreachable target surfaces the SSH error, writes no entry, and exits non-zero; without `--verify`, no network call is made (quickstart Scenario 9).

### Tests for User Story 3

- [X] T019 [P] [US3] Verify tests in `tests/unit/providers/test_added_verify.py` (SSH subprocess mocked): reachable target → registers after a successful probe; unreachable/auth-fail → SSH error surfaced, **no** registry write, non-zero exit (FR-014/US3.2, fail-closed); `--verify` absent → zero network round-trips (FR-014/US3.3)

### Implementation for User Story 3

- [X] T020 [US3] Add the `--verify` option to the `add` command in `src/remo_cli/cli/added.py`, and add `verify_reachable(host)` to `src/remo_cli/providers/added.py` — a lightweight `ssh -o BatchMode=yes -o ConnectTimeout=… true`-style probe built through `build_ssh_opts` (so port/identity apply) — wiring `--verify` into `add()` to run it BEFORE `save_known_host`, declining to register (no write) and returning non-zero on failure

**Checkpoint**: All three stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T021 [P] FR-012 guard: audit provider `destroy`/`snapshot`/resize resolution paths in `src/remo_cli/providers/*.py`; where a host is resolved by name, ensure a `type="ssh"` host yields a clear "manually-registered SSH host with no managed infrastructure" error rather than an opaque failure, with a regression test in `tests/unit/providers/test_added_provider_guard.py`
- [X] T022 [P] Update `README.md` with a `remo add` / `remo remove` section: target syntax `[user@]host[:port]`, `--user/--port/--identity/--verify/--yes`, IPv6 guidance, and the local-only nature of `remove` (Constitution V)
- [X] T023 [P] Run `uv run mypy src/remo_cli` and `uv run ruff check src/remo_cli`; resolve findings in the new/changed files
- [X] T024 Run the quickstart validation (`uv run pytest` on the new suites + a spot-check of the `remo add`/`shell`/`remove` flow against a real reachable box, **including a `remo cp` upload and download** to an added host with a non-default port/identity to confirm FR-005/C2) per `quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001)**: no dependencies.
- **Foundational (T002–T005)**: depends on T001; **blocks all user stories**.
- **US1 (T006–T012)**: depends on Foundational. MVP.
- **US2 (T013–T018)**: depends on Foundational; builds on US1's `providers/added.py` and `cli/added.py` (same files) so runs after US1 in a single-developer flow.
- **US3 (T019–T020)**: depends on Foundational; extends `providers/added.py` and adds the `--verify` option to the `add` command (T020) — the flag is introduced with its behavior, not before (F1).
- **Polish (T021–T024)**: after the desired stories are complete.

### Within Each User Story

- Write the story's tests first (they fail), then implement to green.
- `providers/added.py` before `cli/added.py` before registration in `cli/main.py`.

### Parallel Opportunities

- T002 and T004 (different test files) run in parallel; likewise T006/T007/T008.
- T013 ∥ T014; polish T021 ∥ T022 ∥ T023.
- Implementation tasks that touch the **same** file (`providers/added.py`: T009→T015→T016→T020; `cli/added.py`: T010→T017; `cli/main.py`: T011→T018) are sequential.

---

## Parallel Example: User Story 1

```bash
# Tests first (distinct files → parallel):
Task: "Target-parse + add tests in tests/unit/providers/test_added_add.py"
Task: "CLI add tests in tests/unit/cli/test_add_cmd.py"
Task: "Shell-degradation test in tests/unit/cli/test_shell_added.py"

# Then implement provider → CLI → registration (sequential; shared files):
Task: "providers/added.py: parse_ssh_target + add()"
Task: "cli/added.py: add command"
Task: "register add in cli/main.py"
Task: "shell.py: skip version check for type=ssh"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. T001 (Setup) → T002–T005 (Foundational) → T006–T012 (US1).
2. **STOP and VALIDATE**: quickstart Scenarios 1–3 and 7 — add a host, connect,
   confirm plain-shell degradation.
3. Ship the MVP: a user with only SSH access can register and connect.

### Incremental Delivery

- Add US2 (update/remove) → validate Scenarios 4–5.
- Add US3 (`--verify`) → validate Scenario 9.
- Polish (FR-012 guard, README, lint, quickstart) last.

---

## Notes

- `[P]` = different files, no ordering dependency.
- Every conditional branch (collision refuse vs update, IPv6 reject, port
  present/default, identity present/absent, verify pass/fail, version-check skip)
  has a dedicated test — Constitution II.
- Registry format is **unchanged**; only a new `type` value + type-gated reads.
- Commit after each story checkpoint.
