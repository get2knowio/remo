---

description: "Task list for 016-sync-reconcile"
---

# Tasks: Unified Sync Reconcile

**Input**: Design documents from `/specs/016-sync-reconcile/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Test tasks ARE included and are not optional. SC-008 requires automated sync coverage for all four providers against a real temporary registry, and Constitution Principle II ("Test All Conditional Paths") binds the consent gate, the completeness flag, and the marker branches. Two providers have zero sync tests today, and where sync *is* tested the destructive step is mocked out — which is why neither AWS bug was caught.

**Organization**: Grouped by user story. **Phase ordering is risk-ordered, not strictly priority-ordered** — see the note below.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Exact file paths are included in every task

## Path Conventions

Single project, three-layer CLI: `src/remo_cli/{cli,providers,core}/`, `tests/{unit,integration}/`, `ansible/`, `docs/`.

## Why Hetzner comes early

US4 is P2, but its Hetzner half is pulled ahead of the two P1 AWS phases. Until `sync()` is rewritten, `remo hetzner sync` still runs `clear_known_hosts_by_type("hetzner")` unguarded — and because the `remo` label it filters on is never applied by anything, **every invocation wipes the entire Hetzner registry**. It is the only provider whose bug fires unconditionally, and the consent gate does not protect it until its own probe lands. The Proxmox half of US4 stays at its priority position.

---

## Phase 1: Setup

**Purpose**: Establish a baseline so new breakage is distinguishable from pre-existing state.

- [X] T001 Capture the baseline by running `uv run pytest`, `uv run mypy src/remo_cli`, and `uv run ruff check src/remo_cli`; record any pre-existing failures in the PR description
- [X] T002 Add a `seed_registry(config_dir, hosts)` helper to `tests/conftest.py` that writes a v2 registry from a list of `KnownHost` objects, wrapping the existing `build_v2_host_entry` and `write_v2_registry` helpers

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build `core/reconcile.py`, the provider-agnostic engine every user story depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

**Note on parallelism**: T003–T011 all edit the same new file, and T012–T015 all edit the same test file. This phase is inherently sequential.

- [X] T003 Create `src/remo_cli/core/reconcile.py` with module docstring, `from __future__ import annotations`, exit-code constants (`EXIT_OK = 0`, `EXIT_FAILURE = 1`, `EXIT_ABORTED = 3`), and the exception hierarchy `ProbeError` / `ReconcileConflictError` / `AmbiguousPlanError` / `ScopeError`; the module must never raise `SystemExit`, per the boundary `core/registry.py` already observes
- [X] T004 Implement the `SyncScope` frozen dataclass in `src/remo_cli/core/reconcile.py` with `in_update_scope()`, `in_removal_scope()`, `describe()`, and construction-time validation, per the predicate table in `data-model.md`; `describe()` must name the enumeration boundary (`incus host h (default project)`, `proxmox node n (this node only)`) per FR-045, and AWS's asymmetry applies — an entry with an empty region is matchable for update but never removable
- [X] T005 Implement the `DiscoveredHost` and `ProbeResult` frozen dataclasses in `src/remo_cli/core/reconcile.py`, including `complete`, `incomplete_reason`, `adoption_criteria`, and `warnings`
- [X] T006 Implement `merge_entry(existing, discovered)` in `src/remo_cli/core/reconcile.py`: refresh `host`/`access_mode`/`instance_id`/`region` only when the discovered value is non-empty, always preserve `user`, never touch `type`/`name` (FR-041, with FR-018 falling out of it)
- [X] T007 Implement the `ReconcilePlan` dataclass and the pure `build_plan(current, probe, scope)` in `src/remo_cli/core/reconcile.py` per the classification matrix in `data-model.md`, including `skipped_unmarked`, `retained_unmarked`, `removals_suppressed`, `baseline`, and `AmbiguousPlanError` on duplicate names within a scope (FR-037)
- [X] T008 Implement `render_plan(plan, dry_run)` in `src/remo_cli/core/reconcile.py` following the output contract in `contracts/cli-sync.md`: scope line first, four named categories with accurate counts, the explicit no-op message, unmarked-retained note, skipped-unmarked hints, adoption criteria, and the suppressed-removals warning
- [X] T009 Implement the consent gate in `src/remo_cli/core/reconcile.py`: `ConsentOutcome` enum and a function returning `NOT_REQUIRED` / `APPLY` / `DECLINED` / `NON_INTERACTIVE`, using `sys.stdin.isatty()` before `confirm()` from `core/output.py` (FR-011–FR-014)
- [X] T010 Implement `apply_plan(plan)` in `src/remo_cli/core/reconcile.py` using a single `mutate_registry()` call whose mutator re-derives the in-scope slice, compares it to `plan.baseline`, raises `ReconcileConflictError` on drift with a re-run instruction and no automatic retry (FR-046), and otherwise splices reconciled in-scope entries back beside untouched out-of-scope ones — the mutator must call no other registry function, as the lock is not reentrant
- [X] T011 Implement the `run_sync(scope, probe_fn, auto_confirm, dry_run)` driver in `src/remo_cli/core/reconcile.py` sequencing probe → read → build → render → dry-run exit → consent → apply, catching all four exceptions, reporting via `print_error`, and returning the exit code
- [X] T012 Write unit tests for `SyncScope` predicates, `describe()` boundary text, and `merge_entry` in `tests/unit/core/test_reconcile.py`, covering all four providers, the AWS empty-region asymmetry, and the stopped-instance address-preservation case
- [X] T013 Write unit tests for `build_plan` in `tests/unit/core/test_reconcile.py` covering every row of the classification matrix: added, updated, unchanged, removed, skipped-unmarked, retained-unmarked, `complete=False` suppression, out-of-scope isolation, and the ambiguous-name refusal
- [X] T014 Write unit tests for the consent gate and exit codes in `tests/unit/core/test_reconcile.py` covering all five outcomes (no removals, `--yes`, confirmed, declined, non-interactive) and asserting `2` is never returned
- [X] T015 Write unit tests for `apply_plan` in `tests/unit/core/test_reconcile.py` using `tmp_config_dir`, asserting exactly one `mutate_registry` call, out-of-scope entries preserved byte-for-byte, `ReconcileConflictError` when the same-scope slice changes between plan and write, and no conflict when only a *different* scope changed (FR-046, SC-016)

**Checkpoint**: The reconcile engine is complete and fully unit-tested with no provider wired. Nothing is user-visible yet.

---

## Phase 3: User Story 1 - Sync never silently deletes registry entries (Priority: P1) 🎯 MVP

**Goal**: Prove the whole pipeline end-to-end on one provider — scope, plan, consent, single atomic write, exit codes. Incus is chosen for the smallest probe and because it already has `--all` and `--use-ip` to prove they survive.

**Independent Test**: Seed a registry, force the probe to return zero hosts, run sync and decline — registry byte-identical, exit 3. Repeat with `--yes` — removals applied and named, exit 0.

### Implementation for User Story 1

- [X] T016 [US1] Change `_resolve_container_ip` in `src/remo_cli/providers/incus.py` to return `""` on SSH failure instead of `sys.exit(1)` (currently lines 113 and 117), and catch `FileNotFoundError`, so a transient failure no longer becomes a proposed deletion
- [X] T017 [US1] Add `_probe(scope, user, use_ip, include_all)` to `src/remo_cli/providers/incus.py` returning a `ProbeResult` with every container (marked and unmarked), `marked` from `user.remo`, `complete=True`, per-container IP failures appended to `warnings`, and `ProbeError` raised when the listing itself fails
- [X] T018 [US1] Rewrite `sync()` in `src/remo_cli/providers/incus.py` to build a `SyncScope(type="incus", host=host)`, delegate to `run_sync`, accept `auto_confirm` and `dry_run`, and return `int` instead of `None`
- [X] T019 [US1] Delete the now-false "a later default `sync` will drop those unmarked one(s) again" warning from `src/remo_cli/providers/incus.py` (FR-026)
- [X] T020 [US1] Add `--yes/-y` and `--dry-run` options to the sync command in `src/remo_cli/cli/providers/incus.py` using the explicit-destination style from `contracts/cli-sync.md`, and add the missing `sys.exit(rc)`

### Tests for User Story 1

- [X] T021 [P] [US1] Write probe tests in `tests/unit/providers/test_incus_sync.py` patching `remo_cli.providers.incus._ssh_run_on_incus_host`, covering marked/unmarked classification, `include_all`, `--use-ip` soft IP failure, listing failure raising `ProbeError`, and read-only behaviour (no `incus config set`)
- [X] T022 [P] [US1] Update `tests/unit/cli/providers/test_incus_sync_all.py` for the `int` return type and the new `--yes`/`--dry-run` flags, asserting they thread through as `auto_confirm` and `dry_run`
- [X] T023 [US1] Write the core safety matrix in `tests/integration/test_sync_reconcile.py` against a real registry via `tmp_config_dir`: empty probe + decline (unchanged, exit 3), empty probe + `--yes` (removed and named, exit 0), probe raises (unchanged, exit 1), no TTY without `--yes` (unchanged, exit 3), `--dry-run` (unchanged, no prompt, exit 0), and additions-only (no prompt)

**Checkpoint**: The empty-result wipe is fixed for Incus. This is a shippable MVP.

---

## Phase 4: Hetzner critical path (User Story 4, with a User Story 5 pull-forward)

**Goal**: Stop the only unconditional data-loss path in the product. Also the first time `remo hetzner sync` has ever worked.

**Independent Test**: A labelled server is discovered by a plain sync with no flags; a 60-server project enumerates all 60, not 25; an unlabelled but live server is retained rather than proposed for deletion.

### Implementation

- [X] T024 [US4] Add `_hetzner_api_paged(path, key)` to `src/remo_cli/providers/hetzner.py` looping on `meta.pagination.next_page` with `per_page=50` and reporting whether it ran to exhaustion — today `sync` silently truncates at Hetzner's 25-item default
- [X] T025 [US4] Add `_probe(scope, include_all)` to `src/remo_cli/providers/hetzner.py` routed through `_hetzner_api_paged`, replacing the inline `urllib` block at lines 396-407; it MUST call `GET /v1/servers` **without** `label_selector` (FR-044) and set `marked` from the presence of the `remo` label locally, set `entry.host=""` for a server with no IPv4, and raise `ProbeError` when the API call fails
- [X] T026 [US4] Rewrite `sync()` in `src/remo_cli/providers/hetzner.py` to accept `include_all`/`auto_confirm`/`dry_run`, delegate to `run_sync` with `SyncScope(type="hetzner")`, and return `int` — it currently takes no parameters at all
- [X] T027 [US4] Add `--yes/-y` and `--dry-run` options plus `sys.exit(rc)` to the sync command in `src/remo_cli/cli/providers/hetzner.py`
- [X] T028 [P] [US5] Add `labels: {remo: "true"}` to the `hetzner.hcloud.server` task in `ansible/roles/hetzner_server/tasks/main.yml` as a sibling key of `state: present`; label the server only — not the shared SSH key, and not the volume, whose `state: present` re-assertion in `ansible/hetzner_resize.yml` could strip it

### Tests

- [X] T029 [P] [US4] Write probe and pagination tests in `tests/unit/providers/test_hetzner_sync.py` patching `remo_cli.providers.hetzner._hetzner_api`, covering a two-page walk to exhaustion, a second-page failure yielding `complete=False` with removals suppressed, and a server with no IPv4
- [X] T030 [US4] Add a marker-independence test to `tests/unit/providers/test_hetzner_sync.py` proving an unlabelled but live server in the registry is retained and reported unmarked, never removed (FR-044, SC-015) — the regression guard for the server-side-filter bug

**Checkpoint**: Hetzner sync is functional and non-destructive for the first time.

---

## Phase 5: User Story 2 - AWS sync respects region boundaries (Priority: P1)

**Goal**: Stop a single-region sync from destroying other regions' entries.

**Independent Test**: Register instances in `us-west-2` and `eu-central-1`, sync `eu-central-1`, verify the `us-west-2` entries survive untouched and appear in no report category.

### Implementation for User Story 2

- [X] T031 [US2] Replace the single-shot `describe_instances` in `src/remo_cli/providers/aws.py` (currently lines 721-731) with `ec2.get_paginator("describe_instances").paginate(...)`, returning the pages gathered plus a `complete` flag that is `False` if iteration ends early
- [X] T032 [US2] Add `_probe(scope, include_all)` to `src/remo_cli/providers/aws.py` that filters **on instance state only, never on `tag:remo`** (FR-044), setting `marked` from `tags.get("remo") == "true"` locally, and deriving the registry name from the `remo_resource_name` tag with the `Name`-minus-`remo-` fallback (research R8)
- [X] T033 [US2] Rewrite `sync()` in `src/remo_cli/providers/aws.py` to build a `SyncScope(type="aws", region=_effective_region(region))`, delegate to `run_sync`, accept `auto_confirm`/`dry_run`, and return `int`
- [X] T034 [US2] Add `--yes/-y` and `--dry-run` options plus `sys.exit(rc)` to the sync command in `src/remo_cli/cli/providers/aws.py`

### Tests for User Story 2

- [X] T035 [P] [US2] Write probe and region-scoping tests in `tests/unit/providers/test_aws_sync.py`, including an entry with an empty region that is matched-and-stamped but never proposed for removal, and an untagged but live instance that is retained rather than removed (FR-044, SC-015)
- [X] T036 [P] [US2] Add a `get_paginator` stub to the `ec2` fixture in `tests/unit/providers/test_aws_snapshot.py` so the existing suite keeps passing
- [X] T037 [US2] Add a two-region integration test to `tests/integration/test_sync_reconcile.py` asserting out-of-region entries survive, retain their recorded region, and appear in no report category

**Checkpoint**: The AWS region wipe is fixed.

---

## Phase 6: User Story 3 - Stopped instances survive sync (Priority: P1)

**Goal**: Stop `remo aws stop` followed by any sync from destroying the thing that was stopped.

**Independent Test**: Register an instance, report it `stopped` with no `PublicIpAddress`, sync — entry retained with region and last-known address intact, annotated `(stopped)`, and nothing about state written to `registry.json`.

### Implementation for User Story 3

- [X] T038 [US3] Set the state filter in the AWS probe in `src/remo_cli/providers/aws.py` to `pending`, `running`, `stopping`, `stopped` — matching the list every other AWS command already passes to `_find_remo_instance` — excluding only `shutting-down` and `terminated` (FR-017)
- [X] T039 [US3] Set `entry.host` in the AWS probe in `src/remo_cli/providers/aws.py` to the public IP or `""`, never falling back to the instance id, so `merge_entry` preserves the last known address for a stopped instance (FR-018)
- [X] T040 [US3] Populate `DiscoveredHost.state` from `instance["State"]["Name"]` in the AWS probe in `src/remo_cli/providers/aws.py`
- [X] T041 [US3] Render non-running state annotations inline on entry names in `render_plan` in `src/remo_cli/core/reconcile.py` (FR-019)

### Tests for User Story 3

- [X] T042 [US3] Add stopped-instance tests to `tests/unit/providers/test_aws_sync.py` using a realistic stopped-instance payload with the `PublicIpAddress` key absent, plus a `terminated` instance that correctly lands in removals
- [X] T043 [US3] Add a stopped-instance integration test to `tests/integration/test_sync_reconcile.py` asserting the entry is retained, the region and address survive, and `registry.json` contains no state field

**Checkpoint**: All three named bugs are fixed. Every P1 story is complete.

---

## Phase 7: User Story 4 - Proxmox and cross-provider uniformity (Priority: P2)

**Goal**: Bring the last provider onto the shared engine and prove all four behave identically.

**Independent Test**: Run sync against each of the four providers with a mixed plan and verify identical output structure, flags, and counts.

### Implementation for User Story 4

- [X] T044 [US4] Add a return-code check to `_read_tags_by_vmid` in `src/remo_cli/providers/proxmox.py` (currently lines 145-180) raising `ProbeError` on non-zero — today an SSH failure silently yields an empty tag map, so every container reads unmarked and the node's entries are wiped
- [X] T045 [US4] Add `_probe(scope, user, use_ip, include_all)` to `src/remo_cli/providers/proxmox.py` combining `pct list` and the tag map, with `complete=True` and `ProbeError` on any listing failure
- [X] T046 [US4] Rewrite `sync()` in `src/remo_cli/providers/proxmox.py` to delegate to `run_sync` with `SyncScope(type="proxmox", host=host)` and return `int`
- [X] T047 [US4] Delete the now-false unmarked-drop warning from `src/remo_cli/providers/proxmox.py` (FR-026)
- [X] T048 [US4] Add `--yes/-y` and `--dry-run` options plus `sys.exit(rc)` to the sync command in `src/remo_cli/cli/providers/proxmox.py`

### Tests for User Story 4

- [X] T049 [P] [US4] Write probe tests in `tests/unit/providers/test_proxmox_sync.py` patching `remo_cli.providers.proxmox._run_on_node`, explicitly covering the SSH-failure case that must now raise instead of reporting zero marked containers
- [X] T050 [P] [US4] Update `tests/unit/cli/providers/test_proxmox_sync_all.py` for the `int` return type and the new flags
- [X] T051 [US4] Add cross-provider tests to `tests/integration/test_sync_reconcile.py` asserting identical report structure across all four providers, the scope-boundary text (FR-045), idempotency (second run is a no-op that does not prompt), and exactly one registry write per run
- [X] T052 [US4] Update the pinned per-provider registry shapes in `tests/unit/providers/test_provider_registry_entries.py`, deliberately revising the AWS pin where `host` no longer falls back to the instance id

**Checkpoint**: All four providers share one engine and one contract.

---

## Phase 8: User Story 5 - Adoption parity (Priority: P3)

**Goal**: Give AWS and Hetzner the `--all` adoption flag, and complete the Hetzner label story with a backfill path.

**Independent Test**: With an unmarked instance present, sync without the flag skips it with a hint; with the flag adopts it and states the criteria. An unlabelled Hetzner server gains the label via `update`, and re-running reports no change.

### Implementation for User Story 5

- [X] T053 [US5] Add `include_all` support to the AWS probe in `src/remo_cli/providers/aws.py`, widening **eligibility for addition** to instances whose `Name` tag matches `remo-*`, and setting `adoption_criteria`; the query itself is unchanged, since it already enumerates untagged instances (FR-044)
- [X] T054 [US5] Add the `--all` option to the sync command in `src/remo_cli/cli/providers/aws.py`
- [X] T055 [P] [US5] Add `include_all` support to the Hetzner probe in `src/remo_cli/providers/hetzner.py`, widening eligibility for addition to every server in the project (no naming convention exists to filter on, per research R7) and setting `adoption_criteria` to say so plainly
- [X] T056 [US5] Add the `--all` option to the sync command in `src/remo_cli/cli/providers/hetzner.py`
- [X] T057 [US5] Add `_apply_managed_label(server_name)` to `src/remo_cli/providers/hetzner.py` that reads the server's current labels, merges `remo: "true"`, and `PUT`s the merged map — a blind `PUT` replaces labels wholesale and would violate FR-034; return `(ok, err)` and never raise, mirroring `_apply_managed_marker` in the other providers
- [X] T058 [US5] Call `_apply_managed_label` from `update()` in `src/remo_cli/providers/hetzner.py`, warning on failure without failing the update, mirroring `providers/incus.py:421-428`

### Tests for User Story 5

- [X] T059 [P] [US5] Add adoption tests to `tests/unit/providers/test_aws_sync.py` covering skipped-without-flag with hints, adopted-with-flag with criteria printed, and that the flag does not change what the query enumerates
- [X] T060 [P] [US5] Add adoption tests to `tests/unit/providers/test_hetzner_sync.py` covering the same three cases
- [X] T061 [P] [US5] Write label backfill tests in `tests/unit/providers/test_hetzner_label.py` asserting idempotency (no change when already labelled) and that an unrelated pre-existing label such as `env: prod` survives the merge
- [X] T062 [US5] Add a durability test to `tests/integration/test_sync_reconcile.py` proving an entry adopted via `--all` is retained by a subsequent plain sync, reported as unmarked, with no prompt — the regression guard for the marker-semantics change

**Checkpoint**: All five user stories complete.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T063 Update `README.md`: add `--yes`/`--dry-run`/`--all` to the sync command reference (lines 291, 300, 313-314, 324-325) and rewrite the troubleshooting prose (lines 405-424) for the new marker semantics, deleting the claim that a later default sync drops `--all`-adopted entries
- [X] T064 [P] Update the sync section in `docs/proxmox.md` (lines 64-65), replacing "rebuild known_hosts entries" — which describes the behaviour being removed — with the reconcile semantics, and note the node-only enumeration boundary
- [X] T065 [P] Add a Sync section to `docs/incus.md` under CLI Commands, covering scope, the confirmation gate, `--yes`, `--dry-run`, `--all`, and the default-project enumeration boundary
- [X] T066 [P] Add a Sync section to `docs/hetzner.md` covering the same, plus the new `remo` label applied at creation and the `update` backfill path
- [X] T067 [P] Update the IAM permissions table in `docs/aws.md` (line 218) to reflect that sync now describes all non-terminal instances in the region and reads tags
- [X] T068 Verify no stale copy remains by grepping `src/` for "will drop those unmarked" and "rebuild known_hosts", and confirm both return nothing
- [X] T069 [P] Update `CLAUDE.md` Active Technologies and Recent Changes with the 016-sync-reconcile entry
- [X] T070 Run the full validation from `quickstart.md` including the exit-code checks (0 for `--dry-run`, 2 for a bad flag, 3 for a declined removal)
- [X] T071 Run `uv run pytest`, `uv run mypy src/remo_cli`, and `uv run ruff check src/remo_cli` and compare against the T001 baseline

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**
- **Phase 3 (US1, Incus)**: depends on Phase 2
- **Phase 4 (Hetzner)**: depends on Phase 2. Independent of Phase 3, though doing Incus first settles the contract on a simpler provider
- **Phase 5 (US2, AWS region)**: depends on Phase 2
- **Phase 6 (US3, AWS stopped)**: depends on Phase 5's AWS probe (T032). T041 edits `core/reconcile.py`
- **Phase 7 (US4, Proxmox)**: depends on Phase 2; T051 additionally needs Phases 3–6 for the cross-provider assertions
- **Phase 8 (US5)**: T053–T054 need Phase 5; T055–T058 need Phase 4
- **Phase 9 (Polish)**: depends on all shipped stories

### Within Each User Story

- Provider probe before the `sync()` rewrite before the CLI flags
- `core/` before `providers/` before `cli/`
- Implementation before that story's tests, except where a test pins existing behaviour about to change (T036, T052)

### Parallel Opportunities

- **Phase 2**: none — all tasks share two files
- **Phase 3**: T021, T022 (different test files)
- **Phase 4**: T028 (Ansible) is independent of the whole Python chain; T029 pairs with it
- **Phase 5**: T035, T036 (different test files)
- **Phase 7**: T049, T050 (different test files)
- **Phase 8**: T055 with T053; T059, T060, T061 (three different test files)
- **Phase 9**: T064, T065, T066, T067, T069 (five different documents)
- **Across phases**: once Phase 2 lands, Phases 3, 4, 5 and 7 touch disjoint provider modules and can be staffed concurrently

---

## Parallel Example: after Phase 2

```bash
# Developer A — Incus MVP (Phase 3)
Task: "Add _probe() to src/remo_cli/providers/incus.py"

