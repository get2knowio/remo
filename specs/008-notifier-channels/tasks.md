# Tasks: Notifier Channels — interchangeable delivery channels for the notifier sidecar

**Input**: Design documents from `/specs/008-notifier-channels/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Included. The spec's per-story Independent Tests, the `test_stub_channel` deliverable (US3 / SC-002), the agentsh integration suite (US2 / FR-020..FR-023, contracts/agentsh-integration.md), and Constitution II (test all conditional paths) all require them.

**Organization**: Tasks are grouped by user story. The shared package restructure is Foundational because all stories depend on the core/channels split, the catalog, and the generic transport config.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4 for story-phase tasks; Setup/Foundational/Polish carry no story label

## Path Conventions

Single project: package at `src/remo_cli/`, Ansible at `ansible/`, tests at `tests/notifier/`, build context at `notifier/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Packaging and build scaffolding that the restructure builds on.

- [X] T001 [P] Reorganize optional-dependency extras in `pyproject.toml`: add `notifier-core` (fastapi, uvicorn[standard], pydantic, structlog, **httpx** for the agentsh client, tomli marker), redefine `notifier-telegram` = notifier-core + python-telegram-bot, and keep `notifier` as an alias of `notifier-telegram` for back-compat (research R4/R9).
- [X] T002 [P] Parameterize `notifier/Dockerfile`: add `ARG CHANNEL=telegram` and change the install step to `uv pip install ".[notifier-${CHANNEL}]"`; keep the multi-stage layout and COPY context unchanged (research R4).
- [X] T003 [P] Create test package dirs `tests/notifier/core/__init__.py` and `tests/notifier/channels/telegram/__init__.py` for the relocated suites (research R8).

**Checkpoint**: Extras, build arg, and test layout exist; no behavior change yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The core/channels split, the channel catalog, and the generic transport config that ALL user stories require.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 [P] Create `src/remo_cli/notifier/channels/__init__.py` and `src/remo_cli/notifier/channels/base.py` defining the import-light `ChannelDescriptor` and `RequiredEnv` dataclasses per contracts/channel-descriptor.md (no FastAPI/telegram imports).
- [X] T005 Move `src/remo_cli/notifier/transports/telegram.py` → `src/remo_cli/notifier/channels/telegram/transport.py` (create `channels/telegram/__init__.py`); add a module-level `build(config) -> NotificationTransport` factory wrapping the existing constructor; keep `bind_grants`/`set_token`/`send_digest` and the grant command handlers intact (research R1, R7).
- [X] T006 Move the `TelegramConfig` Pydantic model out of `src/remo_cli/notifier/config.py` into `src/remo_cli/notifier/channels/telegram/config.py` (fields + `read_token()` verbatim) (research R3).
- [X] T007 Generalize `TransportConfig` in `src/remo_cli/notifier/config.py` to `{type: str, <raw per-type mapping>}`, removing the telegram-only validator and the `TelegramConfig` import; validate the selected type's sub-mapping by lazily delegating to the channel's own model. Also add an `[agentsh]` config section (`api_url`, `api_key_file`, `poll_interval_seconds`, `webhook_enabled`) per data-model.md (research R3/R9).
- [X] T008 Create `src/remo_cli/notifier/channels/telegram/descriptor.py`: the Telegram `ChannelDescriptor` (id `telegram`, label, image `remo-notifier-telegram`, the two `REMO_NOTIFIER_TELEGRAM_*` `required_env`, `transport_factory` path, `render_transport_toml`) producing TOML byte-identical to 007 (contracts/channel-descriptor.md).
- [X] T009 Create `src/remo_cli/notifier/channels/catalog.py`: `CHANNELS = [telegram_descriptor]` plus `list_channels()` and `get(id)` helpers; import-light (data-model.md).
- [X] T010 Generalize `build_transport()` in `src/remo_cli/notifier/cli.py` to resolve the channel via the catalog by `config.transport.type` and lazy-import its `transport_factory`, replacing the hardcoded telegram dispatch; keep the SIGHUP token-reread path working via the duck-typed `set_token` (research R1).
- [X] T010a Adopt agentsh's approval schema in `src/remo_cli/notifier/models.py`: replace the invented `/v1/approve` request body with agentsh's `Request` fields (`id`, `created_at`, `expires_at`, `session_id`, `command_id`, `kind`, `target`, `rule`, `message`, `fields`); update the transport ABC (`transports/base.py`) so `send_approval_request` carries this object (contracts/agentsh-integration.md, data-model.md).
- [X] T010b Add `src/remo_cli/notifier/agentsh_client.py` (CORE): an httpx approver client that polls `GET {api_url}/api/v1/approvals` (default interval 5s), and resolves via `POST {api_url}/api/v1/approvals/{id}` with body `{"decision":"approve"|"deny","reason":...}` and an approver `X-API-Key` header read from `api_key_file`; map internal allow/deny → agentsh approve/deny; fail-secure on agentsh errors/auth-disabled. Record the verified agentsh version the client is pinned to in a module constant/comment (FR-020/FR-021/FR-023).
- [X] T010c Rework `src/remo_cli/notifier/server.py`: remove the `POST /v1/approve` push endpoint; in the lifespan, run the agentsh poll loop that pulls pending `Request`s, hands each to the active transport for delivery, and routes the human's tap to `agentsh_client.resolve()`. Keep `GET /v1/health` (add agentsh-connection health). The human's decision always flows human → channel → notifier → agentsh (the notifier resolves; the human never calls agentsh) (FR-020/FR-022).
- [X] T010d Add the optional "poll now" webhook trigger endpoint (CORE), gated by `agentsh.webhook_enabled`: on POST it only schedules an immediate poll, treating the body as untrusted/unsigned; the loop is correct without it (FR-022).
- [X] T011 Relocate existing tests: move server/state/grants/logging tests under `tests/notifier/core/` and the telegram transport test under `tests/notifier/channels/telegram/`; update them for the agentsh-sourced flow (no `/v1/approve`); confirm the suite is green against the restructured package (research R8).

