---
description: "Task list for 009-notifier-source-registration"
---

# Tasks: Notifier Source Registration — dynamic multi-agentsh polling

**Input**: Design documents from `/specs/009-notifier-source-registration/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: INCLUDED. The plan's Testing section enumerates new test files and
Constitution II ("Test All Conditional Paths") requires it; the lifecycle has many
branches (graceful/ungraceful drop, reconcile, capacity, backoff, restart).

**Organization**: Tasks are grouped by user story. US1 + US2 are both P1 (the
registry engine + connection-as-registration); US3 (Feature) is P2; US4 (status)
is P3.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1–US4 for story tasks; Setup/Foundational/Polish carry no story label
- All paths are repo-relative to `/workspaces/remote-coding/`

## Path conventions

Single project: `src/remo_cli/notifier/` (service), `src/remo_cli/cli/` (laptop CLI),
`ansible/roles/remo_notifier/`, `features/` (devcontainer Feature), `tests/notifier/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create the new package/dir skeletons this feature adds.

- [X] T001 Create the `sources/` subpackage skeleton: `src/remo_cli/notifier/sources/__init__.py` and the test package `tests/notifier/sources/__init__.py`
- [X] T002 [P] Create the devcontainer Feature skeleton dirs: `features/remo-notifier-source/` and `features/remo-notifier-source/scripts/` (empty, populated in US3)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The in-memory registry engine, config, models, and state primitives that ALL user stories build on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 [P] Add `SourcesConfig` (`[sources]`: `max_sources`, `keepalive_interval_seconds`, `idle_timeout_seconds`, `poll_base_interval_seconds`, `poll_backoff_factor`, `poll_backoff_cap_seconds`, `poll_backoff_jitter`) with validators (idle > keepalive; cap ≥ base; 0 ≤ jitter ≤ 1; factor ≥ 1), make `agentsh: AgentshConfig | None` optional in `NotifierConfig`, and add optional `source_id` (default `"seed"`) to `AgentshConfig` in `src/remo_cli/notifier/config.py` (data-model SourcesConfig)
- [X] T004 [P] Add wire/response models `SourceRegistration` (inbound, `extra="forbid"`, `source_id` pattern, inline `api_key`, bounded `labels`) and `SourceStatus` (outbound, excludes `api_key`/`api_url`), and add `sources: int` to `HealthResponse` in `src/remo_cli/notifier/models.py` (data-model SourceRegistration / SourceStatus)
- [X] T005 [P] Extend `PendingApprovals` in `src/remo_cli/notifier/state.py`: key entries by a core-minted colon-free delivery id, add the `delivery_id → (source_id, epoch, agentsh_approval_id)` mapping, and add `drain_source(source_id)` that fail-secure-denies only that source's entries (data-model DeliveryMapping; research R3/R9)
- [X] T006 Create `Source` dataclass + `PollHealth` (`poll_state`, `consecutive_failures`, `current_backoff_seconds`, `last_success_at`) with a redacted `repr` (no `api_key`) in `src/remo_cli/notifier/sources/source.py` (data-model Source / PollHealth)
- [X] T007 Create `SourcePoller` in `src/remo_cli/notifier/sources/poller.py`: per-source poll loop over the source's `AgentshClient`, per-source in-flight dedup, full-jitter exponential backoff on poll failure (`min(cap, base*factor**failures)`), reset on success, and an injected `dispatch(source, request)` callback — never de-registers on poll failure (depends on T003, T006; research R2/R4)
- [X] T008 Create `SourceRegistry` in `src/remo_cli/notifier/sources/registry.py`: lock-guarded `register(reg)` (capacity → `AtCapacity`; duplicate `source_id` → epoch-bumped reconcile, latest wins, cancel old task), `remove(source_id, epoch)` (epoch-guarded, skip `permanent`, cancel task + stop client), `snapshot()`/`count()`, `drain_all()`, and a poller-factory hook (depends on T005, T006, T007; data-model SourceRegistry; research R2/R7)
- [X] T009 [P] Tests for config in `tests/notifier/core/test_config.py`: `[sources]` defaults + each bound validator, `[agentsh]` now optional, seed `source_id` default (T003)
- [X] T010 [P] Tests for the registry in `tests/notifier/sources/test_registry.py`: capacity → `AtCapacity`; duplicate `source_id` reconcile (one task, epoch bumped); epoch-guarded `remove` (stale no-op); `permanent` seed never removed; `snapshot`/`count`; `drain_all` (T008)
- [X] T011 [P] Tests for the poller in `tests/notifier/sources/test_poller.py` with a fake agentsh: backoff grows then caps, resets on success, fail-secure (no dispatch while failing), per-source in-flight dedup (T007)

