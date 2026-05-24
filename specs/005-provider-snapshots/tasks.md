---
description: "Implementation tasks for Provider Snapshots feature"
---

# Tasks: Provider Snapshots

**Input**: Design documents from `/specs/005-provider-snapshots/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/cli-surface.md ✅, quickstart.md ✅

**Tests**: Included. The contracts/cli-surface.md test matrix is the authoritative source for per-provider test scenarios.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing. Within each story, per-provider tasks are parallelizable (different files).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

Single-project Python CLI under `src/remo_cli/`, tests under `tests/unit/`. All paths absolute from repo root.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project scaffolding for the new files.

- [X] T001 Create empty `src/remo_cli/models/snapshot.py` and `src/remo_cli/core/snapshot.py` placeholder files (just the `from __future__ import annotations` header) so subsequent tasks have stable import paths
- [X] T002 [P] Create empty per-provider snapshot test files: `tests/unit/providers/test_incus_snapshot.py`, `tests/unit/providers/test_proxmox_snapshot.py`, `tests/unit/providers/test_aws_snapshot.py`, `tests/unit/providers/test_hetzner_snapshot.py` (just module docstring + future-annotations import) so tests can be added per-story without import-path churn
- [X] T003 [P] Create empty per-provider CLI snapshot test files: `tests/unit/cli/providers/test_incus_snapshot.py`, `tests/unit/cli/providers/test_proxmox_snapshot.py`, `tests/unit/cli/providers/test_aws_snapshot.py`, `tests/unit/cli/providers/test_hetzner_snapshot.py`
- [X] T004 [P] Create empty `tests/unit/core/test_snapshot.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Cross-cutting model and helpers used by every provider. No user story can begin until this phase is complete.

⚠️ **CRITICAL**: All four providers depend on the Snapshot dataclass, the SnapshotStatus enum, and the name generator/validator.

- [X] T005 Implement `SnapshotStatus` enum and `Snapshot` frozen dataclass in `src/remo_cli/models/snapshot.py` per `data-model.md`
- [X] T006 Implement `generate_default_name()` and `validate_name()` in `src/remo_cli/core/snapshot.py` per `research.md` (rules: 1–40 chars, `^[A-Za-z0-9][A-Za-z0-9_-]*$`); `validate_name` raises `click.BadParameter` on violation
- [X] T007 [P] Write unit tests in `tests/unit/core/test_snapshot.py` covering: default-name format matches `remo-YYYYMMDD-HHMMSS`, lexicographic sort order is creation order, name-too-long rejected, leading-dash rejected, spaces rejected, special chars rejected, single-char accepted, 40-char-exact accepted
- [X] T008 Verify all unit tests pass: `uv run --extra dev pytest tests/unit/core/test_snapshot.py -v`

**Checkpoint**: Foundation ready — user story implementation can now begin in parallel across providers.

---

## Phase 3: User Story 1 — Save state before a risky change (Priority: P1) 🎯 MVP

**Goal**: A developer can take a snapshot of their instance, mutate state, then restore — and end up back at the snapshot's state. Spec's "independent test" is satisfied by any one provider working end-to-end.

**Independent Test**: From `quickstart.md` steps 1–7 on any chosen provider: baseline file → snapshot → mutate → restore → verify baseline returns.

**MVP narrowing**: Implement Incus first (simplest: synchronous, COW, no SDK). Then expand to Proxmox / AWS / Hetzner in parallel by separate developers.

### Implementation for User Story 1 — Incus (MVP slice)

