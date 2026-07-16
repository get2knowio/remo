---
description: "Task list for Remo Web Session Interface"
---

# Tasks: Remo Web Session Interface

**Input**: Design documents from `/specs/010-web-session-interface/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (all present)

**Tests**: INCLUDED — the spec's "Required Verification Strategy" mandates them (unit, integration on
disposable SSH targets, e2e nine-terminal fixture, browser, compatibility/parity, image, Ansible
idempotency, resource). Test tasks precede the implementation they cover within each phase.

**Organization**: Grouped by user story (US1–US5) so each is independently implementable/testable.

**Package note**: Python package root is `src/remo_cli/` (not `remo`). Web deps live in the `web`
optional extra and MUST stay lazily imported (NFR-008 / FR-041).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US5 for user-story phases; Setup/Foundational/Polish carry no story label

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project scaffolding for the web extra, frontend build, and container skeleton

- [X] T001 Add `[project.optional-dependencies].web = ["fastapi", "uvicorn[standard]", "websockets", "pydantic>=2"]` to `pyproject.toml` and register the `web` package under wheel packaging
- [X] T002 [P] Create backend package skeleton with empty modules per plan: `src/remo_cli/web/__init__.py`, `app.py`, `config.py`, `discovery.py`, `ssh_master.py`, `terminal.py`, `terminal_registry.py`, `tokens.py`, `health.py`, and `src/remo_cli/web/api/__init__.py`
- [X] T003 [P] Scaffold the frontend project in `frontend/` (`package.json` pinning `ghostty-web@0.4.0` + `xterm` + `vite` + `react` + `typescript`, `vite.config.ts`, `tsconfig.json`, `index.html`, `src/main.tsx`)
- [X] T004 [P] Copy the Ghostty WASM asset into `frontend/public/` and add a Vite step that serves it same-origin (no CDN) per FR-038
- [X] T005 [P] Add `docker/` skeleton: multi-stage `Dockerfile`, `compose.example.yml`, `entrypoint.sh` (placeholders wired later)
- [X] T006 [P] Extend tooling: add web sources to `ruff`/`mypy` config in `pyproject.toml`; add `pytest-asyncio` to the `dev` extra

**Checkpoint**: Skeletons exist; nothing wired yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The `remo-host` command, shared protocol client, SSH core refactor, read-only config
accessor, web app factory + health/ready, and shared models. Everything below blocks ALL user stories.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### remo-host host command (serves US1 discovery + US2 attach)

- [X] T007 Create `ansible/roles/user_setup/templates/remo-host.sh.j2` implementing `capabilities --json`, `sessions list --json`, and `sessions attach --project <name>` per `contracts/remo-host-protocol.md` (read-only discovery via `find`/`zellij list-sessions`/`docker ps`; attach `exec`s `~/.local/bin/project-launch`; documented exit codes 0/2/3/4/5; JSON on stdout, diagnostics on stderr)
- [X] T008 Add an idempotent install task for `remo-host` to `ansible/roles/user_setup/tasks/main.yml` (mirror the existing "Install project-launch script" `template:` task; `mode: 0755`; use `| default()` on any registered vars)
- [X] T009 [P] Ansible template test: extend `tests/unit/test_ansible_templates.py` to assert `remo-host.sh.j2` renders, emits only JSON on stdout for `--json` verbs, validates/refuses traversal in project names, and delegates attach to `project-launch`
- [X] T010 [P] Ansible idempotency test in `tests/ansible/test_remo_host_idempotency.py`: install task is idempotent on a fresh host AND a host already having `project-menu`/`project-launch`; both conditional branches covered

### Shared protocol client + SSH core refactor (no provider knowledge; lives in core)

- [X] T011 [P] Unit tests for the protocol client in `tests/unit/core/test_remo_host_client.py`: version negotiation `[1,1]` (in-range ok, out-of-range → typed incompatibility), malformed-JSON rejection, payload-size cap, argv quoting for project names with spaces/Unicode/leading-dash
- [X] T012 Implement `src/remo_cli/core/remo_host_client.py`: build `remo-host` argv, parse/validate versioned JSON into models, `[min,max]=[1,1]` negotiation, typed errors, payload cap (makes T011 pass)
- [X] T013 [P] Unit tests for SSH refactor in `tests/unit/core/test_ssh_controlpath.py`: `build_ssh_base_cmd()` produces identical direct+SSM args to today for the CLI default, and honors `$REMO_SSH_CONTROL_DIR` override; direct/SSM parity + safe arg construction (FR-055)
- [X] T014 Refactor `src/remo_cli/core/ssh.py`: extract `build_ssh_base_cmd(host, *, tty, multiplex, control_dir)`, parameterize the hard-coded `ControlPath` (default `~/.ssh/remo-…`, override via `$REMO_SSH_CONTROL_DIR`), keep `shell_connect` behavior unchanged (makes T013 pass; existing `tests/unit/core/test_ssh.py` stays green)
- [X] T015 [P] Add a read-only-safe registry accessor to `src/remo_cli/core/config.py` (resolve `known_hosts` path WITHOUT the `mkdir` side effect) + unit test in `tests/unit/core/test_config.py` for read-only mount safety

### Shared models

- [X] T016 [P] Create `src/remo_cli/models/capability.py` (`RemoteCapability`) per data-model.md
- [X] T017 [P] Create `src/remo_cli/models/session_target.py` (`SessionTarget` + opaque-id derivation + zellij/devcontainer enums)
- [X] T018 [P] Create `src/remo_cli/models/discovery.py` (`DiscoverySnapshot` + typed `InstanceStatus`/`TypedError`)

### Web app factory, config, health/ready, CLI group (blocks US4 + serves all)

- [X] T019 Implement `src/remo_cli/web/config.py` (`WebSettings`: bind addr, concurrency/timeouts, cache TTL, terminal caps 32/16, token TTL 30s, allowed hosts/origins) from env with safe defaults
- [X] T020 Implement `src/remo_cli/web/app.py` FastAPI factory: mount routers, Host/Origin + CSP middleware (no wildcard CORS), serve built frontend same-origin
- [X] T021 [P] Implement `src/remo_cli/web/health.py` + wire `GET /api/v1/health` and `GET /api/v1/ready` (liveness vs config-validity) per `contracts/rest-api.md`
- [X] T022 Create `src/remo_cli/cli/web.py` Click group `remo web {serve,check}` that lazy-imports `remo_cli.web.*` and fails with `pip install "remo-cli[web]"` (no traceback) when the extra is absent; register it in `src/remo_cli/cli/main.py`
- [X] T023 [P] Negative-import unit test in `tests/unit/web/test_lazy_import.py`: importing `remo_cli.cli.main` with `fastapi`/`uvicorn` blocked still succeeds (NFR-008), and `remo web serve` without the extra prints the install hint

**Checkpoint**: `remo-host` installs+works over SSH; shared client, SSH base-cmd, models, and a
health-only web app exist. User stories can now begin.

---

## Phase 3: User Story 1 - Discover every available session target (Priority: P1) 🎯 MVP

**Goal**: A dashboard that discovers projects across all reachable instances concurrently, grouped by
provider/instance, showing Zellij + devcontainer state, with typed status for unreachable/outdated hosts.

**Independent Test**: Mount a 3-instance × 3-project registry, make all reachable, load the UI → all
nine targets appear grouped with correct state, no terminal opened (SC-001/SC-010).

### Tests for User Story 1

- [X] T024 [P] [US1] Integration test in `tests/integration/test_remo_host_e2e.py` against disposable SSH targets: healthy, unreachable, malformed-JSON, incompatible-protocol, and slow hosts each yield the correct typed `DiscoverySnapshot` status (FR-006)
- [X] T025 [P] [US1] Unit test in `tests/unit/web/test_discovery.py`: concurrency/timeout knobs honored, per-host failure isolation, cache TTL + manual refresh, registry hot-reload without restart (FR-004/FR-005)

### Implementation for User Story 1

- [X] T026 [US1] Implement `src/remo_cli/web/discovery.py`: read registry (read-only accessor) and run concurrent per-instance discovery via `remo_host_client` + `build_ssh_base_cmd`, producing `DiscoverySnapshot[]`; bounded concurrency, connection/command timeouts, TTL cache (makes T024/T025 pass)
- [X] T027 [US1] Implement `src/remo_cli/web/api/hosts.py`: `GET /api/v1/hosts`, `GET /api/v1/sessions`, `POST /api/v1/discovery/refresh` per `contracts/rest-api.md`; register router in `app.py`
- [X] T028 [P] [US1] Frontend API client methods in `frontend/src/api/client.ts` for hosts/sessions/refresh (typed to data-model)
- [X] T029 [P] [US1] Discovery store in `frontend/src/state/discovery.ts` with incremental per-instance updates (FR-035) and interval + manual refresh
- [X] T030 [US1] Dashboard components in `frontend/src/components/` (`Dashboard.tsx`, `InstanceGroup.tsx`, `TargetCard.tsx`) grouping by provider/instance and showing reachability, compatibility, Zellij state, devcontainer state (FR-029); render `no_remo_host`/incompatible with the update remediation (FR-059)

**Checkpoint**: The dashboard discovers and displays all nine targets with correct state — MVP viable.

---

## Phase 4: User Story 2 - Open a browser terminal into a project (Priority: P1)

**Goal**: Select a target → interactive browser terminal attached to the project's normal Remo Zellij/
devcontainer session, streaming launch progress if cold, with typed failure + retry and no orphans.

**Independent Test**: From a browser with no local CLI, open a stopped devcontainer project, watch
startup, get a shell inside the container, run a command, disconnect, reconnect → same Zellij session.

### Tests for User Story 2

- [X] T031 [P] [US2] Unit tests in `tests/unit/web/test_tokens.py`: single-use consumption, 30s expiry, replay rejection, token never in URL/logs (FR-049)
- [X] T032 [P] [US2] Unit tests in `tests/unit/web/test_terminal_resize.py` and `tests/unit/web/test_backpressure.py`: resize clamps to bounds → `TIOCSWINSZ` (FR-060), bounded output queue pauses PTY reader under stall (FR-021), clean process reap on close (FR-019)
- [X] T033 [P] [US2] Integration test in `tests/integration/test_terminal_attach.py` against a disposable SSH target: WS handshake with subprotocol token, `ready`→PTY bytes, resize control frame, disconnect reaps local ssh but leaves remote Zellij running (FR-019/FR-020)

### Implementation for User Story 2 (backend)

- [X] T034 [P] [US2] Create `src/remo_cli/models/*` terminal entities used by the service (`TerminalAttachment`, `WsToken`, `SshMaster`) — colocate in `src/remo_cli/web/` if service-only, per data-model.md
- [X] T035 [US2] Implement `src/remo_cli/web/ssh_master.py`: per-instance ControlMaster in `$REMO_SSH_CONTROL_DIR` keyed by `(user,host,port,access_mode)`, stale-socket cleanup, `ssh -O check` health, dead-master → per-child reconnect (FR-024)
- [X] T036 [US2] Implement `src/remo_cli/web/terminal.py`: `pty.openpty()` + asyncio `ssh -tt … "remo-host sessions attach --project <quoted>"` with `TERM=xterm-256color`; PTY↔WS pumps, resize, backpressure, classified errors (auth/network/remote_capability/missing_project/remote_launch), process-group reap (makes T032 pass)
- [X] T037 [US2] Implement `src/remo_cli/web/tokens.py` (single-use 30s tokens bound to terminal+target) and `src/remo_cli/web/terminal_registry.py` (lifecycle, global/per-client caps 32/16, rejection) (makes T031 pass)
- [X] T038 [US2] Implement `src/remo_cli/web/api/terminals.py`: `POST/GET/DELETE /api/v1/terminals` + `WS /api/v1/terminals/{id}` per `contracts/terminal-websocket.md` (subprotocol token, Origin/Host check, server-side target re-authorization FR-050, binary/JSON framing); register router (makes T033 pass)

### Implementation for User Story 2 (frontend)

- [X] T039 [P] [US2] Define `frontend/src/terminal/RendererAdapter.ts` interface (create/open, write, onInput, fit/resize, focus, title, selection/copy, dispose — FR-037)
- [X] T040 [P] [US2] Implement `frontend/src/terminal/GhosttyRenderer.ts` (default) and `frontend/src/terminal/XtermRenderer.ts` (fallback) against the adapter (FR-036)
- [X] T041 [US2] WS terminal client in `frontend/src/api/client.ts`: open with `remo-terminal.v1` + token subprotocol, binary I/O, JSON control (resize/ready/exit/error), bounded auto-reconnect→manual (Clarifications Q2, FR-020)
- [X] T042 [US2] `frontend/src/components/TerminalCard.tsx`: renders one adapter-backed terminal with provider/instance/project labels, connection state, and reconnect/close controls (FR-032); typed failure + retry (US2 scenario 4)

**Checkpoint**: A single target opens an interactive terminal matching `remo shell -p`; reconnect reaches the same session.

---

## Phase 5: User Story 3 - Open and switch among many sessions (Priority: P1)

**Goal**: Open several/all targets; grid, tab/focused, and keyboard-fast switching; hidden terminals stay
connected; input/output never cross-routed.

**Independent Test**: Open all nine, interact with each, switch through grid/focused modes repeatedly →
input/output stay routed correctly with identity always visible.

### Tests for User Story 3

- [X] T043 [P] [US3] E2E fixture + test `tests/integration/test_nine_terminals.py`: three addressable SSH targets × three projects, open nine real PTY/WS terminals, assert no cross-routing incl. repeated project names (SC-003)
- [X] T044 [P] [US3] Browser (Playwright) tests in `tests/e2e/`: grid/tab/focus switching, keyboard input routes only to focused terminal, hidden terminals remain connected, per-terminal reconnect controls, and **basic mobile keyboard/input operation on a mobile viewport/emulation** (FR-033)

### Implementation for User Story 3

- [X] T045 [P] [US3] Workspace store in `frontend/src/state/workspace.ts`: open-terminal set/order, layout mode (grid|tabs|focused), focused id, persisted to `localStorage` only (FR-034)
- [X] T046 [P] [US3] `frontend/src/components/GridView.tsx` and `TabView.tsx` rendering multiple `TerminalCard`s without disconnecting hidden ones (FR-031)
- [X] T047 [US3] Bulk-open controls (one / all-on-instance / selected / all) in `Dashboard.tsx` (FR-030) creating independent attachments with per-terminal progress/error (US3 scenario 1)
- [X] T048 [US3] Keyboard-switching + focus routing wiring so keystrokes reach only the focused terminal (FR-031/US3 scenario 2), with provider/instance/project always visible (US3 scenario 4)

**Checkpoint**: Nine concurrent terminals open, switchable, correctly isolated.

---

## Phase 6: User Story 4 - Install as a home-lab Docker service (Priority: P1)

**Goal**: Documented Compose install with read-only registry + SSH material, tmpfs runtime, health/
readiness gating, non-root/read-only hardening, on amd64 and arm64, reaching direct-SSH and SSM targets.

**Independent Test**: On a clean amd64/arm64 Docker host, follow only the Compose docs, run readiness,
open a remote terminal from another tailnet device (SC-006).

### Tests for User Story 4

- [X] T049 [P] [US4] Image tests in `tests/image/` via `docker buildx`: builds amd64 + arm64; runs non-root with read-only rootfs; `/api/v1/ready` gates on required mounts; SSM plugin present and arch-correct (FR-042/FR-044/FR-027)
- [X] T050 [P] [US4] `remo web check` unit/integration test in `tests/unit/web/test_check.py`: validates registry readability, SSH identity, runtime-dir writability, required executables, reachability, protocol — without opening an interactive session (FR-046)

### Implementation for User Story 4

- [X] T051 [US4] Implement `remo web check` command body in `src/remo_cli/web/` (invoked by `cli/web.py`) producing per-check PASS/FAIL with what/why/how remediation (FR-046; US4 scenario 2 registry≠auth message)
- [X] T052 [US4] Implement `remo web serve` command body (uvicorn bootstrap of `app.py` with `WebSettings`, graceful shutdown that stops new terminals + reaps attachments, leaving remote Zellij intact — NFR-007/SC-014)
- [X] T053 [US4] Complete `docker/Dockerfile`: stage 1 node builds `frontend/`; stage 2 slim Python runtime installing `openssh-client`, AWS CLI v2, Session Manager Plugin selected by `TARGETARCH`; non-root UID/GID; read-only-friendly layout (FR-042)
- [X] T054 [US4] Complete `docker/entrypoint.sh` (non-root; run `remo web check` gate then `remo web serve`) and `docker/compose.example.yml` (RO registry + SSH material + optional AWS creds mounts, tmpfs `/run/remo-ssh`, healthcheck, restart, `no-new-privileges`, dropped caps, safe bind default — FR-043/FR-047)
- [X] T055 [P] [US4] Wire secret/token/proxy-command redaction into logging config in `src/remo_cli/web/app.py`/`config.py` (FR-028) + unit test asserting tokens/keys/proxy commands never appear in logs

**Checkpoint**: Compose install is readiness-gated, hardened, multi-arch, reaches direct + SSM targets.

---

## Phase 7: User Story 5 - Preserve CLI behavior and compatibility (Priority: P2)

**Goal**: `remo shell` keeps all behavior; web and CLI share connection/session identity, protocol
parsing, and validation; same project → same Zellij session from either surface.

**Independent Test**: Open a project from web and from `remo shell -p`; both attach to the same Zellij
session; existing CLI tests remain green (SC-002/SC-008).

### Tests for User Story 5

- [X] T056 [P] [US5] Parity test in `tests/integration/test_web_cli_parity.py`: web attach and `remo shell -p <project>` reach the same Zellij session on direct SSH and SSM (SC-002)
- [X] T057 [P] [US5] Regression run: confirm existing `tests/unit/core/test_ssh.py` + full suite pass unchanged after the T014 refactor (SC-008)

### Implementation for User Story 5

- [X] T058 [US5] Refactor `shell_connect`/`build_project_launch_remote_cmd` in `src/remo_cli/core/ssh.py` to consume the shared `build_ssh_base_cmd` and (where sensible) the shared `remo_host_client` for name validation, without changing CLI-visible behavior (FR-054)
- [X] T059 [US5] Confirm shared project-name validation is used by BOTH web attach and CLI so quoting/injection handling is identical (US5 scenario 3); add the shared validator if not already central

**Checkpoint**: CLI unchanged for users; web and CLI provably share the connection/session contract.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T060 [P] Ghostty Web compatibility suite in `tests/e2e/`: bash, zsh, Zellij, project menu/launch, devcontainer startup, full-screen TUIs, bracketed paste, mouse, Unicode, resize (FR-039/SC-009)
- [X] T061 [P] Resource/soak test extending `tests/integration/test_nine_terminals.py`: nine terminals for ≥1h, assert bounded memory, child-process cleanup, no cross-routing/unintended disconnects (NFR-004/SC-013)
- [X] T062 [P] Restrictive CSP finalization in `app.py` compatible with local Ghostty WASM + same-origin WS, plus a test asserting the policy (FR-051)
- [X] T067 [P] Latency/performance verification in `tests/perf/test_latency.py`: measure and record discovery-of-9-projects incremental render ≤10 s (SC-010), first warm-session output ≤5 s (SC-011), and web-introduced keystroke→echo ≤100 ms p95 excluding network/remote (SC-012); fail if thresholds exceeded on the 3×3 fixture
- [X] T068 [P] Negative-security test in `tests/integration/test_security_rejections.py`: `POST /terminals` with a fabricated/undiscovered `session_target_id` → 404 (FR-050); WS handshake from a disallowed `Origin` → 1008; reuse of a consumed or expired `ws_token` → 1008; assert tokens never appear in server logs or URLs (SC-007, quickstart V6)
- [X] T063 [P] README + operator docs (`docs/` and README): architecture, security boundary (LAN/tailnet only, grants shell to all instances), Compose, credentials/SSM, discovery states, terminal limits, troubleshooting, upgrade compatibility (FR-052/FR-057)
- [X] T064 [P] Update repo `CLAUDE.md` Active Technologies + Project Structure to include the web service, `remo-host`, and the `web` extra
- [X] T065 Run `quickstart.md` validation scenarios V1–V9 end-to-end and record results
- [X] T066 Final `ruff`/`mypy` clean-up across `src/remo_cli/web`, `core`, `models` and frontend `tsc`/lint

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1)**: no dependencies.
- **Foundational (P2)**: depends on Setup; **BLOCKS all user stories** (remo-host, shared client, SSH refactor, models, web app+health).
- **US1 (P3)**: depends on Foundational. MVP.
- **US2 (P4)**: depends on Foundational. Independent of US1 (can build in parallel), but a full demo uses US1's dashboard to pick a target.
- **US3 (P5)**: depends on Foundational + US2 (renders multiple `TerminalCard`s) and US1 (targets to open).
- **US4 (P6)**: depends on Foundational (`app.py`, health/ready, CLI group); full value depends on US2 (terminals). Buildable in parallel with US1–US3 for the packaging/hardening/check parts.
- **US5 (P7)**: depends on the T014 SSH refactor (Foundational) and US2's shared client usage.
- **Polish (P8)**: depends on all targeted stories.

### User story independence

- US1 is fully independent (discovery only).
- US2 is independent of US1 for backend; the two P1 stories can be staffed in parallel after Foundational.
- US3 layers on US2's terminal component.
- US4 (packaging) can proceed alongside once health/ready + serve exist.

### Within each story

- Tests are authored before/with implementation and must fail first.
- Models → services → endpoints → frontend integration.

---

## Parallel Opportunities

- **Setup**: T002–T006 all `[P]`.
- **Foundational**: T009/T010 (Ansible tests), T011/T013/T015 (core tests), T016/T017/T018 (models), T021/T023 all `[P]`; T012 after T011, T014 after T013.
- **US1**: T024/T025 `[P]`; T028/T029 `[P]` (frontend) alongside T026/T027 (backend).
- **US2**: T031/T032/T033 `[P]`; T039/T040 `[P]` (frontend adapters) alongside T034–T038 (backend).
- **US3**: T043/T044 `[P]`; T045/T046 `[P]`.
- **US4**: T049/T050 `[P]`; T055 `[P]`.
- **US5**: T056/T057 `[P]`.
- **Polish**: T060–T064, T066, T067, T068 `[P]` (T067/T068 are independent test files added by analyze remediation).

## Parallel Example: Foundational core

```bash
# Author these test files together (all [P], different files):
Task: "Protocol client tests in tests/unit/core/test_remo_host_client.py"   # T011
Task: "SSH controlpath/base-cmd tests in tests/unit/core/test_ssh_controlpath.py"  # T013
Task: "Read-only config accessor test in tests/unit/core/test_config.py"    # T015
# Then create the three shared models together:
Task: "RemoteCapability model in src/remo_cli/models/capability.py"          # T016
Task: "SessionTarget model in src/remo_cli/models/session_target.py"         # T017
Task: "DiscoverySnapshot model in src/remo_cli/models/discovery.py"          # T018
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational (critical — includes `remo-host` + shared client) →
3. Phase 3 US1 → **STOP & validate**: dashboard shows all nine targets with correct state (SC-001).

### Incremental delivery

Foundation → US1 (discovery MVP) → US2 (open a terminal) → US3 (many + switching) → US4 (ship as a
container) → US5 (parity proof) → Polish (compatibility/soak/docs). Each story is independently
testable and adds value without breaking prior ones.

### Parallel team strategy

After Foundational: Dev A → US1, Dev B → US2 (both P1), Dev C → US4 packaging/hardening. US3 begins once
US2's `TerminalCard` lands; US5 once the SSH refactor is settled.

---

## Notes

- `[P]` = different files, no incomplete-task dependency.
- Every task names an exact file path; test tasks precede/accompany the implementation they cover.
- Keep web deps lazily imported — verified by T023.
- Preserve the flat registry schema and existing CLI behavior (US5); refactors must keep `test_ssh.py` green.
- Commit after each task or logical group; stop at any checkpoint to validate a story independently.
