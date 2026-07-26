---
description: "Task list for feature: Simplify Web Adoption & Close the Lifecycle"
---

# Tasks: Simplify Web Adoption & Close the Lifecycle

**Input**: Design documents from `/specs/017-web-adopt-simplify/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included. This codebase tests conditional paths extensively and Constitution Principle II ("Test All Conditional Paths") requires it; each new branch has a corresponding test task.

**Organization**: Grouped by user story (priority order) so each story is an independently testable increment. Story priorities from spec.md: US1 (P1); US2, US3, US6 (P2); US4, US5 (P3).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US6 for story-phase tasks; Setup/Foundational/Polish carry no story label
- Exact file paths are included in every task

## Path Conventions

Single Python package, three-layer architecture: `src/remo_cli/{cli,core,providers,web,models}/`, tests under `tests/{unit,integration}/`. Paths below are repo-root-relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm baseline before changing shared modules.

- [X] T001 Confirm the working tree is on branch `017-web-adopt-simplify` and the baseline is green: run `uv run pytest -q`, `uv run mypy src/remo_cli`, `uv run ruff check src/remo_cli` and record any pre-existing failures so they are not attributed to this feature.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The push-cache v3 format upgrade is shared by US1 (seeding), US2 (offline diff / nudge), US3 (revocation connection tuple), and US5 (mirror generation). It MUST land before those stories.

**⚠️ CRITICAL**: No user-story work that reads or writes the push cache can begin until this phase is complete.

- [X] T002 Extend `CachedInstance` in `src/remo_cli/core/web_adopt.py` with the non-secret connection tuple fields `host: str`, `user: str`, `access: str`, `type: str`, `port: int | None` (defaults preserving backward-lenient parse), per data-model.md §1.
- [X] T003 Bump `PUSH_CACHE_VERSION` 2 → 3 in `src/remo_cli/core/web_adopt.py` and change the per-deployment on-disk shape to `{ "mirror_generation": int, "instances": { name -> entry } }`; update `load_push_cache`, `save_push_cache`, and `_parse_instances` to read/write the nested shape and the new `CachedInstance` fields, treating any `cache_version != 3` file as empty (existing graceful-degradation behavior), per data-model.md §2 and research.md R7.
- [X] T004 Update `_cache_from_outcomes` in `src/remo_cli/core/web_adopt.py` to populate the connection tuple (host/user/access/type/port) for each `adopted`/`unchanged` direct-access instance, and thread the per-deployment `mirror_generation` through `_update_push_cache` so it is preserved across writes.
- [X] T005 [P] Update `tests/unit/core/test_web_push.py` cache round-trip tests for `cache_version: 3`: nested `instances` + `mirror_generation`, connection-tuple persistence, and the "v2/unversioned file treated as empty" degradation case.

**Checkpoint**: Push cache v3 reads/writes cleanly and older caches degrade to empty. User stories can now begin.

---

## Phase 3: User Story 1 - One command to connect and re-sync (Priority: P1) 🎯 MVP

**Goal**: A single `remo web push` that adopts on first use and re-syncs afterward, via one code path; `remo web adopt` becomes a deprecated alias.

**Independent Test**: Against a fresh deployment, one push adopts it; a second push re-syncs (unchanged instances skipped). `git grep _adopt_flow src/` returns nothing.

### Tests for User Story 1

- [X] T006 [P] [US1] Update `tests/integration/test_web_adopt_e2e.py` to drive the unified `run_push` for both first-push (empty cache → full adoption) and re-sync (populated cache → `unchanged`), asserting one summary format for both.
- [X] T007 [P] [US1] In `tests/unit/cli/test_web_adopt_cmd.py`, assert `remo web adopt` prints a deprecation warning and delegates to `run_push` (same result as `remo web push`), and that `remo web push --help` documents the unified adopt-or-resync behavior. Add a trust-invariant assertion for the unified path (SC-009 / FR-004): no personal key is ever placed in the pushed payload or authorized on any instance, and only the single `remo-web@<deployment>` marker line is installed.

### Implementation for User Story 1

- [X] T008 [US1] Merge the two flows in `src/remo_cli/core/web_adopt.py`: delete `_adopt_flow`, make `_push_flow` the single orchestrator, and repoint `run_adopt` to call the same flow (or fold `run_adopt` into `run_push`), preserving the status precheck, payload-version gate, per-instance loop, PUT, verify, and cache-seed steps (research.md R1; contracts/cli-web-push.md).
- [X] T009 [US1] Update `src/remo_cli/cli/web.py`: turn the `adopt` command body into a thin deprecated alias that emits a one-line `print_warning` and calls `run_push`; refresh the `push` command help to describe first-push-adopts behavior; keep `--via`/`--allow-empty`/`--yes`/`--token`/URL resolution identical across both.
- [X] T010 [US1] Update `tests/integration/test_web_cli_parity.py` if it references the removed `_adopt_flow`/`run_adopt` split, so parity is asserted against the single path.

**Checkpoint**: `remo web push` adopts-or-resyncs through one path; `adopt` is a deprecated alias. MVP is shippable.

---

## Phase 4: User Story 2 - Offline drift status + out-of-date nudge (Priority: P2)

**Goal**: `remo web status` reports registry-vs-cache drift with zero network, and every registry-mutating command nudges when a push cache exists.

**Independent Test**: After a push, add/change/remove one instance each; offline `remo web status` reports 1 new / 1 changed / 1 removed in < 2s. Each of create/destroy/sync/add/remove prints the nudge when a cache exists and nothing when it does not.

### Tests for User Story 2

- [X] T011 [P] [US2] Create `tests/unit/core/test_web_drift.py`: `diff_registry_against_cache` classification (new/changed/removed/in_sync), `select_deployment` (implicit single, explicit-required multi, error listing ids), and `out_of_date_notice` gating (non-None iff cache exists and is non-empty).
- [X] T012 [P] [US2] Extend `tests/integration/test_web_cli_parity.py` to assert the nudge fires on successful `create`, `destroy`, `sync` (apply path), `add`, `remove`, and is absent with no cache / on dry-run sync / on `aws stop|start|reboot`.

### Implementation for User Story 2

- [X] T013 [US2] Create `src/remo_cli/core/web_drift.py` (stdlib + `core`/`models` only — must import without the `web` extra) with `InstanceDrift`, `DriftReport`, `diff_registry_against_cache(hosts, cached_instances)` (reusing `instance_fingerprint`), `select_deployment(cache, selector)`, `render_drift(report)`, and `out_of_date_notice() -> str | None`, per data-model.md §4 and contracts/cli-web-status.md.
- [X] T014 [US2] Add the `status` command to `src/remo_cli/cli/web.py` (core-only imports): load cache → select deployment (implicit/one, `--deployment` required for many) → diff → `render_drift`; explicit "no prior push" and "in sync — nothing to push" outcomes; exit 1 only on ambiguous multi-deployment selection.
- [X] T015 [US2] Emit the nudge on successful registry apply in `src/remo_cli/core/reconcile.py::run_sync` (after `apply_plan`, in the NOT_REQUIRED/APPLY branch only — never on dry-run or abort), calling `web_drift.out_of_date_notice()` and printing it when non-None. (Sync's nudge lives in core, not the CLI layer, because only `run_sync` distinguishes "applied" from "dry-run/no-op"; this mirrors `run_sync` already printing success/warn lines via `core.output`. Depends on T013.)
- [X] T016 [P] [US2] Emit the nudge after successful `create` and `destroy` in `src/remo_cli/cli/providers/incus.py`.
- [X] T017 [P] [US2] Emit the nudge after successful `create` and `destroy` in `src/remo_cli/cli/providers/proxmox.py`.
- [X] T018 [P] [US2] Emit the nudge after successful `create` and `destroy` in `src/remo_cli/cli/providers/hetzner.py`.
- [X] T019 [P] [US2] Emit the nudge after successful `create` and `destroy` in `src/remo_cli/cli/providers/aws.py` (NOT on `stop`/`start`/`reboot`/`info`/`list`).
- [X] T020 [P] [US2] Emit the nudge after successful `add` and `remove` in `src/remo_cli/cli/added.py`.

**Checkpoint**: Operators can see drift offline and are nudged after any registry mutation.

---

## Phase 5: User Story 3 - Best-effort revocation on removal (Priority: P2)

**Goal**: Removing an instance and pushing strips its `remo-web@` authorized_keys line over the operator's SSH, reporting clearly when it can't.

**Independent Test**: Remove a reachable instance and push → summary shows `revoked`, the line is gone, other keys intact. With the instance unreachable → `could_not_revoke`, push still exits 0.

### Tests for User Story 3

- [X] T021 [P] [US3] In `tests/unit/core/test_web_adopt_authorize.py` (or a sibling), test `build_revoke_command` (marker-only removal, missing-file no-op, idempotent, atomic form) and `revoke_service_key` (returns `(False, reason)` on exit 255/timeout/OSError, never raises).
- [X] T022 [P] [US3] In `tests/unit/core/test_web_push.py`, test the removed-instance path: `RevocationOutcome` reported per removed direct-access instance, SSM / no-connection-tuple → `could_not_revoke`, and overall push completion (exit 0) regardless of revocation failures.

### Implementation for User Story 3

- [X] T023 [US3] Add `build_revoke_command()`, `revoke_service_key(host) -> tuple[bool, str]`, and the `RevocationOutcome` dataclass to `src/remo_cli/core/web_adopt.py`, symmetric to the existing authorize helpers (ambient SSH, `BatchMode=yes`, never raises), per contracts/revocation.md and data-model.md §5.
- [X] T024 [US3] In `_push_flow` (`src/remo_cli/core/web_adopt.py`), replace the current manual-revocation warning block with best-effort revocation: for each `removed` name with a cached connection tuple and `access != "ssm"`, reconstruct a `KnownHost`, call `revoke_service_key`, and collect `RevocationOutcome`s; SSM / missing tuple → `could_not_revoke` with remediation.
- [X] T025 [US3] Extend `render_summary` (or add a revocation section) in `src/remo_cli/core/web_adopt.py` to render revocation outcomes alongside adoption outcomes (FR-018), without changing the exit code.

**Checkpoint**: Instance removal revokes service access best-effort; failures are reported, never fatal.

---

## Phase 6: User Story 6 - Bare-metal adopted mode (Priority: P2)

**Goal**: A bare-metal `remo web serve` with a personal `~/.ssh/id_*` and writable `REMO_HOME` is adoptable; Docker read-only-mount deployments still classify `mount_configured`.

**Independent Test**: `test_state.py` — writable home + service keypair + personal key → `adopted`; non-writable home → `mount_configured`; `REMO_WEB_MODE` forces deterministically. (Independent of the web_adopt changes.)

### Tests for User Story 6

- [X] T026 [P] [US6] Extend `tests/unit/web/test_state.py` with the four outcomes: bare-metal-with-personal-key → `adopted`; non-writable `REMO_HOME` → `mount_configured`; `REMO_WEB_MODE=adopted`/`=mount_configured` override wins (subject to `broken` guards); invalid `REMO_WEB_MODE` → fail-fast config error.

### Implementation for User Story 6

- [X] T027 [US6] Add `mode_override` (env `REMO_WEB_MODE`, values `adopted`/`mount_configured`/unset; invalid → config error) and `mirror_meta_path` (= `web_identity_dir / "mirror-meta.json"`) to `WebSettings` in `src/remo_cli/web/config.py`, per data-model.md §7.
- [X] T028 [US6] Rework `detect_state` in `src/remo_cli/web/state.py` (research.md R5, data-model.md §6): honor `REMO_WEB_MODE` after the `broken` guards; remove `_user_identity_present()` from the `mount_configured` trigger so a readable personal key no longer forces it; keep non-writable `REMO_HOME` as the authoritative `mount_configured` signal. Delete or de-wire `_user_identity_present` if unused elsewhere.

**Checkpoint**: Bare-metal adopted operation works; the Docker RO-mount story is unchanged.

---

## Phase 7: User Story 4 - `--force` full re-authorization (Priority: P3)

**Goal**: `remo web push --force` bypasses the fingerprint fast-path and re-scans/re-authorizes every direct-access instance.

**Independent Test**: An instance a normal push reports `unchanged` is re-scanned and re-authorized under `--force`.

**Dependency note**: Edits `_push_flow` / `run_push` in `core/web_adopt.py` and the push command in `cli/web.py` — sequence after US1 (T008/T009) and avoid concurrent edits with US3/US5 in the same file.

### Tests for User Story 4

- [X] T029 [P] [US4] In `tests/unit/core/test_web_push.py`, assert `force=True` sends every direct-access instance through the full keyscan/authorize path (no `unchanged`), while `force=False` preserves the fingerprint skip; per-instance failures stay non-fatal under force.

### Implementation for User Story 4

- [X] T030 [US4] Thread a `force: bool = False` parameter through `run_push`/`run_adopt` and `_push_flow` in `src/remo_cli/core/web_adopt.py`, guarding the "fingerprint matches → `unchanged`" branch with `not force`.
- [X] T031 [US4] Add the `--force` flag to the `push` (and aliased `adopt`) command in `src/remo_cli/cli/web.py` with help text per contracts/cli-web-push.md.

**Checkpoint**: `--force` recovers out-of-band-rebuilt instances.

---

## Phase 8: User Story 5 - Multi-workstation flap detection (Priority: P3)

**Goal**: The deployment reports a mirror-identity marker; a push from workstation B over A's mirror warns before overwriting.

**Independent Test**: Push from A (generation → N); push from B (no local record) warns naming A's last push; consecutive same-workstation pushes and first-ever pushes do not warn.

**Dependency note**: Workstation-side edits touch `_push_flow`/cache in `core/web_adopt.py` — sequence after US1 and coordinate with US3/US4 in that file. Service-side edits are isolated to `web/api/setup.py`.

### Tests for User Story 5

- [X] T032 [P] [US5] In `tests/unit/web/test_setup_api.py`, assert `GET /setup/status` returns `mirror_generation`/`last_push` when mirror-meta exists (and omits them when absent), `PUT /setup/registry` increments the generation and returns it, and a mirror-meta write failure does not fail an otherwise-successful PUT. Assert the marker exposes no secret and no instance contents (FR-027): the status response contains only the generation and the `last_push.at`/`.workstation` descriptor, never key material or registry entries.
- [X] T033 [P] [US5] In `tests/unit/core/test_web_push.py`, test the workstation flap logic table (contracts/setup-status-marker.md): no warning when server gen absent / no cache entry / `server_gen <= cached_gen`; warning when `server_gen > cached_gen`; interactive confirm/abort vs. `--yes` proceed; cached generation updated from the PUT response.

### Implementation for User Story 5 (service side)

- [X] T034 [US5] In `src/remo_cli/web/api/setup.py`, write `mirror-meta.json` (generation+1, `last_push.at`, `last_push.workstation`) atomically as the final step of `_apply_payload`; add `mirror_generation` to the `RegistryApplyResponse` model. Read the optional `workstation` label from the **raw `body` dict** (not the pydantic `AdoptionPayloadV2In`/`V1In` models, which ignore extra fields by default), defaulting to `"unknown"` when absent; store it verbatim as untrusted display text. On mirror-meta write failure log and continue (PUT still succeeds), per contracts/setup-status-marker.md.
- [X] T035 [US5] In `src/remo_cli/web/api/setup.py`, add `mirror_generation` and `last_push` to `SetupStatusResponse` / `get_status`, sourced from `settings.mirror_meta_path` (omit both when the file is absent/unreadable).

### Implementation for User Story 5 (workstation side)

- [X] T036 [US5] In `_push_flow` (`src/remo_cli/core/web_adopt.py`), after reading `GET /setup/status`, compare `status.mirror_generation` to the cached generation for this deployment and, when the mirror advanced elsewhere, warn (naming `last_push.at`/`.workstation`); interactive → confirm/abort, `--yes` → proceed; send this workstation's label (`socket.gethostname()` + user) on the PUT and store the returned `mirror_generation` in the cache.

**Checkpoint**: Cross-workstation overwrites are flagged before they happen.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Documentation consolidation (FR-031 / Constitution V) and full validation.

- [X] T037 [P] Consolidate the adoption docs in `docs/web-session-interface.md` into a single adoption section (remove the separate adopt vs. push sections) covering the unified push, `remo web status`, the out-of-date nudge, best-effort revocation, `--force`, multi-workstation flap detection, and the `REMO_WEB_MODE` override.
- [X] T038 [P] Update `CLAUDE.md` "Recent Changes" and the relevant "Active Technologies" / structure notes to describe 017 (unified push, `web_drift.py`, cache v3, mirror-meta marker, mode-detection fix).
- [X] T039 Run the full gate: `uv run pytest -q`, `uv run mypy src/remo_cli`, `uv run ruff check src/remo_cli`, and `cd frontend && npm run test` (expect no frontend regressions).
- [X] T040 Execute the `quickstart.md` scenarios A–H end-to-end against a local `remo web serve` and confirm each success criterion (SC-001..SC-009).

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: none.
- **Foundational (P2)**: after Setup — **blocks US1/US2/US3/US5** (all touch the push cache). US6 does not depend on it and may start in parallel with Foundational.
- **User stories (P3–P8)**: after Foundational (except US6). Priority order US1 → US2 → US3 → US6 → US4 → US5.
- **Polish (P9)**: after all desired stories.

### Story dependencies & file-contention notes

- **US1** is the MVP and lands first; it establishes the single `_push_flow`/`run_push` that US3, US4, and US5 (workstation side) all further edit in `core/web_adopt.py` — those three must be sequenced (not concurrent) against that file.
- **US2** is largely isolated (new `core/web_drift.py`, `cli/web.py` status command, and nudge call sites across separate provider files) — the `[P]` nudge tasks (T016–T020) touch different files and can run together.
- **US6** is fully independent (only `web/config.py` + `web/state.py`) and can be developed any time after Setup.
- **US5 service side** (T034/T035, `web/api/setup.py`) is independent of the workstation-side `core/web_adopt.py` edits and can proceed in parallel with them.

### Within each story

- Tests are written alongside implementation; verify new branches are covered (Constitution II).
- In `core/web_adopt.py`, Foundational (cache v3) precedes US1 (flow merge) precedes US3/US4/US5 workstation edits.
- **US2**: T013 (`core/web_drift.py`) is a hard prerequisite for T014 (`status` command) and every nudge call-site (T015–T020) — those tasks import `web_drift.out_of_date_notice()` / the diff+select helpers, so T013 MUST land first even though T016–T020 are `[P]` against each other.

---

## Parallel Opportunities

- **Foundational**: T005 (tests) can be written in parallel with T002–T004 review.
- **US2 nudge fan-out**: T016, T017, T018, T019, T020 are different files → run together. T011/T012 (tests) parallel with T013.
- **US6**: T026 (tests) parallel with T027/T028 across two files.
- **US5**: service side (T034/T035) parallel with workstation side (T036); T032/T033 tests parallel.
- **Polish**: T037 and T038 are different files → parallel.
- **Cross-story staffing**: once Foundational is done, US2, US6, and US5-service can proceed concurrently with the US1→US3→US4→US5-workstation chain on `core/web_adopt.py`.

---

## Parallel Example: User Story 2

```bash
# Tests + new module together:
Task: "Create tests/unit/core/test_web_drift.py (T011)"
Task: "Create src/remo_cli/core/web_drift.py (T013)"