- [X] T009 [US1] Add internal helper `_ssh_run_on_incus_host(host, user, command) -> CompletedProcess` to `src/remo_cli/providers/incus.py` if not already present (matches the pattern in `providers/proxmox.py:_ssh_run`)
- [X] T010 [US1] Implement `_list_snapshots_for_container(incus_host, container, user) -> list[Snapshot]` in `src/remo_cli/providers/incus.py` using `incus query /1.0/instances/<container>/snapshots?recursion=1` over SSH and parsing JSON to Snapshot dataclasses
- [X] T011 [US1] Implement `snapshot_create(name, host, user, snap_name, description) -> int` in `src/remo_cli/providers/incus.py`. Calls T010 first to detect duplicate name (FR-006); on conflict exits 1 with a clear error. Otherwise runs `incus snapshot create <container> <snap_name>` (with `--description` if provided) over SSH. Returns 0 on success.
- [X] T012 [US1] Implement `snapshot_restore(name, host, user, snap_name, auto_confirm) -> int` in `src/remo_cli/providers/incus.py`. Calls T010 to verify the snapshot exists and is AVAILABLE (always true for Incus); rejects missing snapshot with exit 1 (FR-028 applies to pending only, missing is its own error). If `not auto_confirm`, calls `confirm("Restore '<snap>' to <container>? Container will be stopped during rollback. [y/N]", default=False)`. On accept, orchestrate: (1) query `incus info <container> --format json` over SSH and read `.status`; (2) if `Running`, run `incus stop <container>`; (3) run `incus restore <container> <snap_name>`; (4) if the container was Running pre-restore, run `incus start <container>` so the user is left with a reachable container (FR-013). Returns 0 on success and prints reconnect hint (SC-002).
- [X] T013 [US1] Add the `snapshot` Click subcommand group to `src/remo_cli/cli/providers/incus.py` using `@incus.group()`. Add `create` and `restore` commands that delegate to T011/T012. `create` accepts `--name` (calling `generate_default_name()` if omitted via Click `default_factory`), `--description`, and `--verbose`. `restore` accepts `-y`/`--yes` flag.
- [X] T014 [P] [US1] Write unit tests in `tests/unit/providers/test_incus_snapshot.py` covering rows 1–4 and 10–14 of the contracts test matrix for Incus: create happy path, create with name+description, create duplicate (mock T010 returns existing), create invalid name (delegated to T006 — covered in T007), restore confirm yes, restore confirm no, restore bypass with `--yes`, restore pending (always AVAILABLE on Incus — skip this row for Incus), restore missing snapshot
- [X] T015 [P] [US1] Write CLI unit tests in `tests/unit/cli/providers/test_incus_snapshot.py` using Click's `CliRunner` covering Click-level parsing + dispatch: `--name` default factory produces `remo-` prefix, `--description` passed through, `-y` short flag and `--yes` long flag both bypass confirm
- [X] T016 [US1] Run Incus tests and verify all pass: `uv run --extra dev pytest tests/unit/providers/test_incus_snapshot.py tests/unit/cli/providers/test_incus_snapshot.py -v`

**MVP Checkpoint**: Incus create + restore works. User Story 1 is independently testable on Incus.

### Implementation for User Story 1 — Proxmox

