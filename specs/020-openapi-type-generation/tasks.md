---

description: "Task list for 020-openapi-type-generation"
---

# Tasks: Schema-Derived Frontend Types

**Input**: Design documents from `/specs/020-openapi-type-generation/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: **REQUIRED, not optional.** The deliverable of this feature *is* a set of automated checks.
The spec mandates them explicitly (FR-015..FR-020, FR-022) and the contracts fix their exact
semantics: `contracts/drift-checks.md` §4 defines T-1..T-10, `contracts/terminal-frames-v1.md` §3
defines F-1..F-6. Skipping test tasks would skip the feature.

**Organization**: Grouped by user story so each ships and validates independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1..US4 from spec.md
- Exact file paths are given in every task

## Path Conventions

Web application: Python service in `src/remo_cli/web/`, React SPA in `frontend/src/`, scripts in
`scripts/` and `frontend/scripts/`, tests in `tests/unit/` and `frontend/src/**/*.test.ts`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Toolchain and directory scaffolding. No behavior change.

- [X] T001 Create the generated-artifact directory `frontend/src/api/generated/` with a `README.md` stating that every file in it is generated, never hand-edited, and naming the two regeneration commands (no `.gitkeep` — the README keeps the directory tracked)
- [X] T002 Add `openapi-typescript` to `devDependencies` in `frontend/package.json` pinned to an **exact** version (no caret — research R6 and SC-005 depend on this), then run `npm install` and commit the updated `frontend/package-lock.json`
- [X] T003 [P] Add `generate:types` and `check:types-fresh` scripts to `frontend/package.json`; do **not** modify the existing `build` script (research R7 — the Docker frontend stage must keep working with no Python)
- [X] T004 [P] Exclude `frontend/src/api/generated/` from Vitest coverage reporting in `frontend/vite.config.ts` so generated declarations do not skew coverage. **Do not** add lint-tool exclusions: this repo has no ESLint — `npm run lint` is `tsc --noEmit`, and generated files must stay type-checked

**Checkpoint**: Toolchain present; nothing else changed; `npm run build` and `docker build` still pass.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Make the published contract describe what the service already returns, and build the
export. Every user story depends on this.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete. Doing contract
completeness *first* means US1's checked-in baseline is correct on day one and never needs a churn
re-baseline commit.

**⚠️ INVARIANT for this entire phase**: these are **declaration-only** changes. Not one serialized
byte may move (FR-005). Every task below is verified by the existing service test suite passing
unmodified.

### Vocabulary publication

- [X] T005 [P] Add `KnownProviderType(str, Enum)` with members `incus`, `hetzner`, `aws`, `proxmox` to `src/remo_cli/web/api/hosts.py`
- [X] T006 Annotate `InstanceOut.status: InstanceStatus` (importing the existing enum from `src/remo_cli/models/discovery.py`) and remove the `.value` unwrapping at `src/remo_cli/web/api/hosts.py::_instance_out`; a `str`-Enum serializes to its value, so the payload is unchanged
- [X] T007 Annotate `SessionTargetOut.zellij_state: ZellijState` and `devcontainer_running: DevcontainerRunning` (existing enums in `src/remo_cli/models/session_target.py`) and remove the corresponding `.value` unwrapping in `src/remo_cli/web/api/hosts.py::_target_out`
- [X] T008 Type `instance_type: KnownProviderType | str` on both `InstanceOut` and `SessionTargetOut` in `src/remo_cli/web/api/hosts.py` — this emits `anyOf[$ref, string]`, keeping the field open (FR-014) while making the vocabulary a referenced component (FR-004); verified byte-identical for both `"aws"` and a third-party value in research R5

### Response-model declaration

> Each model must document what the handler **already returns**. If a body does not match a proposed
> model, the model is wrong — not the handler (data-model.md §2).

- [X] T009 [P] Add `HealthResponse { status: str }` and attach it to `GET /health` in `src/remo_cli/web/health.py` (currently returns `dict`, publishing an untyped open object)
- [X] T010 [P] Add `ReadinessResponse { status: str, checks: dict[str, str], detail: str | None }` in `src/remo_cli/web/health.py` and declare it via `responses={200: ..., 503: ...}` on `GET /ready`, leaving the handler returning `JSONResponse` — the console deliberately reads the body on **both** statuses and `detail` is genuinely absent on the plain `ready` path
- [X] T011 [P] Add `MintPairingResponse { code: str, expires_in: int }` and `DetailResponse { detail: str }` to `src/remo_cli/web/api/pairing.py`; declare 200 and 403 on `POST /pairing/mint`. The 403 body is `{"detail": ...}`, **not** the error envelope — verified in the handler
- [X] T012 [P] Declare `POST /pairing/end` as a 204 with no response model in `src/remo_cli/web/api/pairing.py` — it returns `Response(status_code=204)` today, not a JSON object
- [X] T013 Attach the already-defined `CreateTerminalResponse` to `POST /terminals` in `src/remo_cli/web/api/terminals.py`; the model exists but is not wired to the route, so it never reaches `components`
- [X] T014 Add `ErrorEnvelope { error: ErrorOut }` in `src/remo_cli/web/api/hosts.py` and declare it on the failure responses of the routes that **actually return it** (`terminals.py`, `setup.py`, and the `app.py` middleware paths). Do **not** declare it on `pairing.py` — publishing an envelope the service does not honor is the exact failure this feature exists to prevent

### Export

- [X] T015 Create `scripts/export_openapi.py` writing `json.dumps(create_app().openapi(), indent=2, sort_keys=True)` plus a single trailing newline to `frontend/src/api/generated/openapi.json`; support `--stdout` for the determinism check; keep all logging on stderr so `--stdout` emits clean JSON (`create_app()` logs an operator-auth INFO line — research R1)
- [X] T016 Make `scripts/export_openapi.py` fail with an actionable message naming the `web` extra and the `uv sync --extra web` command when `remo_cli.web` cannot be imported (FR-008)
- [X] T017 Run the export and commit the first `frontend/src/api/generated/openapi.json` baseline

### Verification that nothing moved (FR-005)

- [X] T017a Add a payload-equivalence test to `tests/unit/web/` that captures `GET /hosts` and `GET /sessions` response bodies from the **pre-change** code (fixture JSON committed alongside the test) and asserts the post-change service returns byte-identical bodies for both a known provider type and a third-party one. FR-005 is the invariant the whole phase rests on and is the only MUST in this feature with no other dedicated check
- [X] T017b Add a test asserting `POST /pairing/end` still returns HTTP 204 with an empty body and `POST /pairing/mint`'s 403 still returns `{"detail": ...}` — the two handler shapes that Phase 2 declares and must not idealize

**Checkpoint**: The published contract describes reality. Existing service tests pass unmodified;
response payloads byte-identical (proven by T017a/T017b, not assumed). User story work can now begin.

---

## Phase 3: User Story 1 — A service-side model change cannot silently diverge (Priority: P1) 🎯 MVP

**Goal**: A backend model change that is not mirrored in the checked-in artifact fails the build with
a message naming what drifted and how to fix it.

**Independent Test**: Rename a field in a service response model on a scratch branch, run
`uv run pytest tests/unit/test_schema_drift.py`, confirm it fails naming the component and printing the
regeneration command. Revert, regenerate, confirm green. (quickstart S3)

### Tests for User Story 1

> Write these first; they must fail before T023 exists.

- [X] T018 [P] [US1] Create `tests/unit/test_schema_drift.py` with T-1 (real repository, all checks pass with zero findings) per `contracts/drift-checks.md` §4
- [X] T019 [P] [US1] Add hermetic synthetic tests T-2 (app has a path the artifact lacks — message names path and method) and T-3 (a component schema's properties differ — message names the component) to `tests/unit/test_schema_drift.py`
- [X] T020 [P] [US1] Add T-4 (artifact file missing) and T-5 (artifact unparseable) to `tests/unit/test_schema_drift.py`, asserting the R-4 message shape rather than a diff-against-empty
- [X] T021 [P] [US1] Add T-6 to `tests/unit/test_schema_drift.py`: run the export 3× on unchanged sources and assert byte-identical output (SC-005)
- [X] T022 [P] [US1] Add T-7 to `tests/unit/test_schema_drift.py`: running the check against a drifted tree leaves no tracked file modified (R-1, FR-019)

### Implementation for User Story 1

- [X] T023 [US1] Implement the schema drift check in `tests/unit/test_schema_drift.py` following the structure of `tests/unit/test_docs_structure.py`: a pure comparison function, a `render_failure_message` helper, and a thin test wrapper — so the two gates read as one family (FR-018)
- [X] T024 [US1] Implement `render_failure_message` per `contracts/drift-checks.md` §3 (M-1..M-6): name the artifact path, group findings by kind with counts, one item per line, `To fix:` block with the exact command, and a link to `docs/maintaining-generated-types.md`
- [X] T025 [US1] Implement M-6 explicitly — the message must state that a FastAPI/Pydantic/generator upgrade can also cause this failure and that regenerating is still the correct fix, so a contributor does not hunt for a source change that does not exist (research R2)
- [X] T026 [US1] Ensure the check **fails rather than skips** when the `web` extra is unavailable (R-3, FR-017), mirroring the reasoning `test_docs_structure.py` records for refusing to skip on a missing heading
- [X] T027 [US1] Verify the check rides the existing `uv run pytest` step in the `test` job of `.github/workflows/ci.yml` (no workflow edit needed — `tests/unit/` is already collected and the job runs `uv sync --all-extras`)
- [X] T027a [US1] Add an assertion to `tests/unit/test_schema_drift.py` that all 9 console-called REST endpoints (`/hosts`, `/sessions`, `/discovery/refresh`, `/ready`, `/pairing/mint`, `/pairing/end`, `POST /terminals`, `GET /terminals`, `DELETE /terminals/{id}`) are present in the artifact **with a non-empty response schema** — an endpoint publishing `{}` must fail. This is what makes SC-006 enforced rather than asserted once by hand

**Checkpoint**: US1 is complete and independently valuable — drift is caught even before the console
imports a single generated type. Run quickstart S2, S3, S4.

---

## Phase 4: User Story 2 — The console's types come from the service, not a comment (Priority: P1)

**Goal**: `api/client.ts` imports service-owned shapes from the generated artifact; error handling is
byte-for-byte unchanged.

**Independent Test**: No hand-declared service-owned shape remains in the console's API layer, and
`npm run test` passes with **zero test files modified** (quickstart S8).

### Tests for User Story 2

- [X] T028 [P] [US2] Create `frontend/scripts/check-types-fresh.mjs` — regenerate types to a temp path, byte-compare against the checked-in `schema.d.ts`, never write a tracked file (R-1, R-2)
- [X] T029 [P] [US2] Add a test asserting the checked-in artifact is present and parseable, failing with the R-4 message when absent (FR-020) — this is the console-only contributor's guard (US1 acceptance scenario 5)

### Implementation for User Story 2

- [X] T030 [US2] Generate `frontend/src/api/generated/schema.d.ts` from the checked-in `openapi.json` via `npm run generate:types` and commit it
- [X] T031 [US2] Replace the hand-written response/request interfaces in `frontend/src/api/client.ts` (`SessionTarget`, `InstanceStatus`, `RemoteCapability`, `TypedError`, `DiscoveryInstance`, `HostsResponse`, `SessionsResponse`, `RefreshResponse`, `MintPairingResponse`, `CreateTerminalResponse`, `TerminalSummary`, `ListTerminalsResponse`) with aliases onto `components["schemas"][...]` from the generated module
- [X] T032 [US2] Delete the stale header comment in `frontend/src/api/client.ts` claiming the types "mirror … exactly" and replace it with an accurate provenance statement naming the generated module and the regeneration command (FR-027)
- [X] T033 [US2] Verify — do not rewrite — that `ApiError`, `request()`, the `redirect: "manual"` / `opaqueredirect` re-auth path, the `_REAUTH_KEY` sessionStorage cooldown, `getReady()`'s dual-status body read, and `mintPairingCode()`'s synthesized 403 in `frontend/src/api/client.ts` are unchanged (FR-011). This is a type-provenance change, not a client rewrite
- [X] T034 [US2] Keep `ServiceStatus`, `ReadinessCheck`, `TerminalConnectionState`, and `TerminalConnectionCallbacks` hand-declared in the console — they are console-owned and deliberately open, and must not be pushed through the generated artifact (FR-012)
- [X] T035 [US2] Add the `check:types-fresh` invocation as a step in the `frontend` job of `.github/workflows/ci.yml`, after `npm ci` and alongside `npm run lint` (research R8 — the `frontend` job has Node only, which is why this check lives here and not with the Python one)
- [X] T036 [US2] Update any console test fixtures that construct service-shaped objects so they type-check against the generated types; correct a fixture that was already wrong rather than loosening the type (spec Assumptions)

**Checkpoint**: US1 + US2 together are the full P1 slice — the check is now meaningful because the
types it guards are actually imported. Run quickstart S2, S8.

---

## Phase 5: User Story 3 — Vocabularies read from the schema (Priority: P2)

**Goal**: `providerMeta.ts` maps over schema-derived values; a new status value is a compile error;
an unknown value at runtime still renders.

**Independent Test**: Add a status value, regenerate, confirm `tsc` fails at the presentation mapping;
supply a presentation, confirm green (quickstart S5).

### Tests for User Story 3

- [X] T037 [P] [US3] Add a console test that feeds an **off-union** status value to `statusMeta()` and asserts a neutral fallback renders without throwing (SC-010, FR-013a) — this is the guard against "achieving exhaustiveness" by deleting the `default:` branch
- [X] T038 [P] [US3] Add T-8 to `tests/unit/test_schema_drift.py`: `KnownProviderType`'s members equal the built-in provider names from `core/provider_registry.all_descriptors()`, failing with instructions to update the enum when a first-party provider is added
- [X] T039 [P] [US3] Add T-9 to `tests/unit/test_schema_drift.py`: registering a third-party provider type (via `provider_registry.temporary_registration`) leaves the exported artifact byte-identical (SC-011, FR-004a)
- [X] T040 [P] [US3] Add a console test asserting an instance with an unrecognized `instance_type` renders with the neutral provider fallback (SC-009)

### Implementation for User Story 3

- [X] T041 [US3] Rewrite `frontend/src/components/providerMeta.ts` to derive its status vocabulary from `components["schemas"]["InstanceStatus"]` instead of re-declaring the union
- [X] T042 [US3] Make the status presentation mapping exhaustive over the schema-derived union in `frontend/src/components/providerMeta.ts` (a `Record<InstanceStatus, StatusMeta>` or an exhaustiveness-checked switch), so an unmapped value is a compile error (FR-013)
- [X] T043 [US3] **Retain** the runtime fallback branch in `frontend/src/components/providerMeta.ts` for values outside the compiled union (FR-013a). Exhaustiveness governs the mapping; the fallback governs what a running console does with a value it was never compiled against
- [X] T044 [US3] Derive the `PROVIDERS` record in `frontend/src/components/providerMeta.ts` from `components["schemas"]["KnownProviderType"]` instead of re-declaring the four names, keeping the `?? { label: type || "?", color: "var(--prov-unknown)" }` fallback intact
- [X] T045 [US3] Consume the schema-derived `ZellijState` and `DevcontainerRunning` unions wherever the console branches on them (`frontend/src/components/railModel.ts`, `SessionRail.tsx`, and any other consumer found by grep)

**Checkpoint**: All three P1/P2 stories done. Run quickstart S5, S6, S7.

---

## Phase 6: User Story 4 — Control-frame contract has checked provenance (Priority: P3)

**Goal**: The `remo-terminal.v1` frames have one definition, the service uses it, and drift fails a check.

**Independent Test**: Change a frame shape on a scratch branch; a check fails naming the frame. Terminal
behavior unchanged (quickstart S9).

**⚠️ HIGHEST-RISK PHASE**: this is the only place in the feature where runtime behavior can actually
move. Read `contracts/terminal-frames-v1.md` §3 before starting.

### Tests for User Story 4

- [x] T046 [P] [US4] Create `tests/unit/web/test_frames.py` with round-trip tests for all six frames (`resize`, `ping`, `ready`, `exit`, `error`, `pong`) asserting serialized output is **byte-identical** to today's `json.dumps` literals, key order included (F-4)
- [x] T047 [P] [US4] Add the F-3 lenient-inbound tests to `tests/unit/web/test_frames.py`: malformed JSON, a non-object payload (list, string, number, `null`), and an unknown `type` value must each be **silently dropped** — no exception raised, no socket close. This is the invariant most likely to regress
- [x] T048 [P] [US4] Add T-10 to `tests/unit/test_schema_drift.py`: the frame model set versus the checked-in `terminal-frames.json`, failing with a message naming the drifted frame (FR-022)
- [x] T048a [P] [US4] Assert in `tests/unit/test_schema_drift.py` that the frame check's failure message is produced by the **same** `render_failure_message` helper as the REST check and carries the same M-1..M-6 elements (artifact name, grouped findings, `To fix:` command, doc link, dependency-bump note) — FR-025 requires the two to read as one family, and a shared renderer is what makes that structural rather than aspirational
- [x] T048b [P] [US4] Add a zero-tolerance gate to `tests/unit/test_architecture.py` asserting that `src/remo_cli/web/api/terminals.py` contains no ad-hoc control-frame dictionary literal (SC-012). Follow the existing `test_no_new_sys_exit_in_providers_layer` pattern in that module — quickstart S9's manual grep is a one-time check, not a regression gate

### Implementation for User Story 4

- [x] T049 [US4] Create `src/remo_cli/web/frames.py` with `ErrorClass(str, Enum)` (`auth`, `network`, `remote_capability`, `missing_project`, `remote_launch`), six frame models each carrying `v: 1` and a `type` discriminator, and two discriminated unions `InboundFrame` / `OutboundFrame` per `contracts/terminal-frames-v1.md` §2
- [x] T050 [US4] Change `_send_control` in `src/remo_cli/web/api/terminals.py` to accept an `OutboundFrame` model rather than a bare `dict`, preserving its existing `WebSocketDisconnect`/`RuntimeError` swallow
- [x] T051 [US4] Replace all five ad-hoc frame dict literals in `src/remo_cli/web/api/terminals.py` (lines ~308, ~324, ~394, ~404, ~449) with `frames.py` model constructions (SC-012 requires zero remaining)
- [x] T052 [US4] Rewrite `_handle_control` in `src/remo_cli/web/api/terminals.py` to validate through `InboundFrame`, **preserving the silent-drop behavior exactly** (F-3): validation failure returns, never raises. Do not let a `ValidationError` escape into the socket lifecycle
- [x] T053 [US4] Extend `scripts/export_openapi.py` to also emit `frontend/src/api/generated/terminal-frames.json` from `TypeAdapter(...).json_schema()`, wrapped in the envelope `{protocol: "remo-terminal.v1", frame_version: 1, inbound: ..., outbound: ...}` (F-5), using the same determinism rules as the REST artifact
- [x] T054 [US4] Generate `frontend/src/api/generated/terminal-frames.d.ts` and extend `frontend/package.json`'s `generate:types` and `frontend/scripts/check-types-fresh.mjs` to cover it
- [x] T055 [US4] Replace the hand-written `ControlMessage` interface in `frontend/src/terminal/TerminalConnection.ts` with the generated frame types, leaving the reconnect budget/backoff, ping interval, RTT reporting, and close-code handling untouched (FR-024)
- [x] T056 [US4] Document in `contracts/terminal-frames-v1.md` and `docs/maintaining-generated-types.md` that the frame contract versions independently of the REST contract (FR-023, F-6)

**Checkpoint**: All four stories complete. Run quickstart S9 and confirm `grep -n '"v": 1' src/remo_cli/web/api/terminals.py` returns nothing.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T057 [P] Write `docs/maintaining-generated-types.md`: what each artifact is, the two regeneration commands, when regeneration is required, how to read each drift-check failure, and the dependency-bump case (FR-026, SC-008)
- [X] T058 [P] Add a generated-file header to every artifact in `frontend/src/api/generated/` naming the regeneration command and stating the file is not hand-edited (R-6)
- [X] T059 Update the `## Project Structure` diagrams in `CLAUDE.md` and `AGENTS.md` to add `scripts/export_openapi.py`, `src/remo_cli/web/frames.py`, and the new test modules — `tests/unit/test_docs_structure.py` **will fail** otherwise (FR-028)
- [X] T060 [P] Update the Commands section of `CLAUDE.md` with the regeneration and check commands
- [X] T061 [P] Document in `docs/maintaining-generated-types.md` that the exported artifact is an internal build input with no external compatibility promise and no deprecation policy (FR-029)
- [X] T062 Add the 019-style Recent Changes entry for 020 to `CLAUDE.md`, moving the displaced third entry to `docs/feature-history.md` rather than letting the generator drop it
- [X] T063 Run the full quickstart: S1–S11 in `specs/020-openapi-type-generation/quickstart.md`
- [X] T064 Run `docker build -f docker/Dockerfile -t remo-web:drift-check .` and confirm it succeeds unchanged (quickstart S10) — the frontend stage copies only `frontend/` and has no Python, so this proves the artifact placement is correct
- [X] T065 Confirm SC-001 by inspection: zero service-owned request/response shapes remain hand-declared in `frontend/src/api/client.ts` (down from at least 12)
- [X] T066 Confirm SC-007: `uv run pytest` and `npm run test` both green with **zero pre-existing test files modified** to accommodate the change (fixture typing from T036 does not count unless a behavioral assertion changed)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Phase 1 — **blocks all user stories**
- **US1 (Phase 3)**: depends on Phase 2
- **US2 (Phase 4)**: depends on Phase 2; shares the export from T015 with US1 but does not depend on US1's check
- **US3 (Phase 5)**: depends on Phase 4 (needs the generated types imported before it can map over them)
- **US4 (Phase 6)**: depends on Phase 2 only — the frame pipeline is genuinely independent of the REST pipeline and could be built in parallel with US1/US2 by a second developer
- **Polish (Phase 7)**: depends on all shipped stories