**Checkpoint**: Package split complete, catalog live, config generic, agentsh approver-client wired (poll → deliver → resolve) — Telegram still builds and delivers. User stories can now proceed in parallel.

---

## Phase 3: User Story 1 — Operator installs a notifier by choosing a channel (Priority: P1) 🎯 MVP

**Goal**: An operator installs a notifier by selecting a channel (named or picked), with a per-channel credential preflight, via the explicit deploy command only.

**Independent Test**: With Telegram in the catalog and its creds set, run `remo notifier deploy <host>` without `--channel`; confirm a picker offers the catalog, the pick deploys, and the host runs that channel reporting healthy.

- [X] T012 [P] [US1] Add the `channels` subcommand to `src/remo_cli/cli/notifier.py` (`remo notifier channels`) listing each catalog descriptor's id, label, and required env (marking secrets) per contracts/cli-notifier.md (FR-006a).
- [X] T013 [US1] Add `--channel` option and the channel-resolution rules to `deploy` in `src/remo_cli/cli/notifier.py`: named-in-catalog deploys; unknown name exits non-zero listing available channels (FR-010); no name + interactive → fuzzy picker via `core/picker`; no name + non-interactive → actionable error (FR-011); single-channel catalog may auto-select.
- [X] T014 [US1] Replace the hardcoded Telegram env check in `deploy` with a per-channel preflight driven by the selected descriptor's `required_env`, failing non-zero and naming exactly the missing `REMO_NOTIFIER_<CHANNEL>_*` vars and purposes, deploying nothing (FR-012/FR-012a, SC-007). Also preflight the channel-independent agentsh connection inputs (`api_url` + approver key `REMO_NOTIFIER_AGENTSH_API_KEY`) required by every channel (FR-020).
- [X] T015 [US1] In `deploy`, pass channel extra-vars to `notifier_deploy.yml`: `remo_notifier_channel=<id>`, the descriptor-rendered transport TOML fragment, and the secret/non-secret env values (contracts/cli-notifier.md).
- [X] T016 [P] [US1] Parameterize the Ansible role by channel: in `ansible/roles/remo_notifier/defaults/main.yml` add `remo_notifier_channel` (default `telegram`) and template `remo_notifier_image` as `remo-notifier-{{ remo_notifier_channel }}:{{ remo_notifier_version }}`; keep `| default()` on any registered-var access (Constitution I).
- [X] T017 [US1] Update `ansible/roles/remo_notifier/templates/remo-notifier.service.j2` to run the channel image and pass `--build-arg`/image per channel, and `ansible/roles/remo_notifier/tasks/main.yml` to build with `--build-arg CHANNEL={{ remo_notifier_channel }}`; service name, bridge bind, and port unchanged (FR-013/FR-014, research R5).
- [X] T018 [US1] Make `ansible/roles/remo_notifier/templates/notifier.toml.j2` generic: render the `[transport]` block from the CLI-supplied `remo_notifier_transport_toml` fragment instead of hardcoded telegram keys; keep `[server]`/`[approval]`/`[grants]`/`[instance]` as-is (research R5).
- [X] T018a [US1] Add the `[agentsh]` block to `notifier.toml.j2` (`api_url`, `api_key_file`, `poll_interval_seconds`, `webhook_enabled`) and write the approver key from `REMO_NOTIFIER_AGENTSH_API_KEY` to a 0400 secret file in `ansible/roles/remo_notifier/tasks/main.yml`, mirroring the Telegram token handling; keep `| default()` on registered vars (FR-020, Constitution I).
- [X] T019 [US1] Remove the notifier from the provisioning flow (FR-009a): delete the `remo_notifier` include from `ansible/tasks/configure_dev_tools.yml` and drop the `configure_remo_notifier` default; generalize the credential vars in `ansible/group_vars/all.yml` to the channel-namespaced convention.
- [X] T020 [P] [US1] Tests in `tests/notifier/test_cli_notifier.py` covering every selection branch (named/unknown/picker/non-interactive/single-channel) and the per-channel preflight (present/missing, incl. the agentsh approver key), plus `tests/notifier/test_catalog.py` for `get`/`list` and the `channels` command output (Constitution II).
- [X] T020a [US1] Re-point `remo notifier test` (in `src/remo_cli/cli/notifier.py`): 007's `test` POSTed to the removed `/v1/approve`, so drive a **local synthetic-approval injection** path that delivers a test-labeled approval to the installed channel without contacting agentsh, and reports the human's tap. Preserve the operator-facing behavior; add the minimal server-side test hook it needs (contracts/cli-notifier.md).