- [X] T017 [P] [US1] Implement `_detect_snapshot_capable_storage(host, user, vmid) -> tuple[bool, str]` in `src/remo_cli/providers/proxmox.py`: runs `pct config <vmid>` to extract rootfs storage, then `pvesm status` to look up its type; returns `(supported, storage_type)`. Supported set per `research.md`: zfspool, lvmthin, btrfs, cephfs, rbd, nfs.
- [X] T018 [P] [US1] Implement `_list_snapshots_for_vmid(host, user, vmid) -> list[Snapshot]` in `src/remo_cli/providers/proxmox.py` by reading `/etc/pve/lxc/<vmid>.conf` over SSH (`ssh <user>@<host> cat /etc/pve/lxc/<vmid>.conf`) and parsing `[<snap>]` sections, extracting `snaptime` and `description` keys.
- [X] T019 [US1] Implement `snapshot_create(name, host, user, vmid, snap_name, description) -> int` in `src/remo_cli/providers/proxmox.py`. Calls T017 first; on unsupported storage exits 1 with clear error (FR-005). Then calls T018 to detect duplicate name (FR-006). Then runs `pct snapshot <vmid> <snap_name> --description "<desc>"` via SSH. Returns 0 on success.
- [X] T020 [US1] Implement `snapshot_restore(name, host, user, vmid, snap_name, auto_confirm) -> int` in `src/remo_cli/providers/proxmox.py`. Uses T018 to verify snapshot exists. Confirm prompt: "Restore '<snap>' to <container>? Container will be stopped during rollback. [y/N]". On accept, orchestrate: (1) query `pct status <vmid>` to capture running/stopped state; (2) run `pct rollback <vmid> <snap_name>` — Proxmox handles the stop internally; (3) if container was `running` pre-rollback, run `pct start <vmid>` to leave the user with a reachable container (FR-013). Returns 0 on success with reconnect hint.
- [X] T021 [US1] Add `snapshot` subcommand group to `src/remo_cli/cli/providers/proxmox.py` with `create` and `restore` commands mirroring T013. Resolve `vmid` from `KnownHost.instance_id` and `host`/`user` from `KnownHost.region` per existing `_run_provider_update` dispatch pattern in `src/remo_cli/cli/shell.py`.
- [X] T022 [P] [US1] Write unit tests in `tests/unit/providers/test_proxmox_snapshot.py` covering: create happy path, create with explicit name + description, create duplicate name (mock T018 returns existing), create with unsupported storage (mock T017 returns `(False, 'dir')`) → exit 1 with `dir`-naming message, restore happy path, restore confirm decline, restore bypass, restore missing snapshot
- [X] T023 [P] [US1] Write CLI unit tests in `tests/unit/cli/providers/test_proxmox_snapshot.py` mirroring T015
- [X] T024 [US1] Run Proxmox tests: `uv run --extra dev pytest tests/unit/providers/test_proxmox_snapshot.py tests/unit/cli/providers/test_proxmox_snapshot.py -v`

### Implementation for User Story 1 — AWS

- [X] T025 [P] [US1] Add helper `_get_root_volume_id(ec2, instance_id) -> tuple[str, int, str, str]` to `src/remo_cli/providers/aws.py` returning `(volume_id, size_gib, az, device_name)` from `describe_instances` → `BlockDeviceMappings`
- [X] T026 [P] [US1] Implement `_list_snapshots_for_volume(ec2, volume_id) -> list[Snapshot]` in `src/remo_cli/providers/aws.py` using `describe_snapshots(Filters=[{Name: "volume-id", Values: [volume_id]}, {Name: "tag:remo", Values: ["true"]}])`. Maps `State` to `SnapshotStatus`: `pending`/`creating` → PENDING, `completed` → AVAILABLE, `error` → FAILED. Extracts `remo-snapshot-name` tag as the user-facing name.
- [X] T027 [US1] Implement `snapshot_create(name, snap_name, description, region) -> int` in `src/remo_cli/providers/aws.py`. Resolves instance + root volume via T025. Detects duplicate name via T026 (FR-006). Calls `ec2.create_snapshot(VolumeId, Description, TagSpecifications=[{ResourceType: "snapshot", Tags: [remo=true, remo-snapshot-name=<name>, remo-instance=<instance>]}])`. Prints "creation started ... will take several minutes" hint (FR-004). Returns 0 immediately (no polling).
- [X] T028 [US1] Implement `snapshot_restore(name, snap_name, region, auto_confirm) -> int` in `src/remo_cli/providers/aws.py` per the AWS restore flow in `research.md`: (1) lookup snapshot via T026 + verify status == AVAILABLE (FR-028 — exit 1 if PENDING); (2) confirm with downtime warning; (3) stop instance, poll until stopped; (4) detach root volume, poll until available; (5) `create_volume(SnapshotId, Size=max(current, snap.size), AZ, VolumeType=current_type)`, poll until available; (6) attach new volume at the original device name, poll until in-use; (7) start instance, poll until running; (8) tag old volume with `remo-restore-orphan=<timestamp>` and KEEP it (per `research.md` step 9). On step-4-through-7 failure: best-effort fallback attach of original volume + start; if fallback fails, print explicit recovery instructions naming both volume IDs (FR-016). Print FR-029 hint about `resize2fs` if the new volume is larger than snapshot recorded size.
- [X] T029 [US1] Add `snapshot` subcommand group to `src/remo_cli/cli/providers/aws.py` with `create` and `restore` commands. Resolve instance name from `--name` flag if given else from `os.environ.get("USER", "remo")` per existing AWS convention.
- [X] T030 [P] [US1] Write unit tests in `tests/unit/providers/test_aws_snapshot.py` mocking `boto3` (use `mocker.patch` on `_boto3_session` or the EC2 client directly): create happy path (verify TagSpecifications), create async hint in output, create duplicate name → exit 1, restore happy path (verify call sequence: stop → detach → create_volume → attach → start; verify old volume gets `remo-restore-orphan` tag), restore confirm decline (no stop call made), restore bypass with `--yes`, restore pending snapshot → exit 1 (FR-028), restore missing snapshot → exit 1, restore mid-flight failure during attach → fallback reattach attempted → recovery message includes both volume IDs (FR-016), restore with current volume larger than snapshot → new volume sized at current → resize2fs hint printed (FR-029)
- [X] T031 [P] [US1] Write CLI unit tests in `tests/unit/cli/providers/test_aws_snapshot.py` mirroring T015
- [X] T032 [US1] Run AWS tests: `uv run --extra dev pytest tests/unit/providers/test_aws_snapshot.py tests/unit/cli/providers/test_aws_snapshot.py -v`