# Nudge call sites across providers (different files) together:
Task: "Nudge in cli/providers/incus.py (T016)"
Task: "Nudge in cli/providers/proxmox.py (T017)"
Task: "Nudge in cli/providers/hetzner.py (T018)"
Task: "Nudge in cli/providers/aws.py (T019)"
Task: "Nudge in cli/added.py (T020)"
```

---

## Implementation Strategy

### MVP first (User Story 1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational (cache v3) → 3. Phase 3 US1 (unified push) → **STOP and validate** via quickstart Scenario A/B → ship. `remo web adopt`/`push` now share one path.

### Incremental delivery

Foundational → US1 (MVP) → US2 (drift+nudge) → US3 (revocation) → US6 (bare-metal mode) → US4 (`--force`) → US5 (flap) → Polish. Each story is independently testable and adds value without breaking the previous ones.

### Parallel team strategy

After Foundational: one developer takes the `core/web_adopt.py` chain (US1 → US3 → US4 → US5-workstation), a second takes US2 (drift/nudge, mostly separate files), a third takes US6 + US5-service (`web/*`). Integrate at Polish.

---

## Notes

- `[P]` = different files, no dependency on an incomplete task; tasks editing `core/web_adopt.py` are deliberately NOT marked `[P]` against each other.
- Preserve the hard constraint: `core/web_adopt.py` and `core/web_drift.py` import stdlib + `core`/`models` only (never `remo_cli.web.*` or optional deps).
- Preserve trust-model invariants on every change (service-scoped identity, workstation-verified host keys only, single `remo-web@` marker, pairing-gated surface, never copy personal keys).
- Commit after each task or logical group; stop at any checkpoint to validate a story independently.
