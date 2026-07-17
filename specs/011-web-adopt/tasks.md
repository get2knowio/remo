# Tasks: CLI-to-Web Adoption

**Input**: Design documents from `/specs/011-web-adopt/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — Constitution Principle II (Test All Conditional Paths) makes the
state matrix, token gating, and trust-decision tables mandatory test surface;
research.md R13 defines the layered strategy these tasks implement.

**Organization**: Tasks are grouped by user story. US2's *state machinery* and US3's
*auth dependency* live in Phase 2 (Foundational) because every story builds on them —
the US2/US3 phases then cover their user-visible surfacing and hardening validation,
keeping each story independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 (adopt), US2 (unconfigured boot), US3 (token gate), US4 (ongoing push)

---

## Phase 1: Setup

**Purpose**: Baseline verification and shared test scaffolding

- [X] T001 Verify baseline is green (`uv sync --all-extras && uv run pytest && cd frontend && npm ci && npm run lint`) so feature regressions are attributable
- [X] T002 Add shared pytest fixtures for a temp writable/read-only `REMO_HOME` state dir (registry + `web-identity/` layouts for all four configuration states) in tests/unit/web/conftest.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: State machinery, service identity, SSH option threading, and the auth
dependency that every user story builds on

**⚠️ CRITICAL**: No user story phase can begin until this phase is complete

- [X] T003 Add `api_token`, service-identity paths (`web-identity/` under `REMO_HOME`), and resolved `ssh_identity_file`/`ssh_known_hosts_file` settings to `WebSettings` in src/remo_cli/web/config.py (per research R1/R6)
- [X] T004 Create `ConfigurationState` detection (`unconfigured`/`adopted`/`mount_configured`/`broken`, writability probes, mount-precedence rule, EACCES-safe per research R2) in src/remo_cli/web/state.py
- [X] T005 Add `ServiceIdentity` generation to src/remo_cli/web/state.py: `ssh-keygen -t ed25519` subprocess, `deployment_id` minting, `state.json` persistence, 0600/0644 enforcement, reuse-never-regenerate (FR-002, research R3)
- [X] T006 [P] Extend `build_ssh_opts()`/`build_ssh_base_cmd()` with optional `identity_file`/`known_hosts_file` params emitting `IdentityFile`+`IdentitiesOnly` / `UserKnownHostsFile` opts; `None` default keeps argv byte-identical in src/remo_cli/core/ssh.py (research R6)
- [X] T007 [P] Add `Authorization` header + bearer-token redaction patterns to src/remo_cli/web/logging_config.py (FR-022)
- [X] T008 Create the setup router scaffold with `require_setup_token` dependency (`hmac.compare_digest`; unset token → 404 on every route; mismatch → 401) in src/remo_cli/web/api/setup.py and register it in src/remo_cli/web/app.py (FR-020/FR-021, research R4)
- [X] T009 [P] Unit tests: full state-detection matrix (4 states × probe failures × precedence) in tests/unit/web/test_state.py
- [X] T010 [P] Unit tests: ssh-opts regression (default `None` → today's argv, byte-compared) + new param emission in tests/unit/core/test_ssh_identity_opts.py

**Checkpoint**: Foundation ready — user story phases can begin

---

## Phase 3: User Story 1 - First-time adoption from the workstation CLI (Priority: P1) 🎯 MVP

**Goal**: `remo web adopt` hands a working CLI's configuration to a fresh service:
registry mirror + verified host keys pushed, service identity authorized on every
direct-access instance, verification report rendered — personal key never moves.

**Independent Test**: Against a locally run `remo web serve` with a temp writable
`REMO_HOME` and a token (quickstart C/D): one command populates the service, opens
working terminals, re-run is a zero-change no-op.

### Implementation for User Story 1

- [X] T011 [US1] Implement `GET /api/v1/setup/status` and `GET /api/v1/setup/identity` (409 `mount_configured` on identity, contracts/setup-api.md shapes) in src/remo_cli/web/api/setup.py
- [X] T012 [US1] Implement `PUT /api/v1/setup/registry`: full `AdoptionPayload` validation (version, name references, known_hosts parse, SSM-no-keys, empty guard w/ `allow_empty`), then atomic two-file apply — host-keys file first, registry last, temp+rename (FR-016/FR-017/FR-019, research R5) in src/remo_cli/web/api/setup.py
- [X] T013 [US1] Implement `POST /api/v1/setup/verify` wrapping `check.run_checks(include_instances=True)` as JSON in src/remo_cli/web/api/setup.py
- [X] T014 [P] [US1] Thread `WebSettings` service identity/known-hosts into every service SSH call site via the new `build_ssh_base_cmd` params in src/remo_cli/web/discovery.py, src/remo_cli/web/terminal.py, src/remo_cli/web/check.py (adopted mode only; mounted mode passes `None`)
- [X] T015 [US1] Create workstation-side setup-API HTTP client (stdlib `urllib.request`, bearer header, timeouts, typed errors for 401/404/409/422) in src/remo_cli/core/web_adopt.py (research R9; no web-extra imports)
- [X] T016 [US1] Add adoption payload builder in src/remo_cli/core/web_adopt.py: full-mirror snapshot from `get_known_hosts()`, SSM entries in registry but never in `host_keys`, empty-registry guard (FR-008/FR-012/FR-016)
- [X] T017 [US1] Add host-key scan + trust verification in src/remo_cli/core/web_adopt.py: `ssh-keyscan` + `ssh-keygen -F` against workstation known_hosts (hashed-entry safe), match/mismatch/absent decision table with interactive SHA256 fingerprint confirmation, non-interactive skip (FR-009/FR-010, clarification Q2, research R8)
- [X] T018 [US1] Add idempotent authorized_keys management in src/remo_cli/core/web_adopt.py: single POSIX-sh remote command filtering ` remo-web@` marker lines then appending current key, temp+mv, executed via `build_ssh_base_cmd` per instance (FR-011, research R7)
- [X] T019 [US1] Add `--via` SSH tunnel helper in src/remo_cli/core/web_adopt.py: free-port probe, `ssh -N -L` with `ExitOnForwardFailure=yes`, readiness wait, teardown; Host-allowlist failure message names `REMO_WEB_ALLOWED_HOSTS` (FR-018, research R9)
- [X] T020 [US1] Add adopt orchestration in src/remo_cli/core/web_adopt.py: contract flow steps 1–7, per-instance `AdoptionRunOutcome` accumulation with bounded timeouts and failure isolation, verification rendering with "reachable from workstation but not from the service" annotation, prominent security-flag output (FR-013/FR-014/FR-015)
- [X] T021 [US1] Add `remo web adopt` Click command in src/remo_cli/cli/web.py: URL/token resolution order (arg → `REMO_API_URL`/`REMO_API_TOKEN` → prompt), `--token/--via/--allow-empty/--yes`, exit codes per contracts/cli-web-adopt.md, no web-extra imports at any level (FR-006)
- [ ] T022 [P] [US1] Unit tests: payload builder (mirror, SSM exclusion, empty guard; negative assertion that the serialized payload never contains private-key material — FR-007) in tests/unit/core/test_web_adopt_payload.py
- [ ] T023 [P] [US1] Unit tests: trust decision table (match/mismatch/absent × interactive/non-interactive, hashed known_hosts) with mocked subprocess in tests/unit/core/test_web_adopt_trust.py
- [ ] T024 [P] [US1] Unit tests: authorized_keys command construction + marker replacement idempotence + rotation replacement in tests/unit/core/test_web_adopt_authorize.py
- [X] T025 [P] [US1] Unit tests: setup endpoints (status/identity/registry/verify happy paths, 409 mount-configured, 422 invalid/empty, atomicity on mid-apply failure) via TestClient in tests/unit/web/test_setup_api.py
- [ ] T026 [US1] Integration test: full adopt against a live local `remo web serve` (temp `REMO_HOME`, real HTTP): end-state files, verify report, second-run idempotence, unreachable instance via `.invalid` host, and an established terminal attachment surviving a registry push untouched (FR-019 session continuity) in tests/integration/test_web_adopt_e2e.py
- [ ] T027 [P] [US1] Unit tests: adopt CLI command (resolution order, exit codes, mount-configured message, works without the `web` extra installed) in tests/unit/cli/test_web_adopt_cmd.py

**Checkpoint**: Adoption works end-to-end against a locally served instance — MVP

---

## Phase 4: User Story 2 - Fresh service boots into "awaiting adoption" (Priority: P2)

**Goal**: A configless container with a state volume starts healthy, generates its
identity, and clearly signals "awaiting adoption" via readiness, `remo web check`,
and the browser — while RO bind-mount deployments behave exactly as today.

**Independent Test**: Quickstart A + G: fresh container reports `unconfigured`
within 30 s, keypair persists across restart, browser shows the awaiting page;
existing RO-mount image tests stay green unchanged.

### Implementation for User Story 2

- [ ] T028 [US2] Extend readiness in src/remo_cli/web/health.py: `unconfigured` as a 200 status variant, identity candidates include the service key path, `broken` keeps 503 (FR-001/FR-003, research R11)
- [ ] T029 [US2] Teach `remo web check` the unconfigured state in src/remo_cli/web/check.py: PASS with "awaiting adoption — run `remo web adopt`" detail; mount-configured and adopted modes report their mode (FR-003/FR-005)
- [ ] T030 [US2] Generate the service identity at startup when unconfigured (app lifespan / serve bootstrap) in src/remo_cli/web/app.py + src/remo_cli/cli/web.py (FR-002)
- [ ] T031 [US2] Verify/adjust docker/entrypoint.sh so the startup gate passes in the unconfigured state (SC-006 no-crash-loop; likely follows from T029 — confirm and document)
- [ ] T032 [P] [US2] Extend the ready-payload types + polling to expose service state in frontend/src/api/client.ts
- [ ] T033 [US2] Add `AwaitingAdoption` page (explanation + pre-filled `remo web adopt <origin>` command, poll-flip to dashboard on state change, no instance data) in frontend/src/components/AwaitingAdoption.tsx wired into the dashboard root (FR-004, research R12)
- [ ] T034 [P] [US2] Unit tests: ready/check outputs across all four states in tests/unit/web/test_health_states.py
- [ ] T035 [US2] Image tests: unconfigured boot with empty named volume + token (ready `unconfigured` < 30 s, keypair in volume, restart reuses keypair) added to tests/image/test_docker_image.py; existing RO-mount tests must pass unchanged (SC-005/SC-006)
- [ ] T036 [US2] Add the adopted-mode deployment variant (named state volume + `REMO_WEB_API_TOKEN`) alongside the RO-mount variant in docker/compose.example.yml

**Checkpoint**: Both deployment modes boot correctly and are visibly distinguishable

---

## Phase 5: User Story 3 - Token-gated setup surface hardening (Priority: P3)

**Goal**: Prove the fail-closed contract: correct token → accepted; wrong/missing →
401; no token configured → setup surface invisible; nothing sensitive ever logged.

**Independent Test**: Quickstart B — curl matrix against a running service in both
token-set and token-unset deployments.

### Implementation for User Story 3

- [ ] T037 [P] [US3] Contract tests: auth matrix on every setup route (valid/invalid/missing token → 200-family/401/401; unset token → 404 on all; non-setup surface unaffected) in tests/unit/web/test_setup_auth.py (FR-020/FR-021)
- [ ] T038 [US3] Implement + test failed-auth observability: log line without the presented credential, redaction assertions over captured logs for token and Authorization header in src/remo_cli/web/api/setup.py and tests/unit/web/test_setup_auth.py (FR-022/FR-024)

**Checkpoint**: Security posture verified — safe to expose behind Traefik

---

## Phase 6: User Story 4 - Ongoing push after local changes (Priority: P4)

**Goal**: Zero-argument `remo web push` re-syncs a previously adopted service using
saved credentials, touching only new/changed instances.

**Independent Test**: Quickstart E — sync a new instance locally, run `remo web push`,
new instance live in the dashboard < 60 s; rotated token → clear re-auth failure.

### Implementation for User Story 4

- [ ] T039 [US4] Add saved-credentials read/write (0600 `~/.config/remo/web-service.json`, explicit-consent prompt, `deployment_id` stored) in src/remo_cli/core/web_adopt.py (FR-025, research R10)
- [ ] T040 [US4] Add push orchestration in src/remo_cli/core/web_adopt.py: `deployment_id` mismatch → abort with re-adopt guidance; delta detection so unchanged instances skip keyscan/authorize; full mirror still pushed (FR-026/FR-027, clarification Q1)
- [ ] T041 [US4] Add `remo web push` Click command (`--allow-empty`, `--yes`; missing credentials → adopt-style prompts) in src/remo_cli/cli/web.py
- [ ] T042 [P] [US4] Unit tests: saved-credentials lifecycle (consent, perms, absent, rejected token, deployment-id mismatch) + delta logic in tests/unit/core/test_web_push.py
- [ ] T043 [US4] Extend the integration test with the push-after-adopt scenario (new registry entry → only it gets authorized; token rotation → exit 1 with guidance) in tests/integration/test_web_adopt_e2e.py

**Checkpoint**: All four stories independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T044 [P] Document the adoption workflow end-to-end in docs/web-session-interface.md: state volume, token config (compose + hola), adopt/push usage, `--via` fallback, key rotation via state reset + re-adoption, manual de-authorization of removed instances, reverse-proxy/SSO caveat + future forward-auth bypass note (FR-028/FR-029)
- [ ] T045 [P] Update agent context (`.specify/scripts/bash/update-agent-context.sh claude`) and the Recent Changes section so CLAUDE.md reflects 011-web-adopt
- [ ] T046 Run the full quickstart validation (scenarios A–H in specs/011-web-adopt/quickstart.md) against the built image and record outcomes
- [ ] T047 Full quality sweep: `uv run pytest`, `uv run mypy src/remo_cli`, `uv run ruff check src/remo_cli`, `cd frontend && npm run lint && npm run build`, `REMO_RUN_IMAGE_TESTS=1 uv run pytest tests/image/ -v`
- [ ] T048 Verify SC-003 idempotence + SC-005 regression explicitly: back-to-back adopt runs diff-clean; pre-feature compose example boots identically on this build

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → all user story phases
- **US1 (Phase 3)**: needs only Foundational (T004/T005 state+identity, T008 auth scaffold). Its end-to-end validation against a *container* is nicer after US2, but the independent test runs against a local `remo web serve`, so US1 does not block on US2.
- **US2 (Phase 4)**: needs only Foundational. Independent of US1 (surfaces state; pushes nothing).
- **US3 (Phase 5)**: needs Foundational T008; T037 exercises routes from US1 — run after T011–T013 (or against the scaffold's 401/404 layer only).
- **US4 (Phase 6)**: builds on US1's orchestration (T015–T021).
- **Polish (Phase 7)**: after all desired stories.

### Key task-level dependencies

- T004 → T005 (same module); T003 → T004 (settings feed detection)
- T008 → T011 → T012 → T013 (same file, sequential)
- T015 → T016 → T017 → T018 → T019 → T020 (same module, build up) → T021
- T030 depends on T005; T033 depends on T032; T040 depends on T020; T041 depends on T021+T039

### Parallel Opportunities

- Phase 2: T006, T007 in parallel with T003–T005; T009/T010 in parallel once their targets exist
- Phase 3: T014 in parallel with T011–T013 (different files); T022–T025, T027 all parallel (distinct test files)
- Phases 4–6 are parallelizable across contributors once Foundational lands; within US2, T032 parallel with backend tasks

## Parallel Example: User Story 1 test wave

```bash
# After T011–T021 land, launch the whole US1 test wave concurrently:
Task: "Unit tests: payload builder in tests/unit/core/test_web_adopt_payload.py"
Task: "Unit tests: trust decision table in tests/unit/core/test_web_adopt_trust.py"
Task: "Unit tests: authorized_keys idempotence in tests/unit/core/test_web_adopt_authorize.py"
Task: "Unit tests: setup endpoints in tests/unit/web/test_setup_api.py"
Task: "Unit tests: adopt CLI command in tests/unit/cli/test_web_adopt_cmd.py"
```

## Implementation Strategy

### MVP First (US1 via local serve)

1. Phases 1–2 (Setup + Foundational)
2. Phase 3 (US1) → **validate**: quickstart C/D against `remo web serve` locally
3. Demo-able: one command adopts a fresh service; terminals ride the service identity

### Incremental Delivery

1. - Phase 4 (US2) → containers become the demo surface (quickstart A/G) — this is the natural "deploy to the NAS" milestone
2. - Phase 5 (US3) → curl the auth matrix; safe to put behind Traefik/hola
3. - Phase 6 (US4) → the everyday `sync → push` loop
4. - Phase 7 → docs + full validation sweep, then release

### Notes

- All service-side writes go through the temp-file+rename pattern — no new persistence machinery
- `cli/web.py` and `core/web_adopt.py` must never import `remo_cli.web.*` (NFR-008 discipline from 010); T027 asserts it
- Commit after each task or logical group; stop at any checkpoint to validate the story independently