### Implementation for User Story 1 — Hetzner

- [X] T033 [P] [US1] Implement `_list_snapshots_for_server(client, server_id) -> list[Snapshot]` in `src/remo_cli/providers/hetzner.py` using `client.images.get_all(type=[ImageType.SNAPSHOT], label_selector=f"remo=true,remo-source-server-id={server_id}")`. Maps Hetzner image status to SnapshotStatus.  *(Implemented via raw `urllib` REST calls rather than the hcloud client, matching the existing pattern in this file.)*
- [X] T034 [US1] Implement `snapshot_create(name, snap_name, description) -> int` in `src/remo_cli/providers/hetzner.py`. Looks up server by name; detects duplicate via T033; calls `client.servers.create_image(server=<server>, type=ImageType.SNAPSHOT, description=description, labels={"remo": "true", "remo-snapshot-name": snap_name, "remo-source-server-id": str(server.id)})`. Prints async hint. Returns 0 immediately.
- [X] T035 [US1] Implement `snapshot_restore(name, snap_name, auto_confirm) -> int` in `src/remo_cli/providers/hetzner.py`. Verify snapshot AVAILABLE via T033. Confirm with downtime hint. Call `client.servers.rebuild(server=<server>, image=<image>)`. Poll the returned Action until `status == "success"` (timeout 10 min). Return 0; print reconnect hint.
- [X] T036 [US1] Add `snapshot` subcommand group to `src/remo_cli/cli/providers/hetzner.py` with `create` and `restore` commands.
- [X] T037 [P] [US1] Write unit tests in `tests/unit/providers/test_hetzner_snapshot.py` mocking the `hcloud` client: create happy path (verify labels), create async hint, create duplicate name, restore happy path (verify rebuild called + action polled), restore confirm decline, restore bypass, restore pending → exit 1, restore missing → exit 1
- [X] T038 [P] [US1] Write CLI unit tests in `tests/unit/cli/providers/test_hetzner_snapshot.py` mirroring T015
- [X] T039 [US1] Run Hetzner tests: `uv run --extra dev pytest tests/unit/providers/test_hetzner_snapshot.py tests/unit/cli/providers/test_hetzner_snapshot.py -v`

**Checkpoint**: User Story 1 complete across all four providers. Create + restore round-trip works. Run `quickstart.md` steps 1–7 manually on at least one live provider to validate.

---

## Phase 4: User Story 2 — See what snapshots exist (Priority: P2)

**Goal**: User can list snapshots for an instance (or for all instances of a provider) and see when each was taken, its size, and its async status if applicable.

**Independent Test**: From `quickstart.md` step 3: after creating two snapshots, `list` shows both with correct columns.