### Story Dependencies

- **US1 (P1)**: independent after Phase 2
- **US2 (P1)**: independent after Phase 2. Ships **with** US1 — the spec is explicit that a drift check over types nobody imports proves nothing
- **US3 (P2)**: depends on US2
- **US4 (P3)**: independent after Phase 2

### Parallel Opportunities

- T003, T004 in Setup
- T005 and T009–T012 in Foundational (different files); T006–T008 all touch `hosts.py` and must be sequential
- T017a, T017b (payload-equivalence tests, different concerns)
- T018–T022 (all US1 tests, one new file each section but written as independent test functions)
- T028, T029 (US2 tests)
- T037–T040 (US3 tests)
- T046–T048, T048a, T048b (US4 tests; T048b lands in a different module)
- T057, T058, T060, T061 in Polish
- **Whole-story parallelism**: with two developers, US4 (Phase 6) can run concurrently with US1+US2 once Phase 2 lands — they touch disjoint files

### Sequential constraints worth calling out

- T006, T007, T008, T014 all edit `src/remo_cli/web/api/hosts.py` → strictly sequential
- T050, T051, T052 all edit `src/remo_cli/web/api/terminals.py` → strictly sequential
- T017 (commit baseline) must follow every Phase 2 declaration task, or the baseline is immediately stale
- **T017a must capture its fixture bodies BEFORE T005–T014 land** — it proves nothing if the "before" snapshot is taken from already-changed code. Capture the fixtures first, then do the declaration work
- T027a depends on T017 (there must be an artifact to assert against)
- T048a depends on T024 (the shared renderer must exist before the frame check can reuse it)
- T059 must follow T015 and T049, since it documents files those tasks create