**Checkpoint**: An operator can install a channel by name or picker, blocked loudly on missing creds; the notifier is installable only via the explicit command. MVP complete.

---

## Phase 4: User Story 2 — Existing Telegram operator: delivery unchanged, approvals via agentsh (Priority: P1)

**Goal**: Telegram's delivery behavior matches spec 007, while the approval content is sourced from agentsh's `Request` and decisions are resolved against agentsh's API (the one intended change).

**Independent Test**: Run the Telegram workflow (deploy, status/logs, a real approval, a standing grant) and confirm identical delivery behavior, with the approval rendered from an agentsh `Request` and the tap resolving the matching agentsh approval.

- [X] T021 [P] [US2] Add `tests/notifier/channels/telegram/test_toml_parity.py` asserting the descriptor-rendered Telegram `[transport.telegram]` keys (and the role template output for `channel=telegram`) match spec 007 (FR-017).
- [X] T022 [P] [US2] Add `tests/notifier/core/test_agentsh_client.py` exercising the approver client: poll parses `[]Request`; resolve POSTs `{decision,reason}` to `/api/v1/approvals/{id}` with the approver `X-API-Key`; allow→`approve`/deny→`deny` mapping; fail-secure on HTTP error, auth-disabled, and `expires_at` elapsed; **decision routes human → notifier → agentsh, never human → agentsh directly** (FR-020/FR-021/FR-023).
- [X] T022a [P] [US2] Add a test for the optional webhook trigger: a POST schedules an immediate poll, the untrusted body is not used as authority, and the loop still resolves the approval found via `GET` (FR-022).
- [X] T023 [US2] Add a test asserting `GET /v1/health.transport` returns the active channel id (`telegram`) and health reports agentsh-connection reachability (research R6, FR-016).
- [X] T024 [P] [US2] Run the relocated Telegram transport tests (message render from an agentsh `Request`, inline buttons, callback auth, outcome edits, cancel, grants `/rules` `/revoke` `/pause`, "Always" flow, SIGHUP token reread); fix import drift; confirm green (FR-017).

**Checkpoint**: Telegram delivery preserved; approvals correctly sourced from and resolved against agentsh.

---

## Phase 5: User Story 3 — A developer adds a channel without touching the core (Priority: P2)

**Goal**: A new channel is a self-contained drop-in (package + catalog entry + extra), with zero edits to core or existing channels.

**Independent Test**: Register a stub channel in-test and confirm it is selectable/deployable through the catalog + CLI, with no import of or edit to core/Telegram modules.

- [X] T025 [P] [US3] Add `tests/notifier/channels/test_stub_channel.py`: define a fake `ChannelDescriptor` (no real SDK), register it into the catalog under test, and assert it appears in `list_channels()`/`channels`, resolves in `deploy` selection, and passes preflight with its declared env — without importing `server`/`state`/`models`/`grants`/telegram (US3 acceptance, SC-002).
- [X] T026 [P] [US3] Add a guard test that asserts the only files needed to add a channel are under `channels/<id>/` plus the one-line `catalog.py` registration (assert the core module set and `transports/base.py` are import-clean of any channel id) (contracts/channel-extension.md, SC-002).
- [X] T027 [US3] Verify the parameterized Dockerfile builds an arbitrary `CHANNEL` value given a matching extra (smoke-document in quickstart; assert the install command string is constructed from `CHANNEL`), confirming no new Dockerfile is needed per channel (research R4).

**Checkpoint**: The extensibility guarantee is enforced by tests, not just documented.

---