**Implementation note**: The per-provider `_list_snapshots_for_*` functions already exist from US1 (used by restore). US2 adds the **public CLI command** and **table formatting**.

- [X] T040 [US2] Implement shared `format_snapshot_table(snapshots: list[Snapshot], *, show_status: bool) -> str` in `src/remo_cli/core/snapshot.py` that produces the column layout from `contracts/cli-surface.md` (`INSTANCE  SNAPSHOT  CREATED  SIZE  STATUS  DESCRIPTION`). The `show_status` arg is set by the caller based on provider — `True` for AWS/Hetzner (per FR-008), `False` for Incus/Proxmox. Size rendered as human-readable (e.g., `1.2 GiB`) using existing convention; `—` when `size_bytes is None`. Empty-list case returns the `No snapshots found for instance '<X>'` message (FR-010). T042/T043 pass `show_status=False`; T044/T045 pass `show_status=True`.
- [X] T041 [P] [US2] Add unit tests for `format_snapshot_table` in `tests/unit/core/test_snapshot.py`: status column omitted when `show_status=False`, status column present when `show_status=True`, size rendering for None/0/1024/1.5GB inputs, empty-list message, **and a negative assertion (FR-009) that the rendered output contains none of: `$`, `€`, the substring `cost` (case-insensitive), or `/mo`** — guards against accidental reintroduction of cost columns.
- [X] T042 [P] [US2] Add `snapshot list` command to `src/remo_cli/cli/providers/incus.py` that resolves instance from arg (or iterates all incus hosts from registry if omitted), calls T010's helper, formats via T040, prints. Exit 1 on SSH failure (FR-011).
- [X] T043 [P] [US2] Add `snapshot list` command to `src/remo_cli/cli/providers/proxmox.py` mirroring T042 (iterating known proxmox hosts when instance arg omitted)
- [X] T044 [P] [US2] Add `snapshot list` command to `src/remo_cli/cli/providers/aws.py` mirroring T042 (iterating known AWS instances + lazy boto3 import error per existing pattern)
- [X] T045 [P] [US2] Add `snapshot list` command to `src/remo_cli/cli/providers/hetzner.py` mirroring T042 (iterating known Hetzner servers + lazy hcloud import error)
- [X] T046 [P] [US2] Extend `tests/unit/providers/test_incus_snapshot.py` and `tests/unit/cli/providers/test_incus_snapshot.py` with list-scenario rows from the contracts matrix (happy path with rows, no snapshots, provider unreachable)
- [X] T047 [P] [US2] Extend `tests/unit/providers/test_proxmox_snapshot.py` and `tests/unit/cli/providers/test_proxmox_snapshot.py` with list-scenario rows
- [X] T048 [P] [US2] Extend `tests/unit/providers/test_aws_snapshot.py` and `tests/unit/cli/providers/test_aws_snapshot.py` with list-scenario rows (verify status column shows `pending`/`available`)
- [X] T049 [P] [US2] Extend `tests/unit/providers/test_hetzner_snapshot.py` and `tests/unit/cli/providers/test_hetzner_snapshot.py` with list-scenario rows
- [X] T050 [US2] Run all snapshot tests: `uv run --extra dev pytest tests/unit/cli/providers/ tests/unit/providers/ tests/unit/core/test_snapshot.py -v`

**Checkpoint**: User Story 2 complete. `remo <P> snapshot list` works on all four providers.

---

## Phase 5: User Story 3 — Clean up an unwanted snapshot (Priority: P2)

**Goal**: User can delete an individual snapshot.

**Independent Test**: From `quickstart.md` step 8: snapshot created in step 2 is removed; `list` no longer shows it.

