---
description: "Task list for Managed-Instance Tagging & Filtered Sync (Incus / Proxmox)"
---

# Tasks: Managed-Instance Tagging & Filtered Sync (Incus / Proxmox)

**Input**: Design documents from `/specs/013-managed-instance-tags/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — required by Constitution Principle II (Test All Conditional
Paths), the spec's Success Criteria, and the quickstart's named test files. Tests
are written to mock the provider SSH helpers (no live hypervisor needed), mirroring
the existing snapshot suites.

**Organization**: Tasks are grouped by user story (US1 P1, US2 P2, US3 P2) for
independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 / US2 / US3 (setup, foundational, polish have no story label)

## Path Conventions

Single-project Python CLI: `src/remo_cli/`, `tests/` at repository root (per plan.md).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the working baseline. This feature adds no new runtime deps.

- [X] T001 Verify dev env with `uv sync --all-extras`, confirm no new runtime dependency is required (per plan.md), and establish a green baseline with `uv run pytest -q`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The fixed marker constant and the shared apply helper that BOTH `create` (US1) and `update` (US3) depend on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 Add fixed marker constants `INCUS_MANAGED_CONFIG_KEY = "user.remo"`, `INCUS_MANAGED_CONFIG_VALUE = "true"`, and `PROXMOX_MANAGED_TAG = "remo"` (single source, not user-configurable) to src/remo_cli/core/config.py.
- [X] T003 [P] Implement `_apply_managed_marker(host, user, name)` in src/remo_cli/providers/incus.py — runs `incus config set <name> user.remo=true` via `_ssh_run_on_incus_host` (handles localhost + remote); idempotent no-op when already set; returns `(ok, err)` for FR-005 warn-not-fail. Depends on T002.
- [X] T004 [P] Implement `_apply_managed_marker(host, user, vmid)` in src/remo_cli/providers/proxmox.py — reads the `tags:` line from `pct config <vmid>`, and only when `remo` is absent writes the union `pct set <vmid> --tags "<existing;…;remo>"` (split on `[;, ]+`, join with `;`, preserve order); strict no-op when `remo` present; returns `(ok, err)`. Depends on T002.

**Checkpoint**: Marker constant + apply helpers ready — user stories can begin.

---

## Phase 3: User Story 1 - Sync only pulls in remo-managed containers (Priority: P1) 🎯 MVP

**Goal**: Default `remo <incus|proxmox> sync` registers only marker-bearing containers; `create` marks what it makes; a skip hint names anything skipped.

**Independent Test**: On a host with one remo-created and one hand-created container, run `sync` (no flags) → only the remo-created one is registered, and the hint names the skipped one plus both remedies.

### Tests for User Story 1

- [X] T005 [P] [US1] Write tests in tests/unit/providers/test_incus_marker.py covering: `create()` calls the apply helper; apply is idempotent; the marker-aware read helper parses `incus list -f csv -c n,user.remo`; default `sync(all=False)` registers only marked containers; the skip hint names skipped containers + count + both remedies; localhost parity. Also assert **FR-010** (`sync` issues NO `incus config set`/apply call — read-only) and **FR-013** (`sync` makes a bounded number of host queries — a single `incus list`, with no per-container round-trip). Mock `_ssh_run_on_incus_host` / `subprocess.run`.
- [X] T006 [P] [US1] Write tests in tests/unit/providers/test_proxmox_marker.py covering: `create()` calls the apply helper; tag union preserves pre-existing tags; the bulk tag read (`grep -H '^tags:' /etc/pve/lxc/*.conf`) classifies marked vs unmarked; default `sync(all=False)` filters; skip hint names skipped + remedies. Also assert **FR-010** (`sync` issues NO `pct set`/apply call) and **FR-013** (`sync` uses one bulk `grep` tag read plus `pct list` — no per-container `pct config` loop; assert the SSH-call count does not scale with container count). Mock `_ssh_run` / `subprocess.run`.

### Implementation for User Story 1

- [X] T007 [P] [US1] Add an Incus marker-aware read helper in src/remo_cli/providers/incus.py that runs `incus list -f csv -c n,user.remo` (localhost + remote) and returns `[(name, marked: bool)]` (marked = value == "true").
- [X] T008 [P] [US1] Add a Proxmox bulk tag read helper in src/remo_cli/providers/proxmox.py that runs one `grep -H '^tags:' /etc/pve/lxc/*.conf` over SSH and returns `{vmid: set(tags)}`, plus a `remo ∈ tags` classifier (vmid absent ⇒ unmarked).
- [X] T009 [US1] Wire `create()` in src/remo_cli/providers/incus.py to call `_apply_managed_marker` after `rc == 0` (post `save_known_host`); on failure `print_warning` the unmarked-container guidance but do NOT change the command's rc (FR-005). Same file as T007 — sequential.
- [X] T010 [US1] Wire `create()` in src/remo_cli/providers/proxmox.py to call `_apply_managed_marker(host, user, vmid)` after `rc == 0`, using the `vmid` from `_resolve_vmid`; if that `vmid` is empty, `print_warning` that the container could not be marked (and how to backfill) rather than crashing; warn-not-fail on any apply failure (FR-005). Same file as T008 — sequential.
- [X] T011 [US1] Change `sync()` in src/remo_cli/providers/incus.py to accept `all: bool = False`; in the default path register only marked containers (via T007 helper), collect skipped names, and emit the named skip hint (count + `--all` + `remo incus update <name>`) per contracts/cli-sync.md. Depends on T007.
- [X] T012 [US1] Change `sync()` in src/remo_cli/providers/proxmox.py to accept `all: bool = False`; default path registers only marked containers (via T008 helper), collects skipped names, and emits the named skip hint. Depends on T008.

**Checkpoint**: Default sync now filters on both providers and `create` marks — US1 fully testable (SC-001, SC-002).

---

## Phase 4: User Story 2 - Adopt every container on a host with `--all` (Priority: P2)

**Goal**: `sync --all` restores pre-feature unfiltered behavior and, when it registers unmarked containers, distinguishes the adopted-unmarked count.

**Independent Test**: On a host with only unmarked containers, `sync --all` registers all of them; the summary flags how many were not remo-created.

### Tests for User Story 2

- [X] T013 [P] [US2] Write tests in tests/unit/cli/providers/test_incus_sync_all.py: the `--all` flag threads to `providers.incus.sync(all=True)`; all containers register; when ≥1 was unmarked the summary distinguishes the adopted-unmarked count and states the round-trip drop (FR-009). Use Click `CliRunner` + mocks.
- [X] T014 [P] [US2] Write tests in tests/unit/cli/providers/test_proxmox_sync_all.py: same `--all` behavior for Proxmox (registers all, adopted-unmarked summary).

### Implementation for User Story 2

- [X] T015 [P] [US2] Add the `all=True` branch to `sync()` in src/remo_cli/providers/incus.py: register every discovered container, count those with `marked == False`, and emit the adopted-unmarked summary + round-trip warning (FR-007, FR-009). Depends on T011.
- [X] T016 [P] [US2] Add the `all=True` branch to `sync()` in src/remo_cli/providers/proxmox.py (register all, adopted summary). Depends on T012.
- [X] T017 [P] [US2] Add `--all` (`is_flag`) option to `remo incus sync` in src/remo_cli/cli/providers/incus.py and pass it to `providers_incus.sync(all=…)`.
- [X] T018 [P] [US2] Add `--all` (`is_flag`) option to `remo proxmox sync` in src/remo_cli/cli/providers/proxmox.py and pass it to `providers_proxmox.sync(all=…)`.

**Checkpoint**: US1 filtered default AND US2 `--all` opt-out both work (SC-003).

---

## Phase 5: User Story 3 - Backfill the marker via `update` (Priority: P2)

**Goal**: `remo <provider> update <name>` applies the marker (idempotently), making it the low-friction backfill path for pre-existing remo containers.

**Independent Test**: Take a pre-feature (unmarked) remo container, run `update`, then a default `sync` → the container is now registered.

### Tests for User Story 3

- [X] T019 [P] [US3] Extend tests/unit/providers/test_incus_marker.py: `update()` applies the marker (backfill, FR-004); re-running `update` is a no-op (SC-005); a container whose marker was manually removed is skipped by default sync (edge case). Depends on T005.
- [X] T020 [P] [US3] Extend tests/unit/providers/test_proxmox_marker.py: `update()` applies the marker preserving existing tags; two `update`s leave `tags: mytag;remo` unchanged/unreordered (SC-005). Depends on T006.

### Implementation for User Story 3

- [X] T021 [P] [US3] Wire `update()` in src/remo_cli/providers/incus.py to call `_apply_managed_marker` once `host`/`user` are resolved (before/around the configure playbook); warn-not-fail on failure (FR-004, FR-005). Depends on T003.
- [X] T022 [P] [US3] Wire `update()` in src/remo_cli/providers/proxmox.py to call `_apply_managed_marker(host, user, vmid)` once `host`/`user`/`vmid` are resolved; warn-not-fail (FR-004, FR-005). Depends on T004.

**Checkpoint**: All three stories independently functional (SC-004, SC-005, SC-006).

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T023 [P] Update README.md sync sections (lines ~236–268 and ~354–357) to state Incus/Proxmox `sync` now registers only remo-managed containers by default and document the `--all` opt-out and the `update` backfill path (Constitution Principle V).
- [X] T024 Run `uv run mypy src/remo_cli` and `uv run ruff check src/remo_cli`; resolve any findings introduced by this feature.
- [X] T025 Run the full `uv run pytest` and walk quickstart.md Scenarios 1–6 to confirm SC-001…SC-006.
- [X] T026 [P] Confirm FR-011 (no AWS/Hetzner change): existing AWS/Hetzner sync tests still pass and no marker logic leaked into src/remo_cli/providers/aws.py or hetzner.py.
- [X] T027 [P] Confirm FR-012 (registry & connect path unchanged): assert the `KnownHost` written by Incus/Proxmox `sync`/`create` keeps its existing fields with no marker column added (registry line format unchanged), and that `remo shell`/`remo cp` resolution is untouched — via existing registry/known-hosts tests plus a focused assertion in tests/unit/providers/test_incus_marker.py / test_proxmox_marker.py.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup; T002 blocks T003/T004; BLOCKS all user stories.
- **User Stories (Phase 3–5)**: all depend on Foundational.
  - US1 (P1) is the MVP and should land first (it flips the default behavior).
  - US2 (P2) provider branches depend on US1's `sync()` (T015→T011, T016→T012); its CLI-flag and test tasks are otherwise independent.
  - US3 (P2) depends only on the Foundational apply helpers (T021→T003, T022→T004); independent of US1/US2 code paths.
- **Polish (Phase 6)**: after the desired stories are complete.

### User Story Dependencies

- **US1 (P1)**: Foundational only. Delivers filtered default + create-marks + hint.
- **US2 (P2)**: extends US1's `sync()` for the `--all` branch; independently testable.
- **US3 (P2)**: Foundational only; parallelizable with US1/US2 (touches `update`, not `sync`).

### Within Each User Story

- Tests written to fail first, then implementation.
- Same-file tasks run sequentially (e.g., T007→T009→T011 all edit `providers/incus.py`).

### Parallel Opportunities

- Foundational: T003 ∥ T004 (different provider files).
- US1: T005 ∥ T006 (tests), T007 ∥ T008 (read helpers, different files).
- US2: T013 ∥ T014, and T015 ∥ T016 ∥ T017 ∥ T018 (four distinct files).
- US3: T019 ∥ T020, T021 ∥ T022.
- US1 and US3 can be developed in parallel by different people once Foundational is done.

---

## Parallel Example: User Story 1

```bash
# Tests first (different files):
Task: "test_incus_marker.py — create-marks, filtered sync, hint"      # T005
Task: "test_proxmox_marker.py — tag union, filtered sync, hint"       # T006

# Read helpers (different files):
Task: "Incus marker-aware list helper in providers/incus.py"          # T007
Task: "Proxmox bulk tag read helper in providers/proxmox.py"          # T008
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational (T002–T004) → 3. Phase 3 US1 (T005–T012).
4. **STOP and VALIDATE**: default `sync` filters and `create` marks (SC-001, SC-002).
5. Ship — this alone closes the core defect.

### Incremental Delivery

1. Foundational → US1 (MVP: filtered default) → 2. US2 (`--all` escape hatch) →
   3. US3 (`update` backfill) → 4. Polish (README, lint, quickstart).
   Each story adds value without breaking the previous one.

---

## Notes

- The marker is applied HOST-SIDE in the Python provider layer (not Ansible),
  because `update`'s configure playbook connects to the container IP, not the
  hypervisor host — see plan.md / research.md Decision 1.
- `sync` stays read-only (FR-010): it never calls the apply helper.
- Registry line format and the `remo shell`/`cp` connect path are unchanged (FR-012).
- Lifecycle commands (`destroy`/`snapshot`/resize) get NO marker guard (clarification 2).
