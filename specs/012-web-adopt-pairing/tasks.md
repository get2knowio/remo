---

description: "Task list for Ephemeral Device-Pairing Adoption (Forward-Auth Gated)"
---

# Tasks: Ephemeral Device-Pairing Adoption (Forward-Auth Gated)

**Input**: Design documents from `/specs/012-web-adopt-pairing/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included — the spec's per-story Independent Test criteria and
`quickstart.md` define explicit scenarios, and Constitution Principle II ("Test
All Conditional Paths") mandates covering every dormant/live/expired/rotated and
forward-auth/network-restricted branch.

**Organization**: Tasks are grouped by user story. US1/US2/US3 are all P1 and
together form the MVP (the pairing model is not shippable without all three);
US4 (P2) is the re-sync increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 / US2 / US3 / US4 (maps to spec.md user stories)

## Path Conventions

Existing three-layer repo: service `src/remo_cli/web/`, workstation CLI
`src/remo_cli/cli/` + `src/remo_cli/core/`, SPA `frontend/src/`, tests under
`tests/`. Paths below are repository-relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Configuration surface for the pairing model; remove the static token.

- [X] T001 In `src/remo_cli/web/config.py`, remove the `api_token` field (and its `REMO_WEB_API_TOKEN` docstring), and add `pairing_ttl_s: float` (`REMO_WEB_PAIRING_TTL_S`, default `900.0`), `operator_auth: str` (`REMO_WEB_OPERATOR_AUTH`, default `""`), and `forward_auth_header: str` (`REMO_WEB_FORWARD_AUTH_HEADER`, default `""`) per data-model.md.
- [X] T002 [P] In `src/remo_cli/web/logging_config.py`, add a defense-in-depth redaction pattern for a pairing code appearing in a `code`/bearer context (mirrors the existing Authorization-header redaction), so the code can never leak via logs (FR-016).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The in-memory pairing core and the operator-auth seam that every
user story consumes. **No user story can begin until this phase is complete.**

- [X] T003 [P] Create `src/remo_cli/web/pairing.py` with `PairingSession` (code, identity, origin, last_activity, ttl_s; `is_expired(now)`) and `PairingSessionManager` (at-most-one live session; injectable `now=time.monotonic`; `mint(identity, origin) -> code` with rotation, `authenticate(presented) -> PairingSession | None` constant-time + touch, `is_live()` with lazy expiry, `end()`; `threading.Lock` guard) per data-model.md R1/R6/R9.
- [X] T004 [P] Create `src/remo_cli/web/operator_auth.py` with `OperatorIdentity`, the `OperatorAuthProvider` protocol, `NetworkRestrictedProvider` (returns anonymous identity), `ForwardAuthProvider(header_name)` (reads the configured header), and `build_operator_auth_provider(settings)` that fail-fasts when `operator_auth="forward"` has no header name and returns `None` (minting disabled) when unset (R4/R5, FR-009/FR-010).
- [X] T005 Create `src/remo_cli/web/api/pairing.py` with the `POST /pairing/mint` route (returns `{code, expires_in}` with `Cache-Control: no-store`) and `POST /pairing/end` route (idempotent `204`) per contracts/pairing-api.md; mint depends on the configured provider (403 when unauthenticated/unconfigured, no session created).
- [X] T006 In `src/remo_cli/web/app.py`, construct the operator-auth provider at startup via `build_operator_auth_provider(settings)` (fail-fast surfaces here), instantiate `PairingSessionManager` on `app.state`, mount the pairing router under `/api/v1`, and emit a one-line "REMO_WEB_API_TOKEN is now ignored" info log if the old env var is still set (R13).
- [X] T007 In `src/remo_cli/cli/web.py`, when `remo web serve` binds a loopback interface and `REMO_WEB_OPERATOR_AUTH` is unset, default it to `none` for the child process and log the weaker-posture warning (R5), so local dev works without a proxy.
- [X] T008 [P] Unit-test the pairing core in `tests/unit/web/test_pairing.py` with an injected fake monotonic clock: mint returns a code, rotation invalidates the prior, `authenticate` touches the sliding window, idle expiry drops the session, `end()` is idempotent, and concurrent access is lock-safe (Constitution II).

**Checkpoint**: Pairing core + auth seam exist and are unit-tested; routers wired.

---

## Phase 3: User Story 1 - Pair-and-adopt with an ephemeral code (Priority: P1) 🎯 MVP

**Goal**: An authenticated operator mints a code on the adopt page, copies it,
and completes `remo web adopt` — no static token anywhere.

**Independent Test**: Behind a stub forward-auth header, load the adopt page,
mint+copy a code, run `remo web adopt` against a service managing ≥2 reachable
instances, and confirm the dashboard shows all instances with working terminals.

### Tests for User Story 1

- [X] T009 [P] [US1] Integration coverage in `tests/integration/test_web_adopt_e2e.py` (migrated from 011): boots a live `remo web serve` in the network-restricted posture, mints a code via `POST /pairing/mint`, runs the adopt orchestration, and asserts identity→registry→verify all succeed with the code as bearer and the session ends on the terminal verify (FR-007).
- [X] T010 [P] [US1] Unit test `tests/unit/core/test_web_adopt_code.py`: adopt sends the pasted code as `Authorization: Bearer`, and a dormant `404` from a setup call maps to the "reopen the adopt page for a fresh code" message (FR-020).

### Implementation for User Story 1

- [X] T011 [US1] In `src/remo_cli/web/api/setup.py`, replace `require_setup_token` with `require_pairing_code`: read the `PairingSessionManager` from `request.app.state`; dormant `404` when no live session; constant-time match against the live code; wrong/absent/expired → same `404` (never `401`); success touches the session (contracts/setup-api.md, FR-005/FR-006/FR-002).
- [X] T012 [US1] In `src/remo_cli/web/api/setup.py`, end the pairing session after a successful `PUT /registry` apply that transitions/refreshes configured state (FR-007), so the surface returns to dormant post-adoption.
- [X] T013 [US1] In `frontend/src/api/client.ts`, add `mintPairingCode()` (`POST /pairing/mint` → `{code, expires_in}`) and `endPairing()` (`sendBeacon`-friendly `POST /pairing/end`); remove any token concept.
- [X] T014 [US1] In `frontend/src/components/AwaitingAdoption.tsx`, mint on mount (rotation-on-open), hold the code only in a non-rendered `ref`, change the button to **Copy pairing code** (copies from the ref, never renders the value), and fire the `endPairing()` beacon on `visibilitychange`→hidden / `pagehide` (FR-003/FR-004/FR-015/FR-016, R7/R8).
- [X] T015 [P] [US1] In `src/remo_cli/cli/web.py`, rename the `adopt` credential prompt to "Pairing code" (still `--token` / `$REMO_API_TOKEN` / hidden prompt), and remove the `--save` flag from `adopt` (FR-018/FR-019).
- [X] T016 [US1] In `src/remo_cli/core/web_adopt.py`, map the dormant `404` on setup calls to the actionable "reopen the adopt page for a fresh code and retry" error text (FR-020), replacing the 011 "wrong token / setup disabled" messages.
- [X] T017 [US1] In `src/remo_cli/core/web_adopt.py`, remove `SavedCredentials` url/token fields, `save_credentials`, credential loading, and the save offer from the adopt path; retain only a non-secret push cache scaffold (deployment id + per-instance fingerprints) for US4 (FR-019, R10).

**Checkpoint**: First-time adoption works end-to-end via a page-minted code with no static token.

---

## Phase 4: User Story 2 - Setup surface is dormant until pairing (Priority: P1)

**Goal**: `/api/v1/setup/*` is `404` (byte-identical to unknown) whenever no
pairing session is live; health/readiness/SPA stay available.

**Independent Test**: With no session live, assert `404` on every setup route;
mint a session and assert the routes respond to the live code; end the session
(expiry or completion) and assert `404` again.

### Tests for User Story 2

- [X] T018 [P] [US2] Unit test `tests/unit/web/test_setup_dormancy.py` (starlette TestClient): every `/api/v1/setup/*` route returns `404 {"detail":"Not Found"}` with no live session (with and without a bearer); responds to the live code after mint; returns `404` after idle expiry (fake clock) and after adoption completion; a wrong-but-present code returns `404`, never `401` (FR-005/FR-006, SC-001).
- [X] T019 [P] [US2] Unit test in the same file asserting `GET /api/v1/health` and `GET /api/v1/ready` bodies/status are byte-unchanged across dormant, live-session, and post-adoption states (SC-008).

### Implementation for User Story 2

- [X] T020 [US2] Verify/adjust `require_pairing_code` (from T011) so the dormancy branch is exercised for all four setup routes uniformly (router-level dependency), and remove the now-dead `require_setup_token` symbol and its imports.
- [X] T021 [US2] In `src/remo_cli/web/api/pairing.py`, ensure `POST /pairing/end` drives the session to dormant immediately (best-effort beacon target), independent of the idle-TTL backstop (FR-004).
- [X] T022 [US2] Confirm the Origin-allowlist middleware in `src/remo_cli/web/app.py` leaves the `/api/v1/setup/*` origin-less exemption intact while the browser-only `/api/v1/pairing/*` routes remain subject to the Origin check (R11); add a regression test in `tests/unit/web/test_pairing_origin.py`.

**Checkpoint**: Dormancy is provable in all states; health/readiness untouched.

---

## Phase 5: User Story 3 - Forward auth gates code minting (Priority: P1)

**Goal**: Minting requires a trusted proxy-injected identity header; refused
without it; identity recorded (never the code); network-restricted posture is a
loud opt-in.

**Independent Test**: Behind a stub proxy, mint with no header (refused), with a
valid proxy-injected header (succeeds), and confirm the audit log names the
identity and never the code; confirm fail-fast when forward auth is enabled
without a header name.

### Tests for User Story 3

- [X] T023 [P] [US3] Unit test `tests/unit/web/test_operator_auth.py`: `ForwardAuthProvider` authenticates only when the configured header is present/non-empty; `build_operator_auth_provider` fail-fasts on `operator_auth="forward"` with no header; unset → mint disabled (403 not configured); `NetworkRestrictedProvider` returns the anonymous identity (FR-009/FR-011/FR-013).
- [X] T024 [P] [US3] Unit test `tests/unit/web/test_mint_gating.py` (TestClient): `POST /pairing/mint` returns `403` without the trusted header, `200 {code, expires_in}` with it, records the `subject` in logs and on the session, and never logs the code (FR-011/FR-012, SC-004).

### Implementation for User Story 3

- [X] T025 [US3] In `src/remo_cli/web/api/pairing.py`, enforce the operator-auth provider on `POST /pairing/mint`: refuse (`403`, logged with request context, no session) when `authenticate()` returns `None`; on success stamp the returned `OperatorIdentity` onto the minted session (FR-011/FR-012).
- [X] T026 [US3] In `src/remo_cli/web/health.py`, add the operator-auth posture as additive readiness diagnostic detail — `forward` (echo header name, never value), `network-restricted` (flagged weaker), or `unconfigured` — without changing the `ready` status value or health behavior (FR-013, SC-008, R12).
- [X] T027 [US3] In `src/remo_cli/web/check.py`, report the operator-auth posture in `remo web check` output and drop any static-token check (FR-013).
- [X] T028 [US3] In `src/remo_cli/web/app.py`, emit the loud startup warning when the network-restricted posture is active (FR-013), and confirm the forward-auth fail-fast aborts startup with a clear message.

**Checkpoint**: Minting is gated; posture is surfaced loudly; fail-fast enforced.

---

## Phase 6: User Story 4 - Re-sync after local changes uses the same flow (Priority: P2)

**Goal**: A dashboard "Pair CLI to sync" affordance mints a fresh code through
the same lifecycle/gate; `remo web push` consumes it; surface dormant before/after.

**Independent Test**: On an adopted service, open the re-sync affordance, mint a
code, run `remo web push` against a changed local registry, confirm the service
registry mirrors the change, then confirm the surface is dormant after close.

### Tests for User Story 4

- [X] T029 [P] [US4] Integration coverage in `tests/integration/test_web_adopt_e2e.py`: adopt, register a new instance, mint a fresh code, run `remo web push` against the live adopted service, and assert only the new instance is processed (delta) while the mirror applies; plus a dormant-code push that fails with reopen guidance.
- [X] T030 [P] [US4] Unit test `tests/unit/core/test_web_push_code.py`: `push` resolves URL + code every time (arg/env/prompt), no saved-credentials fast path, and the non-secret push cache (deployment id + fingerprints) still skips unchanged instances (FR-019, R10).

### Implementation for User Story 4

- [X] T031 [US4] Add a **Pair CLI to sync** affordance to the dashboard (`frontend/src/components/`) that mints+copies a code via the same `mintPairingCode()`/`endPairing()` lifecycle with `origin: "resync"` (FR-017).
- [X] T032 [US4] In `src/remo_cli/cli/web.py`, remove `push`'s saved-credentials fallback and `NoSavedCredentialsError` path; `push` now resolves URL + pairing code the same way `adopt` does (prompt/env/option) on every run (FR-018/FR-019).
- [X] T033 [US4] In `src/remo_cli/core/web_adopt.py`, finalize `run_push` to use the retained non-secret push cache keyed by the deployment id read from `/status` at push time (no url/token persisted), preserving the "unchanged instance skips re-authorization" optimization (R10).

**Checkpoint**: Re-sync works through the identical pairing mechanism.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Docs, compose, and full-suite validation.

- [X] T034 [P] In `docker/compose.example.yml`, remove `REMO_WEB_API_TOKEN`; document the forward-auth front door with the mint-gated / setup-passthrough split and the `REMO_WEB_OPERATOR_AUTH` + `REMO_WEB_FORWARD_AUTH_HEADER` env (FR-022).
- [X] T035 [P] In `docs/web-session-interface.md`, add the breaking-change note (static token removed), the pairing flow, the forward-auth trust boundary, and the hola-app forward-auth configuration (FR-014/FR-021/FR-022).
- [X] T036 [P] Grep the codebase and tests for any residual `api_token` / `REMO_WEB_API_TOKEN` / `require_setup_token` / `save_credentials` references and remove them (breaking-change completeness, FR-021).
- [X] T037 [P] Frontend lint-clean (`tsc --noEmit`) passes; the "code never in the served bundle" property (SC-006) holds by construction — codes are random values fetched at runtime via `mintPairingCode()` and held only in a non-rendered `ref` (`AwaitingAdoption.tsx`, `PairToSync.tsx`), never a source literal and never inserted into the DOM. (A runtime DOM-assertion unit test was not added: the repo has no frontend test harness — `npm run lint` is `tsc --noEmit` only — and introducing one is out of scope.)
- [X] T038 Run `uv run pytest`, `uv run mypy src/remo_cli`, `uv run ruff check src/remo_cli`, and `cd frontend && npm run lint`; fix any fallout.
- [X] T039 Execute `specs/012-web-adopt-pairing/quickstart.md` scenarios A–G against a live `remo web serve` and record results.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies — start immediately.
- **Foundational (Phase 2)**: depends on Setup — **BLOCKS all user stories** (the manager, auth seam, and routers).
- **US1 / US2 / US3 (Phases 3–5, all P1)**: depend on Foundational. They are tightly coupled (US2 dormancy and US3 gating both act on the same setup/mint routes US1 exercises) and together form the MVP. Recommended order US1 → US2 → US3, but US2 and US3 test tasks are independent and parallelizable once T011/T005 land.
- **US4 (Phase 6, P2)**: depends on Foundational + US1 (reuses the mint lifecycle and the push cache scaffold from T017).
- **Polish (Phase 7)**: after all desired stories.

### Within Each User Story

- Tests written to fail first, then implementation (Constitution II).
- Manager/provider (foundational) before routes; routes before SPA/CLI wiring.

### Parallel Opportunities

- T001 ∥ T002 (Setup, different files).
- T003 ∥ T004 (Foundational core modules, different files); T008 after T003.
- Test tasks marked [P] within a story run together (different files).
- US2 and US3 test authoring can proceed in parallel once the setup gate (T011) and mint route (T005/T025) exist.
- Polish T034 ∥ T035 ∥ T036 ∥ T037.

---

## Parallel Example: Foundational + User Story 1

```bash
# Foundational core (different files):
Task: "Create src/remo_cli/web/pairing.py (PairingSessionManager)"        # T003
Task: "Create src/remo_cli/web/operator_auth.py (provider seam)"          # T004

# User Story 1 tests (different files):
Task: "Integration test tests/integration/test_adopt_pairing.py"          # T009
Task: "Unit test tests/unit/core/test_web_adopt_code.py"                  # T010
```

---

## Implementation Strategy

### MVP (User Stories 1 + 2 + 3 — the pairing model)

1. Phase 1 Setup → Phase 2 Foundational (manager + auth seam + routers).
2. US1 (adopt path), US2 (dormancy), US3 (forward-auth gate) — the three P1
   stories are not independently shippable in isolation (the model needs all
   three), so land them together and validate as one MVP.
3. **STOP and VALIDATE**: quickstart scenarios A–D + F–G.

### Incremental Delivery

1. MVP (US1+US2+US3) → first-time adoption via ephemeral code, dormant surface,
   gated minting. Deploy/demo.
2. US4 → dashboard re-sync + `remo web push`. Deploy/demo.
3. Polish → docs, compose, breaking-change cleanup, full-suite + quickstart.

---

## Notes

- [P] = different files, no dependency on an incomplete task.
- No new runtime dependency — pairing core is stdlib (`secrets`, `time.monotonic`, `threading`).
- The pairing code MUST never appear in logs, the DOM, or the served bundle — verify in T024/T037 (SC-006).
- TTL branches use an injected fake monotonic clock — never `sleep` (T008/T018).
- Commit after each task or logical group.