- [X] T051 [P] [US3] Implement `snapshot_delete(...)` in `src/remo_cli/providers/incus.py` using `incus snapshot delete <container>/<snap_name>` over SSH. Reject if status != AVAILABLE (FR-028); reject if not found (exit 1). Confirm with default-False prompt unless `auto_confirm`.
- [X] T052 [P] [US3] Implement `snapshot_delete(...)` in `src/remo_cli/providers/proxmox.py` using `pct delsnapshot <vmid> <snap_name>` over SSH. Same validation pattern.
- [X] T053 [P] [US3] Implement `snapshot_delete(...)` in `src/remo_cli/providers/aws.py` using `ec2.delete_snapshot(SnapshotId=...)`. Same validation pattern.  *(Delivered alongside AWS US1 expansion; CLI added in T057.)*
- [X] T054 [P] [US3] Implement `snapshot_delete(...)` in `src/remo_cli/providers/hetzner.py` using `client.images.delete(image=...)`. Same validation pattern.  *(Delivered alongside Hetzner US1 expansion; CLI added in T058.)*
- [X] T055 [P] [US3] Add `snapshot delete` Click command to `src/remo_cli/cli/providers/incus.py` (positional `instance`, positional `snapshot`, `-y`/`--yes`)
- [X] T056 [P] [US3] Add `snapshot delete` Click command to `src/remo_cli/cli/providers/proxmox.py`
- [X] T057 [P] [US3] Add `snapshot delete` Click command to `src/remo_cli/cli/providers/aws.py`
- [X] T058 [P] [US3] Add `snapshot delete` Click command to `src/remo_cli/cli/providers/hetzner.py`
- [X] T059 [P] [US3] Extend `tests/unit/providers/test_incus_snapshot.py` and `tests/unit/cli/providers/test_incus_snapshot.py` with delete-scenario rows: confirm yes (provider call made), confirm no (provider call NOT made), bypass with --yes, pending → exit 1, missing → exit 1
- [X] T060 [P] [US3] Extend Proxmox test files with delete scenarios
- [X] T061 [P] [US3] Extend AWS test files with delete scenarios
- [X] T062 [P] [US3] Extend Hetzner test files with delete scenarios
- [X] T063 [US3] Run all snapshot tests: `uv run --extra dev pytest tests/unit/cli/providers/ tests/unit/providers/ tests/unit/core/test_snapshot.py -v`

**Checkpoint**: User Story 3 complete. Delete works on all four providers.

---

## Phase 6: User Story 4 — Destroy-time snapshot cleanup (Priority: P2)

**Goal**: When destroying an instance with existing snapshots, the user is shown them and prompted to clean up first.

**Independent Test**: From `quickstart.md` step 9: destroy an instance with snapshots, both cleanup-accepted and cleanup-declined paths produce the documented behavior.

**Implementation note**: Uses `_list_snapshots_for_*` (from US1) and `snapshot_delete` (from US3). Modifies the existing `destroy()` functions in each provider's business-logic file.

- [X] T064 [US4] Modify `destroy()` in `src/remo_cli/providers/incus.py`: before the existing destruction logic, call `_list_snapshots_for_container(...)`. If list is empty, behave exactly as today (FR-023). If non-empty, print warning + use `core/snapshot.format_snapshot_table()` to display them, then `confirm("Delete these snapshots as part of destroy?", default=False)`. On accept, iterate and call `snapshot_delete(..., auto_confirm=True)`. On decline, print the orphan-cost warning (FR-022). Then proceed with the existing destroy.
- [X] T065 [P] [US4] Modify `destroy()` in `src/remo_cli/providers/proxmox.py` per T064's pattern
- [X] T066 [P] [US4] Modify `destroy()` in `src/remo_cli/providers/aws.py` per T064's pattern (note: also need to handle the case where the orphaned `remo-restore-orphan` volume tag exists — leave those alone; the prompt only covers snapshots, not orphan volumes)
- [X] T067 [P] [US4] Modify `destroy()` in `src/remo_cli/providers/hetzner.py` per T064's pattern
- [X] T068 [P] [US4] Extend `tests/unit/providers/test_incus.py` (the existing destroy test file, NOT the snapshot test file) with three new test cases: destroy with snapshots + cleanup accepted (assert each snapshot deleted then instance destroyed), destroy with snapshots + cleanup declined (assert snapshots NOT deleted but instance still destroyed + orphan-warning printed), destroy with no snapshots (assert behavior unchanged — no new prompts).  *(No `test_incus.py` destroy test file existed; the four destroy-integration scenarios were added to `test_incus_snapshot.py` where related tests live. Also added a 4th scenario covering `--yes`-keeps-snapshots-with-warning, matching the safer-default behavior.)*
- [X] T069 [P] [US4] Extend `tests/unit/providers/test_proxmox.py` with the three destroy-with-snapshots scenarios.  *(Added to test_proxmox_snapshot.py — same reason as T068.)*
- [X] T070 [P] [US4] Extend `tests/unit/providers/test_aws.py` with the three destroy-with-snapshots scenarios.  *(Added to test_aws_snapshot.py — same reason.)*
- [X] T071 [P] [US4] Extend `tests/unit/providers/test_hetzner.py` with the three destroy-with-snapshots scenarios.  *(Added to test_hetzner_snapshot.py — same reason.)*
- [X] T072 [US4] Run the full test suite to confirm no regression in existing destroy tests: `uv run --extra dev pytest -v`