# Developer B — Hetzner critical path (Phase 4), concurrently
Task: "Add _hetzner_api_paged() to src/remo_cli/providers/hetzner.py"
Task: "Add labels: {remo: 'true'} to ansible/roles/hetzner_server/tasks/main.yml"

# Developer C — AWS (Phase 5), concurrently
Task: "Replace describe_instances with get_paginator in src/remo_cli/providers/aws.py"
```

---

## Implementation Strategy

### MVP First (Phases 1–3)

1. Setup → Foundational → User Story 1
2. **STOP and VALIDATE**: the empty-result wipe is fixed for Incus, with consent, dry-run, and correct exit codes
3. Shippable on its own — it fixes the failure mode common to all providers, for one of them

### Incremental Delivery

1. Phases 1–2 → engine ready, nothing user-visible
2. Phase 3 → **MVP**: no silent deletion (Incus)
3. Phase 4 → the unconditional Hetzner wipe is stopped; `hetzner sync` works for the first time
4. Phase 5 → AWS region wipe fixed
5. Phase 6 → AWS stopped-instance wipe fixed; **all three named bugs closed**
6. Phase 7 → all four providers uniform
7. Phase 8 → adoption parity and the Hetzner backfill path
8. Phase 9 → documentation truthful again

### Minimum Risk-Reduction Set

If only part of this ships, Phases 1–4 remove every unconditional data-loss path: the engine, the consent gate, and the one provider whose bug fires on every invocation. Phases 5–6 then close the two conditional AWS paths.

---

## Notes

- `[P]` marks tasks in different files with no incomplete dependencies
- Every task names the exact file it touches
- Commit after each task or logical group
- Stop at any checkpoint to validate a story independently
- Constitution Principle II is the binding gate: the consent gate's five outcomes, `complete` True/False, and `marked` True/False must each be exercised, not sampled