## Phase 6: User Story 4 — Operator switches a host to a different channel (Priority: P3)

**Goal**: Installing a different channel on a host replaces the prior one on the same bind/port; the switch is a fail-secure restart.

**Independent Test**: On a host running one channel, deploy a different channel; confirm only the new channel runs afterward on the unchanged address/port.

- [X] T028 [US4] Add a test/assertion that a second `deploy` with a different `--channel` results in a single running channel: the role's `ExecStartPre=docker rm -f remo-notifier` + channel-templated image yields one service on the unchanged bind/port (FR-013/FR-014, research R5).
- [X] T029 [US4] Add a core test confirming a channel switch (modeled as lifespan shutdown→restart) drains in-flight approvals to a fail-secure deny and clears in-memory grants — no fabricated allow (FR-015, FR-008).

**Checkpoint**: Replace semantics and fail-secure state loss verified.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T030 [P] Ansible defensive-access sweep (Constitution I): `grep -r '\.rc ==' ansible/roles/remo_notifier/` and `grep -r '\.stdout' ansible/roles/remo_notifier/`; ensure every match uses `| default()`.
- [X] T031 [P] Idempotency check (Constitution III): apply `notifier_deploy.yml` twice for `channel=telegram` and confirm no unexpected changes on the second run; confirm per-channel image build is conditional.
- [X] T032 [P] Update `README.md` "Notifier" section for channel selection, `remo notifier channels`, the per-channel `REMO_NOTIFIER_<CHANNEL>_*` convention, and the removal of the provisioning toggle (Constitution V, FR-009a).
- [X] T033 [P] Update `src/remo_cli/notifier/README.md` (and any notifier docs) to describe the core/channels split, the agentsh approver-client integration (poll/resolve, approver key, decision flows through the notifier), and link contracts/channel-extension.md + contracts/agentsh-integration.md.
- [X] T034 [P] Run `uv run mypy src/remo_cli` and `uv run ruff check src/remo_cli`; fix findings introduced by the restructure.
- [X] T034a [P] Add `tests/notifier/test_dependency_isolation.py` (SC-006/FR-019): assert from `pyproject.toml` that `notifier-telegram` pulls the Telegram SDK while `notifier-core` does not, that no channel SDK leaks into `notifier-core`, and that the base CLI install (no extras) imports `cli/notifier.py` + the catalog without importing any channel/service dependency.
- [X] T035 Trim the `## Recent Changes` entry the agent script appended to `CLAUDE.md` to a one-line summary consistent with prior entries.

---

## Dependencies & Execution Order

- **Setup (T001–T003)** → no deps; T001/T002/T003 all [P].
- **Foundational (T004–T011, incl. T010a–T010d)** depends on Setup. Internal order: T004 → (T005, T006) → T007 → T008 → T009 → T010 → T010a (schema/ABC) → T010b (agentsh client) → T010c (server poll loop) → T010d (webhook trigger); T011 after the moves + agentsh wiring. **Blocks all user stories.**
- **US1 (T012–T020, incl. T018a)** depends on Foundational. T012/T016/T020 are [P]; T013→T014→T015 are sequential (same file, deploy command); T017/T018/T018a depend on T016.
- **US2 (T021–T024, incl. T022a)** depends on Foundational; independent of US1. All largely [P].
- **US3 (T025–T027)** depends on Foundational; independent of US1/US2.
- **US4 (T028–T029)** depends on Foundational and the role changes in US1 (T016–T018).
- **Polish (T030–T035)** after the stories it documents/validates; mostly [P].

**Story completion order (by priority)**: US1 (MVP) → US2 → US3 → US4. US2/US3 can proceed in parallel with US1 once Foundational is done, since they touch mostly separate files (tests + core verification vs CLI/role).

## Parallel Execution Examples

- **Setup**: T001, T002, T003 together.
- **Foundational kickoff**: T004 (channels/base) ∥ while T005/T006 moves are staged (T005/T006 then converge into T007–T010).
- **Post-Foundational fan-out**: start US1 T012 & T016 & T020, US2 T021 & T022 & T024, and US3 T025 & T026 concurrently (distinct files).
- **Polish**: T030, T031, T032, T033, T034 together.

## Implementation Strategy

- **MVP = Foundational + US1**: delivers channel-selectable install (named/picker), per-channel preflight, `remo notifier channels`, and explicit-command-only deployment.
- **Increment 2 = US2**: lock in non-regression (Telegram parity + wire-protocol stability tests) — run alongside US1.
- **Increment 3 = US3**: enforce the zero-core-edit extensibility guarantee via the stub-channel and guard tests.
- **Increment 4 = US4**: verify replace semantics and fail-secure switch.
- **Polish**: Ansible safety/idempotency, docs, types/lint.