---

## Parallel Example: User Story 1

```bash
# Write the US1 test cases together (independent test functions):
Task: "T019 synthetic drift tests T-2/T-3 in tests/unit/test_schema_drift.py"
Task: "T020 missing/unparseable artifact tests T-4/T-5 in tests/unit/test_schema_drift.py"
Task: "T021 determinism test T-6 in tests/unit/test_schema_drift.py"
Task: "T022 no-side-effects test T-7 in tests/unit/test_schema_drift.py"
```

## Parallel Example: Foundational response models

```bash
# Different files, no shared state:
Task: "T009 HealthResponse in src/remo_cli/web/health.py"
Task: "T011 MintPairingResponse + DetailResponse in src/remo_cli/web/api/pairing.py"
Task: "T013 attach CreateTerminalResponse in src/remo_cli/web/api/terminals.py"
```

---

## Implementation Strategy

### MVP scope

**Phases 1 + 2 + 3 + 4** — that is, US1 **and** US2 together. This is a deliberate departure from the
usual "MVP = US1 only": both stories are P1 and the spec states they ship together, because a drift
check guarding types nobody imports proves nothing. Stopping after Phase 3 would ship a check with no
consumer; stopping after Phase 4 would ship generated types with no gate.

### Incremental delivery

1. Phases 1–2 → the contract describes reality (no user-visible change, fully revertible)
2. Phases 3–4 → **MVP**: drift is caught and the console consumes generated types
3. Phase 5 → status/provider vocabularies stop being re-declared
4. Phase 6 → the frame contract joins the regime
5. Phase 7 → docs, structure gate, full validation

### Suggested commit boundaries

One commit per phase, except Phase 2 (split: vocabulary / response models / export) and Phase 6
(split: `frames.py` + tests / `terminals.py` refactor / artifact + console). The Phase 6 split matters —
if the WebSocket refactor needs reverting, it should revert without taking the frame definition with it.

---

## Notes

- **Every Phase 2 task is declaration-only.** If a change alters a serialized byte, it is wrong (FR-005) — and T017a proves it rather than trusting it.
- **The error envelope is not universal.** `pairing.py` returns `{"detail": ...}` on 403 and FastAPI's 422s use `HTTPValidationError`. Declare per route what that route really returns (T011, T014).
- **This repo has no ESLint.** `npm run lint` is `tsc --noEmit`. Do not add lint-tool config for the generated directory (T004).
- **The one real behavioral risk is T052.** `_handle_control` silently swallows malformed frames today;
  a naive Pydantic rewrite raises and could tear down the socket. T047 exists to catch that.
- **Do not delete `providerMeta.ts`'s `default:` branch** to satisfy exhaustiveness (T043, T037).
- **`npm run build` must not regenerate** — the Docker frontend stage has no Python (T064).
- Artifacts under `frontend/src/api/generated/` are never hand-edited; fix the source and regenerate.
