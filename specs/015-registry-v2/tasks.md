# Tasks: Versioned Structured Host Registry (Registry v2)

**Input**: Design documents from `/specs/015-registry-v2/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: INCLUDED — the spec mandates automated verification (SC-001 migration matrix, SC-005 concurrency stress, SC-006 skew matrix; quickstart scenarios reference the test files).

**Organization**: Grouped by user story (US1–US5 from spec.md) after shared Setup/Foundational phases.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US5 (user-story phases only)

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Test scaffolding and path plumbing every later task uses.

- [X] T001 Add registry test fixtures to tests/conftest.py: isolated `REMO_HOME` tmp-dir fixture, legacy known_hosts line builder (all 5 types, 4/6/7-field variants), and v2 registry.json builder matching contracts/registry-file-v2.md
- [X] T002 Add `get_registry_path()`, `get_registry_path_readonly()`, `get_registry_backup_path()`, and `get_registry_lock_path()` beside the existing known_hosts path helpers in src/remo_cli/core/config.py (readonly variants must not mkdir)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The `core/registry.py` accessor skeleton — codec, validation, read/write pipeline — that every user story builds on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Create src/remo_cli/core/registry.py with the error taxonomy (`RegistryError`, `RegistryReadError`, `RegistryValidationError`, `RegistryBusyError`, `RegistryNewerVersionError`) and the frozen `RegistryView` dataclass per contracts/registry-accessor-api.md — no `SystemExit` anywhere in the module
- [X] T004 Implement the v2 serializer in src/remo_cli/core/registry.py: KnownHost → hostEntry mapping (data-model.md §3), entries sorted by (type, name), sorted keys, 2-space indent, trailing newline, same-directory temp file + `os.replace` atomic write
- [X] T005 Implement the v2 parser in src/remo_cli/core/registry.py: hostEntry → KnownHost mapping with normalized in-memory `access_mode` ("direct"/"ssm"), `version > 2` → `RegistryNewerVersionError`, per-entry tolerant parse → `RegistryView.warnings`, unknown-type entries preserved verbatim for re-emission (FR-014)
- [X] T006 Implement the legacy codec in src/remo_cli/core/registry.py: tolerant colon-line parse (reusing `KnownHost.from_line`) plus the legacy→v2 mapping table keyed on type FIRST with the SSM classification rule (data-model.md §4, research R5) — this single implementation later serves migration AND payload-v1 mapping
- [X] T007 Implement validation rules V1–V6 (data-model.md §5) as a `validate_hosts()` helper in src/remo_cli/core/registry.py; failure raises `RegistryValidationError` naming field + entry
- [X] T008 Implement `read_registry(readonly=...)` in src/remo_cli/core/registry.py: resolution order registry.json → legacy → empty (file-state table, data-model.md §6), readonly mode using the no-mkdir path helpers with zero side effects (FR-011/FR-013); migration trigger left as a stub for US1
- [X] T009 Implement `mutate_registry(mutator)` and `replace_registry(hosts, allow_empty=False)` in src/remo_cli/core/registry.py: read → apply → validate → atomic write, unknown-type entries passed through untouched (locking arrives in US4; keep the lock call site as a no-op seam)
- [X] T010 [P] Foundational unit tests in tests/unit/core/test_registry_format.py: v2 round-trip fidelity, deterministic serialization (byte-identical re-serialize), unknown-type preservation, newer-version rejection with file untouched, tolerant read warnings, validation rule rejections V2–V6

**Checkpoint**: Accessor reads/writes v2 and reads legacy in memory — no consumer wired yet, no files change shape for users.

---

## Phase 3: User Story 1 - Existing User Upgrades Seamlessly (Priority: P1) 🎯 MVP

**Goal**: Lazy, lossless, idempotent CLI migration; all CLI/provider call sites routed through the accessor; after this phase the system runs on v2 end-to-end.

**Independent Test**: quickstart.md §2 — populate a legacy file with all 5 types + a garbage line, run `remo incus list`, verify the one-time notice, valid registry.json, byte-identical `known_hosts.v1.bak`, and silent idempotent re-runs.

### Implementation for User Story 1

- [X] T011 [US1] Implement `migrate_if_needed()` + `MigrationReport` in src/remo_cli/core/registry.py: write-v2-then-rename ordering, `known_hosts.v1.bak` with non-clobbering numeric suffixes, skipped-lines capture, idempotent no-op when registry.json exists (FR-007/009/010, research R6)
- [X] T012 [US1] Implement both-present resolution in src/remo_cli/core/registry.py: host-set equivalence → silently complete the rename (S3→S2); divergence → v2 wins + warning, never merge (S4, FR-024)
- [X] T013 [US1] Convert src/remo_cli/core/known_hosts.py public functions (`get_known_hosts`, `save_known_host`, `remove_known_host`, `clear_known_hosts_by_type`, `clear_known_hosts_by_prefix`) into thin delegates to the accessor per contracts/registry-accessor-api.md, deleting the module's internal line-parsing/atomic-write code; resolver/guard helpers (`resolve_remo_host_by_name`, `guard_not_added_ssh_host`, `get_aws_region`) unchanged in behavior over `read_registry().hosts`
- [X] T014 [US1] Surface `MigrationReport` through the delegates at the CLI boundary using src/remo_cli/core/output.py: plain-language notice with migrated count, backup name, skipped lines verbatim, and the one-time "next `remo web push` will re-verify all instances" note (FR-025/FR-026)
- [X] T015 [P] [US1] Migration matrix tests in tests/unit/core/test_registry_migration.py: all 5 types × 4/6/7-field combos, garbage lines, unknown types preserved, empty vs missing file, pre-existing backup suffixing, interrupted-migration convergence (S3), divergent both-present warning (S4), completed migration never re-runs — matrix MUST include the two legacy access-mode variants that no current writer produces but old files can contain: a non-AWS line with literal `ssm` in the access-mode slot (the `to_line` back-fill quirk) and a 7-field line with an empty access-mode slot; both map to `access: "direct"` via the type-first rule (quickstart §2 `old/…` lines)
- [X] T016 [P] [US1] Provider save-path fixture tests in tests/unit/providers/test_provider_registry_entries.py: pin the exact KnownHost field usage of each provider save call (providers/incus.py, proxmox.py, aws.py, hetzner.py, added.py) and assert the legacy→v2 mapper classifies each correctly — the research R5 risk pin (especially the `to_line` implicit-SSM quirk vs incus/proxmox `instance_id` overloads)
- [X] T017 [US1] Update existing tests that fabricate legacy known_hosts files (grep tests/ for `known_hosts` fixtures, e.g. tests/unit/test_host_model.py, tests/unit/web/, tests/integration/test_web_adopt_e2e.py) to run against the accessor era: legacy fixtures stay valid as migration inputs; assertions move to v2 where they check written output

**Checkpoint**: MVP — an upgraded user's first command migrates losslessly; everything runs on v2. Two scope caveats: (1) FR-017 (advisory locking) is deliberately deferred to Phase 6 — writes here are as-unlocked-as-today; (2) this checkpoint is shippable ONLY for CLI-only usage — a deployment running the web service must also take US3's T021/T023, because migration renames the legacy file the pre-US3 web readers look for (see Dependencies).

---

## Phase 4: User Story 2 - Registry Values Can No Longer Corrupt the File (Priority: P2)

**Goal**: Any legitimate value (IPv6, colon-containing paths) round-trips; invalid values are rejected pre-write with named-field errors.

**Independent Test**: quickstart.md §4 — `remo add v6box 2001:db8::7 --user admin` round-trips intact; invalid entries are rejected leaving the file unchanged.

### Implementation for User Story 2

- [X] T018 [US2] Wire `validate_hosts()` into every write path (delegates in src/remo_cli/core/known_hosts.py and `replace_registry` in src/remo_cli/core/registry.py) asserting reject-before-write leaves disk untouched (FR-016); delete any CLI-side colon-safety workarounds made obsolete by the format (check src/remo_cli/core/validation.py and providers/added.py)
- [X] T019 [P] [US2] Value-fidelity tests extending tests/unit/core/test_registry_format.py: IPv6 literals, colon-containing identity paths, spaces/special characters, boundary-length names — byte-identical round-trips; rejection-message tests assert field + entry are named
- [X] T020 [US2] Fix `remo add` TARGET parsing for IPv6 in src/remo_cli/providers/added.py (today `rest.partition(":")` splits an IPv6 literal at its first colon, failing before the registry is involved): accept OpenSSH-style bracket form `[user@][v6][:port]` and treat a bare bracket-less TARGET containing multiple colons as a host with no port suffix; then end-to-end IPv6 added-host test in tests/unit/providers/test_added_ipv6.py — add both forms, list, connect argv built correctly (`build_ssh_base_cmd` receives the intact literal)

**Checkpoint**: US1 + US2 — the data-loss bug class is closed.

---

## Phase 5: User Story 3 - One Registry Reader for CLI and Web Service (Priority: P3)

**Goal**: The three parser implementations collapse into the accessor; the web service reads both formats in place with zero side effects.

**Independent Test**: quickstart.md §6 — web check against a read-only volume in each format shows the same host set as the CLI, no writes, malformed entry degrades to a warning; grep finds no private registry parsing under src/remo_cli/web/.

### Implementation for User Story 3

- [X] T021 [US3] Replace `_read_known_hosts_readonly()` in src/remo_cli/web/discovery.py with `registry.read_registry(readonly=True)`; per-entry warnings logged, structural errors surfaced as the existing degraded behavior
- [X] T022 [P] [US3] Replace `_read_registry_readonly()` in src/remo_cli/web/api/setup.py (read/status path only — PUT payload changes are US5) with `registry.read_registry(readonly=True)`
- [X] T023 [P] [US3] Update the registry probe in src/remo_cli/web/state.py to accept registry.json OR legacy known_hosts (either present ⇒ registry present) and map `RegistryNewerVersionError`/`RegistryReadError` to the `broken` state (data-model.md §6)
- [X] T024 [US3] Update src/remo_cli/web/check.py to report which registry format was found and emit the newer-version remediation text on `RegistryNewerVersionError` (FR-025)
- [X] T025 [P] [US3] Web readonly tests in tests/unit/web/test_registry_readonly.py: both formats on a chmod-555 REMO_HOME (no writes/mkdirs — assert mtimes), CLI-vs-web host-set parity, single malformed entry degrades per-entry, broken-state mapping for newer-version files

**Checkpoint**: SC-003 achieved — one parser; web behavior identical across formats.

---

## Phase 6: User Story 4 - Concurrent Writers Don't Lose Entries (Priority: P4)

**Goal**: Advisory locking serializes all read-modify-write sequences; bounded wait; graceful degradation; crash atomicity proven.

**Independent Test**: quickstart.md §5 — multiprocess writers always converge to the union; a held lock produces "registry busy" after ~5 s; SIGKILL mid-write leaves the previous complete state.

### Implementation for User Story 4

- [X] T026 [US4] Implement `registry_lock(timeout_s=5.0)` in src/remo_cli/core/registry.py: `fcntl.flock(LOCK_EX|LOCK_NB)` on the sidecar lock file, 50 ms retry loop, `RegistryBusyError` with plain-language message on timeout, one-time warning + unlocked fallback on `ENOLCK`/`EOPNOTSUPP` (FR-017/FR-019, research R3)
- [X] T027 [US4] Wrap `mutate_registry`, `replace_registry`, and `migrate_if_needed` in `registry_lock` (filling the T009/T011 no-op seam), including the re-check-after-acquire so concurrent first-runs and concurrent migrations converge (FR-010/FR-017)
- [X] T028 [P] [US4] Lock unit tests in tests/unit/core/test_registry_locking.py: timeout → `RegistryBusyError` at ~5 s, monkeypatched flock `OSError` → one-time warning + unlocked proceed, kill-mid-write leaves complete prior state (atomicity, FR-018)
- [X] T029 [US4] Multiprocess stress test in tests/integration/test_registry_concurrency.py: N processes upserting disjoint entry sets in a loop → final union always intact, file parses on every iteration (SC-005)

**Checkpoint**: Lost-update race class closed for CLI-and-service cohabitation.

---

## Phase 7: User Story 5 - Workstation and Web Service on Different Versions (Priority: P5)

**Goal**: Payload v2 with fail-fast version negotiation; upgraded service accepts v1 payloads; delta cache resets once after migration.

**Independent Test**: quickstart.md §7 — the compatibility matrix in contracts/mirror-payload-v2.md §4 passes row by row.

### Implementation for User Story 5

- [X] T030 [US5] Update `PUT /api/v1/setup/registry` in src/remo_cli/web/api/setup.py: accept payload v1 AND v2 (Pydantic models per contracts/mirror-payload-v2.md §2), map v1 through the accessor's legacy mapper (T006), store v2 via `replace_registry`, remove any legacy mirror file as apply step 3, return 400 `unsupported_payload_version` (mirror intact) for unknown versions; replace the legacy colon/newline field checks with V2–V6 validation for v2 payloads (FR-020/021/022)
- [X] T031 [US5] Add `payload_versions: [1, 2]` to `GET /api/v1/setup/status` in src/remo_cli/web/api/setup.py (contracts/mirror-payload-v2.md §1)
- [X] T032 [US5] Update the push flow in src/remo_cli/core/web_adopt.py: build payload v2 from v2 entries; read `payload_versions` from status (absent ⇒ [1]) and abort BEFORE any keyscan/authorize/PUT with the upgrade-the-service remediation when 2 is unsupported (FR-021, fail truly fast)
- [X] T033 [US5] Implement push-cache v2 in src/remo_cli/core/web_adopt.py: `cache_version: 2` field, fingerprint = SHA-256 of canonical sorted-key JSON of the v2 entry, any other/missing cache_version ⇒ empty cache (one-time full re-verification push, FR-026, research R10)
- [X] T034 [P] [US5] Payload contract tests in tests/integration/test_setup_payload_versions.py: v1 accepted → stored as v2 + legacy mirror removed; v2 accepted with schema-exact entries; v3 → 400 with mirror intact and served; missing `payload_versions` → workstation aborts pre-keyscan with remediation; stale cache_version ⇒ full re-verify, idempotent on immediate re-push (SC-006)

**Checkpoint**: All five stories complete; every skew combination lands on the matrix.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T035 [P] Performance regression test in tests/perf/test_registry_perf.py: generated 200-entry registry read+validate+write round-trip < 100 ms (SC-008, research R11)
- [X] T036 [P] Documentation sync (Constitution V): README registry/format section, docs/web-session-interface.md adoption payload examples → v2, CLAUDE.md Active Technologies line ("Flat file (colon-delimited)" → versioned registry.json) and registry references
- [X] T037 Execute quickstart.md scenarios 1–8 end-to-end in a scratch REMO_HOME; fix any drift between quickstart commands and reality
- [X] T038 Full gates: `uv run pytest`, `uv run mypy src/remo_cli`, `uv run ruff check src/remo_cli` — all green

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: needs T001–T002; **blocks all user stories**
- **US1 (Phase 3)**: needs Phase 2. Delivers the MVP; T013 (delegates) is the single riskiest wiring task — land T011/T012 before it so migration exists when call sites route through the accessor
- **US2 (Phase 4)**: needs Phase 2 + T013 (write paths routed). Otherwise independent of US1's migration internals
- **US3 (Phase 5)**: needs Phase 2 only (readonly read path) — can run in parallel with US1/US2 apart from merge conflicts in setup.py (none: US3 touches read path, US5 touches PUT). **Release-blocking caveat**: T021 (discovery) and T023 (state probe) MUST land before any release that includes US1's migration if the deployment uses the web service — post-migration, the pre-US3 web readers look only at the legacy path and would see an empty registry
- **US4 (Phase 6)**: needs T009/T011 (the seams it fills). Independent of US2/US3/US5
- **US5 (Phase 7)**: needs T006 (legacy mapper) + T009 (`replace_registry`); T032/T033 also touch core/web_adopt.py sequentially (same file — not [P] with each other)
- **Polish (Phase 8)**: after all desired stories

### Story completion order (sequential solo path)

US1 → US2 → US3 → US4 → US5 (priority order). Each checkpoint is independently shippable.

### Parallel Opportunities

- Phase 2: T010 in parallel with the tail of T007–T009 (different files)
- US1: T015 + T016 in parallel (different test files) once T011–T013 exist
- US3: T022 + T023 + T025 in parallel; T021 and T024 sequential only against their own files
- Cross-story (if parallelized): US3 (web read path) alongside US1/US2 (CLI write path) — disjoint files except tests/conftest.py
- US5: T030 and T031 are sequential (same file: web/api/setup.py); T034 parallel once T030–T033 exist

## Parallel Example: User Story 1

```bash
# After T011–T014 land, run both test-authoring tasks concurrently:
Task: "Migration matrix tests in tests/unit/core/test_registry_migration.py"
Task: "Provider save-path fixture tests in tests/unit/providers/test_provider_registry_entries.py"
```

## Implementation Strategy

**MVP first (US1)**: Phases 1–3 produce a shippable increment for CLI-only usage — users migrate losslessly and run on v2. If the release includes the web service, US3's T021/T023 are part of the MVP (see the US3 release-blocking caveat above). STOP and validate quickstart §2–§3 before proceeding.

**Incremental delivery**: each subsequent story closes one risk class (US2 data loss → US3 parser drift → US4 races → US5 skew) and each ends at a runnable checkpoint. US4's locking and US5's payload work are the most isolated — good candidates to defer if the branch needs to ship early, since their absence matches today's behavior (unlocked writes, v1 payloads).

**Notes**: commit after each task or logical group; T013 and T030 are the two tasks most likely to surface hidden call-site assumptions — run the full suite immediately after each.