**Checkpoint**: Registry engine is complete and unit-tested — story wiring can begin.

---

## Phase 3: User Story 1 - A devcontainer's agentsh is polled while its connection is open (Priority: P1) 🎯 MVP

**Goal**: One notifier concurrently serves many sources — each connected source's agentsh is polled, its approvals delivered through the installed channel, and each decision resolved against the correct source with no cross-routing.

**Independent Test**: With the notifier running, open two source connections pointing at two fake agentsh endpoints; raise a pending approval on each; confirm each is delivered and each decision resolves against the correct source, concurrently.

### Tests for User Story 1

- [X] T012 [P] [US1] Presence-registration integration test in `tests/notifier/sources/test_presence.py`: `POST /v1/sources` registers a source and the notifier begins polling its agentsh within one poll interval (US1#1); duplicate `source_id` reconciles to a single poll loop (US1#4) (uses httpx `ASGITransport`)
- [X] T013 [P] [US1] Source-scoped delivery test in `tests/notifier/core/test_server.py`: two connected sources each with a distinct pending approval → both delivered and each decision resolved against its own source via its own key, never cross-routed (US1#2/#3); assert the id the core hands the channel is a colon-free delivery id (never the raw agentsh id) and that it round-trips back to the correct source on resolve (research R3)

### Implementation for User Story 1

- [X] T014 [US1] Refactor the per-approval flow in `src/remo_cli/notifier/server.py` to be source-scoped: build a `dispatch(source, request)` that mints a delivery id, reserves in `PendingApprovals`, delivers via the transport, and resolves the **real** agentsh id against `source.client` (keep grant short-circuit + auto-approve digest); remove the module-level single `_poll_loop`/`_handle`/`inflight`/`agentsh` globals (FR-002/FR-018/FR-019; research R3)
- [X] T015 [US1] Add `POST /v1/sources` in `src/remo_cli/notifier/server.py`: validate `SourceRegistration` (400 on bad body), `registry.register(...)` (catch `AtCapacity` → **log the rejection** and return 503 `{"error":"at_capacity",...}` before holding the stream), then return a `StreamingResponse` emitting keepalive ticks every `keepalive_interval_seconds` (contracts/source-registration.md; FR-004/FR-006)
- [X] T016 [US1] Wire the supervisor: in `src/remo_cli/notifier/server.py` build the `SourceRegistry` (with the `dispatch`/poller factory) inside `create_app`, register the optional `[agentsh]` **seed** source (`permanent`, epoch 0, reading its approver key from `agentsh.api_key_file`) at lifespan startup when configured, and `drain_all` on shutdown; in `src/remo_cli/notifier/cli.py` **drop** the single-`AgentshClient` build/`read_api_key` wiring and update the `create_app(...)` call accordingly (FR-005; research R7)

**Checkpoint**: Many sources can connect and be polled/resolved concurrently and independently — the MVP works.

---

## Phase 4: User Story 2 - Connection lifecycle is the registration lifecycle (Priority: P1)

**Goal**: A source is registered for exactly as long as its connection is open; graceful close and ungraceful death both remove it (the latter within the keepalive/idle timeout); a notifier restart starts empty and each source reconnects — with no approval ever auto-allowed.

**Independent Test**: Open a source connection and confirm it is polled; close it and confirm polling stops promptly; `kill -9` the source and confirm removal within the keepalive window; restart the notifier and confirm reconnect re-serves the source.

### Tests for User Story 2

- [X] T017 [P] [US2] Connection-lifecycle integration tests in `tests/notifier/sources/test_presence.py`: graceful client close → source removed + poll stops promptly (US2#2); simulated ungraceful drop → removed within `idle_timeout_seconds` (US2#3); registry starts empty after a fresh `create_app` and a reconnect re-serves the source with nothing auto-allowed in the gap (US2#4); `permanent` seed survives a (simulated) drop
- [X] T018 [P] [US2] In-flight drain test in `tests/notifier/core/test_state.py` / `test_server.py`: removing a source with a pending approval locally abandons it to a fail-secure **deny** (no allow ever), and a best-effort agentsh deny is attempted only when the endpoint is reachable (FR-009; research R9)

### Implementation for User Story 2

- [X] T019 [US2] Add drop detection to the `POST /v1/sources` streaming generator in `src/remo_cli/notifier/server.py`: detect disconnect via a failed keepalive write and a periodic `request.is_disconnected()` bounded by `idle_timeout_seconds`, then call `registry.remove(source_id, epoch)` (epoch-guarded so a stale connection's cleanup never removes the current registration) (FR-007/FR-008; contracts/source-registration.md)
- [X] T020 [US2] Wire fail-secure drain into removal in `src/remo_cli/notifier/sources/registry.py`: on `remove(...)` call `pending.drain_source(source_id)` (local deny) and attempt a best-effort `POST` deny via `source.client` only if reachable, ignoring its failure (FR-009; research R9)

**Checkpoint**: Connection state and registration state cannot diverge; ungraceful death and restart both self-heal fail-secure.

---

## Phase 5: User Story 3 - Opt-in devcontainer Feature maintains the connection (Priority: P2)

**Goal**: A project opts in by adding a reusable devcontainer Feature that opens the presence connection on container start and reconnects on drop; a project that omits it is never connected.

**Independent Test**: Build a devcontainer that includes the Feature against a running notifier; confirm a source connection is established shortly after start, survives notifier restarts via reconnect, and ends (source removed) when the container stops — with no connection for a devcontainer that omits the Feature.

### Tests for User Story 3

- [X] T021 [P] [US3] Feature smoke test in `tests/notifier/test_feature.py`: `shellcheck` passes on `install.sh` + `remo-source-connect.sh`; the connector's preflight exits non-zero naming missing options; a dry-run builds the expected `SourceRegistration` JSON and `POST` target from the resolved options

### Implementation for User Story 3

- [X] T022 [P] [US3] Write `features/remo-notifier-source/devcontainer-feature.json` with the options (`notifierAddress`, `agentshApiUrl`, `apiKey`/`apiKeyFile`, `sourceId`, `labels`) and background `entrypoint` (contracts/devcontainer-feature.md)
- [X] T023 [P] [US3] Write the idempotent `features/remo-notifier-source/install.sh`: ensure `curl`, install the connector to `/usr/local/share/remo-notifier-source/`, render resolved options into an env file (Constitution III)
- [X] T024 [US3] Write `features/remo-notifier-source/scripts/remo-source-connect.sh`: preflight (fail-fast), resolve `source_id`/read inline `api_key` from `apiKeyFile` at connect time, hold `POST /v1/sources` open with `curl --no-buffer`, and reconnect on any exit with full-jitter exponential backoff (base 1s, factor 2, cap 30s) (FR-012/FR-016/FR-017; research R8)
- [X] T025 [P] [US3] Write `features/remo-notifier-source/README.md`: usage, options, and the shared-network deployment prerequisite (notifier ↔ container reachability) (spec Assumptions)

**Checkpoint**: A project can opt in via the Feature and self-heal across notifier restarts; omitting it is a no-op.

---

## Phase 6: User Story 4 - Operator observes connected sources and their health (Priority: P3)

**Goal**: An operator can see which sources a host's notifier is serving and their health (count, ids, labels, poll state, last-success), with dropped sources absent.

**Independent Test**: With several sources connected (one whose agentsh is unreachable), run the status surface and confirm it reports the count and each source's id, labels, poll state, and last-success time; a backing-off source is shown as backing-off; a dropped source is absent.

### Tests for User Story 4

- [X] T026 [P] [US4] Status-surface tests in `tests/notifier/core/test_server.py`: `GET /v1/sources` lists id/labels/`poll_state`/`last_success_at` and never leaks `api_key`/`api_url` (US4#1); an unreachable-agentsh source shows `backing_off` while still listed (US4#2); a dropped source is absent (US4#3); `/v1/health` reports the `sources` count
- [X] T027 [P] [US4] CLI test in `tests/notifier/test_cli_notifier.py`: `remo notifier sources <host>` curls `GET /v1/sources` over SSH and prints the JSON (mirrors the existing `status` test)

### Implementation for User Story 4

- [X] T028 [US4] Add `GET /v1/sources` (returns `{count, sources: [SourceStatus]}` from `registry.snapshot()`) and populate `HealthResponse.sources`/`agentsh_connected` (≥1 source polling) in `src/remo_cli/notifier/server.py` (FR-020; contracts/source-registration.md; research R10)
- [X] T029 [US4] Add the `sources` subcommand to `src/remo_cli/cli/notifier.py` (SSH-curl `GET /v1/sources`, JSON output), reusing the existing `_bind_url`/`_ssh_run` helpers (research R10)

**Checkpoint**: The live set of connected sources and their health is observable from the CLI.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Deployment, docs, and project-wide checks across all stories.

- [X] T030 [P] Add the `[sources]` defaults to `ansible/roles/remo_notifier/defaults/main.yml` (`remo_notifier_max_sources`, keepalive/idle, backoff base/factor/cap/jitter) (data-model SourcesConfig)
- [X] T031 Render the `[sources]` section in `ansible/roles/remo_notifier/templates/notifier.toml.j2`, keeping the existing `[agentsh]` block (now seed); verify every registered-var access keeps `| default()` (Constitution I)
- [X] T032 [P] Update the README "Notifier" section: multi-source registry, the opt-in Feature, the shared-network prerequisite, the accepted open-bridge-only residual cross-source risk (FR-010), and `remo notifier sources` (Constitution V)
- [X] T033 [P] Run the Constitution I pre-commit greps on the role (`grep -r '\.rc ==' ansible/`, `grep -r '\.stdout' ansible/`) and confirm all matches use `| default()`
- [X] T034 [P] Confirm in `tests/notifier/test_dependency_isolation.py` / `test_packaging.py` that the laptop CLI still imports no service/Feature deps and that the new `sources/` subpackage ships in the wheel; add assertions if the existing tests don't already cover it (no new deps were added)
- [X] T035 Run `uv run mypy src/remo_cli` and `uv run ruff check src/remo_cli`; fix findings
- [X] T036 Execute the `quickstart.md` acceptance-check table end-to-end against a local notifier with two fake agentsh endpoints (SC-001..SC-007)

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies — start immediately.
- **Foundational (Phase 2)**: depends on Setup — **BLOCKS all user stories**.
- **US1 (Phase 3)** and **US2 (Phase 4)**: depend on Foundational. US2's drop/drain wiring (T019/T020) depends on US1's `POST /v1/sources` endpoint (T015) and supervisor wiring (T016) existing, so within a single track US1 → US2 is the natural order. With two developers, US1 and US2 tests can be written in parallel; the US2 endpoint edits build on US1's endpoint.
- **US3 (Phase 5)**: depends only on the `POST /v1/sources` contract (contracts/source-registration.md) — can proceed in parallel with US2 once US1's endpoint shape (T015) is fixed; the Feature is pure client code touching no service files.
- **US4 (Phase 6)**: depends on Foundational (`registry.snapshot()`, `SourceStatus`); independent of US2/US3.
- **Polish (Phase 7)**: depends on the stories it documents/deploys (T030/T031 after US1; T032 after US3/US4).

### Within each user story

- Tests are written first and expected to FAIL before implementation.
- Foundational engine (registry/poller/state) before any endpoint wiring.
- Endpoint/registration (US1) before drop/drain semantics (US2).

### Parallel opportunities

- Setup: T002 ∥ T001.
- Foundational: T003 ∥ T004 ∥ T005 (different files); then T006 → T007 → T008 (same subpackage chain); tests T009 ∥ T010 ∥ T011.
- US1 tests T012 ∥ T013; US2 tests T017 ∥ T018; US4 tests T026 ∥ T027.
- US3 implementation T022 ∥ T023 ∥ T025 (different files); T024 after T022/T023.
- Polish: T030 ∥ T032 ∥ T033 ∥ T034.
- With capacity: US3 and US4 can run in parallel with US2 after US1's endpoint (T015) lands.

---

## Parallel Example: Foundational

```bash
# Different files, no interdependencies — run together:
Task: "Add SourcesConfig + optional [agentsh] in src/remo_cli/notifier/config.py"          # T003
Task: "Add SourceRegistration/SourceStatus + HealthResponse.sources in models.py"          # T004
Task: "Add delivery-id map + drain_source to state.py"                                       # T005

# Then the engine chain (sequential): source.py → poller.py → registry.py        # T006→T007→T008
# Then the unit tests together:
Task: "test_config.py"   # T009
Task: "test_registry.py" # T010
Task: "test_poller.py"   # T011
```

## Parallel Example: User Story 1

```bash
# Write both story tests first (different files):
Task: "Presence-registration test in tests/notifier/sources/test_presence.py"   # T012
Task: "Source-scoped delivery test in tests/notifier/core/test_server.py"       # T013

# Then implement server.py sequentially (same file): T014 → T015 → T016
```

---

## Implementation Strategy

### MVP first (User Story 1 only)

1. Phase 1: Setup
2. Phase 2: Foundational (registry engine — blocks everything)
3. Phase 3: US1 — many sources polled/resolved concurrently with no cross-routing
4. **STOP and VALIDATE**: run T012/T013; demo two sources on two fake agentsh endpoints

### Incremental delivery

1. Setup + Foundational → engine ready
2. US1 → connect + poll + resolve (MVP)
3. US2 → connection-as-lifecycle (drop/drain/restart self-heal) — the core correctness property
4. US3 → opt-in devcontainer Feature (the real-world on-ramp)
5. US4 → operator observability
6. Polish → Ansible `[sources]`, README, lint/types, quickstart validation

### Parallel team strategy

- Everyone lands Setup + Foundational together.
- Then: Dev A US1→US2 (server.py owner); Dev B US3 (Feature, no service-file conflicts); Dev C US4 (status + CLI). US3/US4 merge cleanly once US1's `POST /v1/sources` shape is fixed.

---

## Notes

- All registry/poll-health/pending/grant state is in-memory and lost on restart by design (FR-001/FR-013); recovery is by reconnection — never add persistence.
- Fail-secure is the invariant across every branch: unconnected, dropped, backed-off, capacity-rejected, or restarting ⇒ no approval delivered, never an allow (SC-007).
- The channel stays source-unaware (FR-019); never add source knowledge to `transports/`/`channels/`.
- `api_key` is inline on the wire and in-memory only — never log it, never put it in `SourceStatus` or any response.
- `[P]` = different files, no incomplete dependency. Commit after each task or logical group.