**Checkpoint**: User Story 4 complete. Destroy now cleans up (or warns about) snapshots on all four providers.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, validation, and cleanup before the PR.

- [X] T073 Update `README.md` with a new "Snapshots" section documenting `remo <provider> snapshot {create,list,restore,delete}` and the destroy-time prompt. Include a one-liner per-provider on restore semantics (in-place vs. volume-swap-with-downtime vs. server-rebuild-with-downtime).
- [X] T074 [P] Update `CLAUDE.md` "Active Technologies" section if the agent-context script's auto-insertion left stale wording (manually trim duplicates between this story and 003-python-cli-rewrite if needed)  *(Verbose auto-insert collapsed to a single one-line entry matching the style of the other features in the file; same for the "Recent Changes" entry.)*
- [X] T075 [P] Run `uv run mypy src/remo_cli` and fix any type errors introduced — no new errors; the one pre-existing boto3-untyped warning is unchanged.
- [X] T076 [P] Run `uv run ruff check src/remo_cli tests` and fix any lint findings introduced — the two issues my code introduced (unused MagicMock import in hetzner test, extraneous f-prefix) were auto-fixed; remaining ruff warnings in the repo are pre-existing.
- [ ] T077 Execute `quickstart.md` end-to-end manually on one provider of your choice (Incus is fastest); confirm all 9 steps pass including the failure-path checks (unsupported storage, pending-snapshot op, name validation)  *(Deferred to user — requires a live Incus/Proxmox/AWS/Hetzner environment.)*
- [X] T078 Bump version in `pyproject.toml` to `2.0.0rc3` and run `uv lock` to refresh `uv.lock`
- [~] T079 Commit each user story as its own logical commit (US1 → US2 → US3 → US4 → polish); push branch and open PR against `main`  *(Commits done: c68992c US1 MVP, 731b635 US1 expansion, 8908b47 US2+US3, 8160a1c US4, 7f794f7 polish. Push + PR remain — user-authorized actions, deliberately not initiated by the agent.)*

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1. **Blocks all user stories.**
- **Phase 3 (US1)**: Depends on Phase 2. MVP slice is Incus (T009–T016); per-provider expansion (T017–T039) is `[P]` once foundation is done.
- **Phase 4 (US2)**: Depends on Phase 3 (uses each provider's internal `_list_snapshots_for_*` helper from US1).
- **Phase 5 (US3)**: Depends on Phase 2 (and on each provider's snapshot module existing — i.e., the file work from Phase 3). Independent of Phase 4 in principle but in practice the test files were created in Phase 3 / extended in Phase 4.
- **Phase 6 (US4)**: Depends on Phase 5 (uses `snapshot_delete` from US3) and Phase 3's `_list_snapshots_for_*`.
- **Phase 7 (Polish)**: Depends on all user stories.

### Within-Story Dependencies

- **US1, Incus slice (T009–T016)**: Sequential within file `providers/incus.py` (T009 → T010 → T011 → T012); T013 depends on T011/T012; tests T014/T015 [P] after impl; T016 runs all tests.
- **US1, per-provider expansion**: Each provider's tasks form an independent chain; the four chains are `[P]` with each other.
- **US2**: T040 (formatter) → T041 (formatter tests); T042–T045 (per-provider list CLI) are `[P]` and depend on T040; T046–T049 (per-provider tests) are `[P]` and depend on respective CLI tasks.
- **US3**: T051–T054 (per-provider impl) all `[P]`; T055–T058 (per-provider CLI) all `[P]` after their respective impl; T059–T062 (tests) all `[P]` after CLI.
- **US4**: T064–T067 all `[P]` after Phase 5 complete; T068–T071 (tests) all `[P]`.

### Parallel Opportunities

- **Setup**: T002, T003, T004 all `[P]` after T001.
- **Foundational**: T007 [P] with T005/T006 (test file is separate).
- **US1 expansion (after Incus MVP)**: All four providers' chains can be worked by four developers in parallel — they touch entirely different files.
- **US2**: All four providers' `list` commands and tests in parallel.
- **US3**: All four providers' `delete` impl, CLI, and tests in parallel.
- **US4**: All four providers' destroy modifications and tests in parallel.
- **Polish**: T074, T075, T076 [P] (different concerns / no shared files).

---

## Parallel Example: User Story 1 expansion

After Incus MVP (T009–T016) completes, four developers (or four `[P]` tasks) can work simultaneously:

```text
Developer A — Proxmox slice: T017, T018, T019, T020, T021, T022, T023, T024
Developer B — AWS slice:     T025, T026, T027, T028, T029, T030, T031, T032
Developer C — Hetzner slice: T033, T034, T035, T036, T037, T038, T039
```

No file conflicts (each chain touches only `providers/<P>.py`, `cli/providers/<P>.py`, and its own test files). Foundational `models/snapshot.py` and `core/snapshot.py` are read-only at this point.

## Parallel Example: User Story 2

```text
T040 (shared formatter) → then [P]:
  T042 (Incus list CLI)    + T046 (Incus list tests)
  T043 (Proxmox list CLI)  + T047 (Proxmox list tests)
  T044 (AWS list CLI)      + T048 (AWS list tests)
  T045 (Hetzner list CLI)  + T049 (Hetzner list tests)
T050 (run all)
```

---

## Implementation Strategy

### MVP First (US1 Incus slice only)

1. Phase 1 (T001–T004) — minutes.
2. Phase 2 (T005–T008) — half-day; foundation locked.
3. Phase 3 Incus slice (T009–T016) — half-day; end with `uv run remo incus snapshot create` working end-to-end on a real Incus container.
4. **STOP and validate via quickstart.md on Incus.**

Total to MVP: ~1 day.

### Incremental Delivery

1. MVP (US1 Incus) → demo.
2. US1 Proxmox slice (T017–T024) → US1 AWS slice (T025–T032) → US1 Hetzner slice (T033–T039). Each adds a provider; demo after each.
3. US2 (list across all providers) → demo.
4. US3 (delete across all providers) → demo.
5. US4 (destroy integration) → demo.
6. Polish + PR.

### Parallel Team Strategy

After Phase 2:
- Devs A/B/C each take one non-Incus provider for US1 expansion.
- After all of US1: same devs each take their provider's US2/US3/US4 chains.

---

## Notes

- `[P]` tasks = different files, no dependencies on incomplete tasks in the same phase.
- `[Story]` label maps the task to the spec.md user story for traceability.
- Each user story is independently completable and testable per its checkpoint.
- The contracts/cli-surface.md test matrix is the authoritative source of test coverage; the task descriptions above reference subsets of that matrix.
- Commit after each provider slice within US1, and after each story's checkpoint thereafter.
- The AWS restore flow (T028) is the riskiest task; if implementing solo, do it last within US1's expansion so you have the Incus/Proxmox/Hetzner patterns to draw on.
- Avoid: bundling AWS restore impl with its tests in one mega-commit — split the multi-step flow across 2–3 commits with the tests interleaved.
