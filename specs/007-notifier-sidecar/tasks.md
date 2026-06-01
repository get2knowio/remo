# Tasks: Notifier Sidecar — Telegram approval bridge for agentsh

**Input**: Design documents from `/specs/007-notifier-sidecar/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: INCLUDED — the spec explicitly requests them (§9 Tests; Acceptance Criteria 8 requires >85% coverage on `src/remo_cli/notifier/`). Test tasks are written before their implementation within each story.

**Organization**: Tasks are grouped by user story so each can be implemented and tested independently.

## Path Conventions

Single project. Python package at `src/remo_cli/`; Ansible at `ansible/`; tests at `tests/`; Dockerfile at repo-root `notifier/`. All paths below are repo-relative.

**Shared-file note**: `src/remo_cli/cli/notifier.py` and `tests/notifier/test_cli_notifier.py` are each touched by multiple stories (US2/US3/US4). Tasks editing the same file are **not** parallel with one another and are sequenced by phase; this is called out where relevant.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Package scaffolding and packaging so any later phase can build/import.

- [X] T001 Create the notifier package skeleton: `src/remo_cli/notifier/__init__.py`, `src/remo_cli/notifier/transports/__init__.py`, `src/remo_cli/notifier/docs/` (empty), and the test package `tests/notifier/__init__.py`. Every new module starts with `from __future__ import annotations`.
- [X] T002 Update `pyproject.toml`: add the `[notifier]` optional extra (fastapi>=0.115, uvicorn[standard]>=0.32, pydantic>=2.9, python-telegram-bot>=21.6, structlog>=24.4, `tomli>=2.0; python_version<'3.11'`); add `remo-notifier = "remo_cli.notifier.cli:main"` to `[project.scripts]`; add `pytest-asyncio` and `httpx` to the `dev` extra; add `[tool.pytest.ini_options] asyncio_mode = "auto"`; add a `[tool.hatch.build.targets.wheel.force-include]` mapping that ships the host build context to `remo_cli/notifier_build/`. **Layout contract (U1):** the bundled context MUST reproduce a repo-root-relative tree so the Dockerfile's relative `COPY` paths resolve unchanged — i.e. `notifier_build/Dockerfile`, `notifier_build/pyproject.toml`, `notifier_build/README.md`, `notifier_build/uv.lock`, and `notifier_build/src/remo_cli/…`. The same `notifier/Dockerfile` (T003) is the single source of truth; the force-include maps it (and the other context files) into `notifier_build/`. `uv.lock` is included only for an optional locked build (see T003); it is not consumed by a plain `uv pip install`. (per research R5)
- [X] T003 [P] Create `notifier/Dockerfile` (multi-stage: python:3.13-slim builder using `uv pip install ".[notifier]"` into `/opt/venv`; slim runtime, system user 65532, `EXPOSE 18181`, HEALTHCHECK on `/v1/health`, `ENTRYPOINT ["remo-notifier"]`, `CMD ["serve","--config","/etc/notifier/notifier.toml"]`). All `COPY` paths are relative to the build-context root (`Dockerfile`, `pyproject.toml`, `README.md`, `src/`) and MUST match the bundled layout from T002 so the same Dockerfile builds identically from the repo root and from the on-host context (U1). If reproducible/locked builds are wanted later, switch the builder to `uv sync --frozen` to consume `uv.lock`; otherwise `uv.lock` is unused (I1). (spec §5, AC-2)
- [X] T004 [P] Add `community.docker (>=4.0.0)` to `ansible/requirements.yml`. (research R10)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The durable wire-protocol models and logging that every story depends on.

**⚠️ CRITICAL**: No user-story work begins until this phase is complete.

- [X] T005 [P] Implement `src/remo_cli/notifier/logging_setup.py`: structlog config (ISO ts, level, logger name; JSON renderer for non-TTY, key-value for TTY) plus a redaction processor that drops `bot_token`/`authorization`/raw request bodies and keeps only structural fields at INFO+. (FR-017, research R7)
- [X] T006 [P] Implement `src/remo_cli/notifier/models.py`: Pydantic v2 models `Operation`, `ApprovalRequest`, `ApprovalDecision`, `ApprovalResponse`, `HealthResponse`, `ErrorResponse` with `extra="forbid"` where specified, UUID/enum/port validation, and RFC3339 serialization. (data-model.md; FR-001/003/004/005)
- [X] T007 [P] Unit tests `tests/notifier/test_models.py`: valid request round-trips, unknown-field rejection, bad UUID rejection, enum/port bounds, timeout-field presence. (validates T006)
- [X] T007a [P] Unit tests `tests/notifier/test_logging_setup.py`: emit log events carrying a bot token, a raw request body, and a workspace path; assert that at INFO+ none of those values appear and only structural fields (`approval_id`, `decision`, `latency_ms`, `transport`, `pending_count`) survive; assert the sensitive values are present only when the level is DEBUG. (validates T005; G1/SC-006/FR-017)

**Checkpoint**: Wire models + logging importable; stories can begin.

---

## Phase 3: User Story 1 — Human approves/denies from their phone (Priority: P1) 🎯 MVP

**Goal**: A running `remo-notifier` service that accepts an approval over HTTP, delivers it to the authorized Telegram chat with Approve/Deny buttons, and returns the human's decision (or a fail-secure deny on timeout/shutdown/send-failure/capacity).

**Independent Test**: Run `remo-notifier serve --config <toml>` locally against a real bot; POST an `ApprovalRequest`; tapping Approve returns `allow`, Deny returns `deny`, and a 5 s-timeout request returns 408 `{decision: deny, reason: timeout}`.

### Tests for User Story 1 (write first; must fail before implementation)

- [X] T008 [P] [US1] Create `tests/notifier/conftest.py`: a `FakeTransport(NotificationTransport)` (records sends, lets tests resolve/raise on demand), a config-builder fixture, a temp token-file fixture, and an ASGI client fixture. (shared by US1/US3/US4 tests)
- [X] T009 [P] [US1] `tests/notifier/test_config.py`: strict TOML load, unknown-key error, `max_timeout_seconds >= default_timeout_seconds` validator, `max_pending_approvals` default, token read from file (not config), missing/empty token fails fast. (validates T013)
- [X] T010 [P] [US1] `tests/notifier/test_state.py`: register→resolve, timeout→deny, cancel, duplicate-id reservation rejected, at-capacity rejected, send-failure rollback frees the slot, concurrent registration via `asyncio.gather`, `drain()` on shutdown, Future resolved exactly once. (validates T015; FR-003a/008/009/034, FR-010a)
- [X] T011 [P] [US1] `tests/notifier/test_server.py`: cover 200 (allow & deny via FakeTransport), 400 (bad body), 408 (timeout deny shape + within ~timeout), 409 (duplicate id), 503 (transport unhealthy / at capacity / send failure / shutting down), and `GET /v1/health` shape. Include a **timeout-clamp** case: an over-`max_timeout_seconds` request and an omitted-timeout request both resolve at the configured effective bounds (G2/FR-006). Use FastAPI `TestClient` for sync paths and `httpx.AsyncClient` for await-until-decision/timeout. (validates T017; FR-001–010a, FR-006, FR-016)
- [X] T012 [P] [US1] `tests/notifier/test_telegram.py`: inject a mocked `Bot` into the PTB `Application`; assert message text/`MarkdownV2` escaping, inline keyboard `callback_data` (`approve:{id}`/`deny:{id}`), authorized-chat enforcement (foreign chat ignored), callback→`on_response`, non-pending callback is a no-op, message edits per outcome, `cancel` edit, token read from file. (validates T016; contracts/telegram-message.md; FR-010–014)

### Implementation for User Story 1

- [X] T013 [P] [US1] Implement `src/remo_cli/notifier/config.py`: Pydantic config models (`NotifierConfig` + `server`/`approval`/`transport`/`telegram`/`instance`) with `extra="forbid"`, the strict TOML loader (`--config`), cross-field validators, and startup token-file read kept in memory. (FR-018/019/020; data-model.md)
- [X] T014 [P] [US1] Implement `src/remo_cli/notifier/transports/base.py`: the `NotificationTransport` ABC (`start`/`stop`/`send_approval_request`/`cancel`/`healthy`) exactly per `contracts/transport.md`. (FR-015)
- [X] T015 [US1] Implement `src/remo_cli/notifier/state.py`: `PendingApproval` + `PendingApprovals` registry — Future-based, `asyncio.Lock`-guarded atomic capacity+id reservation with rollback on send failure, `resolve`/`cancel`/`count`/`drain`, exactly-once resolution. (depends on T006; FR-002/003a/008/009/034, FR-010a; research R2)
- [X] T016 [US1] Implement `src/remo_cli/notifier/transports/telegram.py`: PTB `Application` with long-polling started via `initialize`/`start`/`updater.start_polling` (never `run_polling`/webhook); send message + inline keyboard; `CallbackQueryHandler` with authorized-chat check and pending-guarded `on_response`; message edits per outcome; `cancel`; token from file. (depends on T006, T014; FR-010–014, research R1/R6; contracts/telegram-message.md)
- [X] T017 [US1] Implement `src/remo_cli/notifier/server.py`: FastAPI app + lifespan (start transport on startup, `drain()`+stop on shutdown); `POST /v1/approve` (validate→clamp timeout→reserve [dup→409, capacity→503]→send [failure→release+503]→register→`await wait_for`→resolve→200, timeout→408 fail-secure deny; transport unhealthy/shutting down→503); `GET /v1/health`. Map outcomes through one fail-secure resolver. (depends on T006/T013/T014/T015/T016; FR-001–010a, FR-016; research R2/R3/R4/R9)
- [X] T018 [US1] Implement `src/remo_cli/notifier/cli.py`: `remo-notifier` entry (`main`) with a `serve --config` command that loads config, configures logging, builds the selected transport, and runs `uvicorn.Server` on the configured host/port. (AC-1)
- [X] T019 [US1] Add a `SIGHUP` handler in `server.py`/`cli.py` that re-reads the token file and re-initializes the bot, for secret rotation without redeploy. (research R6; spec out-of-scope rotation note)

**Checkpoint**: `remo-notifier serve` runs the full approve/deny/timeout loop locally. MVP demonstrable.

---

## Phase 4: User Story 2 — Operator deploys the notifier to an instance (Priority: P1)

**Goal**: `remo notifier deploy <host>` applies the `remo_notifier` Ansible role end-to-end: preflight creds, render config + secret, build the image on the host, install/start the systemd unit, and confirm health — failing loudly on missing creds.

**Independent Test**: Against a fresh Ubuntu 24.04 host with only docker + base packages, run `remo notifier deploy <host>`; `remo-notifier.service` ends `active (running)` and `/v1/health` returns 200, with no manual steps. Missing creds abort cleanly.

### Tests for User Story 2 (write first)

- [X] T020 [P] [US2] `tests/notifier/test_cli_notifier.py` (deploy cases): patch `core.ansible_runner.run_playbook`, `core.known_hosts`, and `core.picker`; assert deploy resolves the host (and fuzzy-picks when omitted), aborts with a clear error when `REMO_NOTIFIER_TELEGRAM_*` are unset, invokes `notifier_deploy.yml` with `-i "{host},"` + `ansible_user`, and passes a rebuild extra-var when `--rebuild`. (validates T028; FR-022/023/024/031)

### Implementation for User Story 2

- [X] T021 [P] [US2] Create `ansible/roles/remo_notifier/defaults/main.yml` (image/version, listen port, bind address `172.17.0.1`, config/secrets dirs, instance id, timeouts, env-backed telegram token+chat id, `remo_notifier_build_from_source: true`, source dir). (spec §6)
- [X] T022 [P] [US2] Create `ansible/roles/remo_notifier/meta/main.yml` with `dependencies: [{role: docker}]`. (spec §6)
- [X] T023 [P] [US2] Create `ansible/roles/remo_notifier/templates/notifier.toml.j2` rendering all runtime config from role vars. (FR-020)
- [X] T024 [P] [US2] Create `ansible/roles/remo_notifier/templates/remo-notifier.service.j2`: `docker run --rm` with `--name`, `--network bridge`, `-p {{bind}}:{{port}}:18181`, config + secret read-only mounts, `--read-only`, `--tmpfs /tmp`, `--cap-drop ALL`, `--user 65532:65532`, `Restart=always`, `RestartSec=5`. (FR-021/026)
- [X] T025 [US2] Create `ansible/roles/remo_notifier/tasks/main.yml`: preflight `assert` non-empty token+chat id (FR-023); create `/etc/notifier`+`secrets` (0700); render config; write token (0400); copy the bundled build context to `remo_notifier_source_dir` **preserving its root-relative layout** so the Dockerfile's `COPY` paths resolve (U1); then `community.docker.docker_image` with `source: build` when `remo_notifier_build_from_source | default(true) | bool`, else `source: pull`; install unit; `daemon-reload`+enable+start; `ansible.builtin.uri` health-wait pinned to `retries: 10, delay: 2` (~20 s ceiling; healthy expected <5 s per SC-003) (FR-025). **All registered-var access uses `| default()`** (Constitution I); idempotent (`changed_when`/handlers). (depends on T021–T024)
- [X] T026 [P] [US2] Create `ansible/roles/remo_notifier/handlers/main.yml` with a `Restart remo-notifier` handler triggered by config/unit changes. (spec §6, Constitution III)
- [X] T027 [P] [US2] Create `ansible/notifier_deploy.yml` (`hosts: all`, `become: true`, applies role `remo_notifier`). (research R10)
- [X] T028 [US2] Implement the `notifier` Click group + `deploy` command in `src/remo_cli/cli/notifier.py`: resolve host via `core/known_hosts` + `core/picker` (fuzzy when omitted), preflight `REMO_NOTIFIER_TELEGRAM_*` env, resolve the bundled build-context path (the installed `remo_cli/notifier_build/` dir, à la `core/config.get_ansible_dir`) and pass it as an extra-var so the role copies it verbatim (U1), and call `run_playbook("notifier_deploy.yml", …)` with inventory/user/rebuild extra-vars. (FR-022/023/024/031; research R5/R8)
- [X] T029 [US2] Register the `notifier` group in `src/remo_cli/cli/main.py` (lazy import + `cli.add_command(notifier)`), matching the existing provider-registration pattern. (FR-032 — additive only)
- [X] T030 [P] [US2] Append the notifier secrets block (`remo_notifier_telegram_bot_token`/`_chat_id` via `lookup('env', …)`) to `ansible/group_vars/all.yml`. (spec §8)
- [X] T031 [P] [US2] Add a guarded `include_role: remo_notifier` to `ansible/tasks/configure_dev_tools.yml` under `when: configure_remo_notifier | default(false) | bool` (opt-in; default-off so a credential-less provision doesn't fail the notifier preflight — corrected after CI). (FR-033)
- [X] T031a [US2] Conditional-path coverage (Constitution II): verify the `configure_remo_notifier: false` path **skips** the role (no notifier dirs/unit created), and that the `remo_notifier_build_from_source: false` branch selects `source: pull` (assert via `--check`/`--list-tasks` or a molecule-style assertion that the build task is skipped and the pull task selected). If the pull path cannot be exercised without a published image, record it as explicitly deferred here rather than leaving the plan's "tested both ways" claim unbacked. (C1; depends on T025, T031)

**Checkpoint**: A fresh host goes to a running, health-passing notifier via one command.

---

## Phase 5: User Story 3 — Operator verifies wiring end-to-end (Priority: P2)

**Goal**: `remo notifier test <host>` pushes a clearly test-labeled approval through the full path and reports the returned decision.

**Independent Test**: Against a deployed host, `remo notifier test <host>` produces a test-labeled Telegram message; tapping a button prints the decision; an unreachable service is reported rather than hanging.

### Tests for User Story 3 (write first)

- [X] T032 [US3] Add `test` cases to `tests/notifier/test_cli_notifier.py`: patch the SSH/subprocess seam; assert the posted body carries `policy_rule_name="test"` and the canonical test `policy_message`, that the decision is rendered, and that an unreachable host yields a clear error (not a hang). (validates T033; FR-027)

### Implementation for User Story 3

- [X] T033 [US3] Add the `test` command to `src/remo_cli/cli/notifier.py`: resolve/fuzzy-pick the host, SSH to it and `curl` a POST of the canonical test `ApprovalRequest` (contracts/telegram-message.md test surface) to `http://{bind}:{port}/v1/approve`, render the decision, surface unreachable cleanly. (sequential after T028 — same file; FR-027/031; research R8)

**Checkpoint**: First-time wiring verifiable in one command.

---

## Phase 6: User Story 4 — Operator observes and controls a running notifier (Priority: P3)

**Goal**: `remo notifier status|logs|restart <host>` for health, logs, and restart, with fuzzy host picking.

**Independent Test**: Against a deployed host, `status` prints the health summary, `logs --follow` streams journald, `restart` returns the service to active; each offers a picker when no host is named.

### Tests for User Story 4 (write first)

- [X] T034 [US4] Add `status`/`logs`/`restart` cases to `tests/notifier/test_cli_notifier.py`: assert `status` SSH-curls `/v1/health` and renders JSON, `logs` runs `journalctl -u remo-notifier.service` with `-f`/`-n` flags, `restart` runs `systemctl restart`, and each fuzzy-picks when the host is omitted. (validates T035–T037; FR-028/029/030/031)

### Implementation for User Story 4

- [X] T035 [US4] Add the `status` command to `src/remo_cli/cli/notifier.py` (SSH `curl -sf …/v1/health`, render JSON). (sequential — shared file; FR-028/031)
- [X] T036 [US4] Add the `logs` command to `src/remo_cli/cli/notifier.py` (SSH `journalctl -u remo-notifier.service`, `--follow`→`-f`, `--lines N`→`-n N`, default `--lines 100` (A2)). (sequential — shared file; FR-029/031)
- [X] T037 [US4] Add the `restart` command to `src/remo_cli/cli/notifier.py` (SSH `sudo systemctl restart remo-notifier.service`). (sequential — shared file; FR-030/031)

**Checkpoint**: Full day-2 operability; all four user stories independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Documentation, quality gates, and acceptance validation spanning all stories.

- [X] T038 [P] Write `src/remo_cli/notifier/docs/wire-protocol.md` from `contracts/openapi.yaml`: request/response schema, all status codes (200/400/408/409/503), timeout contract, cancellation semantics. (spec §10, SC-008)
- [X] T039 [P] Write `src/remo_cli/notifier/docs/config-schema.md` documenting the TOML schema and secret-file handling. (spec §10)
- [X] T040 [P] Write `src/remo_cli/notifier/README.md` (distribution note + future-consumers paragraph pointing at `docs/wire-protocol.md`). (spec §10)
- [X] T041 Update top-level `README.md`: add "Notifier" and "Notifier setup" sections (bot/chat-id steps, env vars, deploy, test) and a wire-protocol pointer. (spec §10, Constitution V)
- [X] T042 Constitution pre-commit check: run `grep -rn '\.rc ==' ansible/roles/remo_notifier/` and `grep -rn '\.stdout' ansible/roles/remo_notifier/`; ensure every match uses `| default()`; fix any found. (Constitution I)
- [X] T043 [P] Run `ruff check src/remo_cli/notifier/` and `mypy src/remo_cli/notifier/`; resolve all findings. (AC-9)
- [X] T044 [P] Run `pytest tests/notifier/ --cov=remo_cli.notifier`; ensure >85% line coverage; add tests for any gap. (AC-8)
- [X] T044a [P] Packaging assertion (SC-007/G3): confirm a base `pip install remo-cli` (without the `[notifier]` extra) does **not** pull fastapi/uvicorn/python-telegram-bot/structlog — e.g. a test that imports the laptop CLI path with those modules absent, or inspects resolved base dependencies.
- [X] T045 Build and check the image: `docker build -t remo-notifier:0.1.0 -f notifier/Dockerfile .`; verify `<250 MB` and that a container answers `/v1/health` 200 within 5 s. (AC-2)
- [ ] T045a Idempotency rerun (Constitution III): run `remo notifier deploy <host>` (or the role) twice against the same host; assert the second run reports no changed config/secret/unit tasks and the service stays `active (running)`. (C2) — **DEFERRED: requires a live remote host; cannot run in this dev environment. The role uses `changed_when`/handlers + `force_source: false`, so re-runs are designed to be no-ops.**
- [ ] T046 Run the `quickstart.md` operator + developer validations end-to-end against a real host and bot, confirming SC-001..SC-007 and AC-3..AC-7. — **DEFERRED: requires a live remote host + a real Telegram bot. Local equivalents are covered: image build + container `/v1/health` (AC-2 ✓), full approve/deny/timeout loop in tests (SC-001/002/005 ✓), packaging isolation (SC-007 ✓).**

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (P1: T001–T004)** → no deps; T003/T004 parallel after T001.
- **Foundational (P2: T005–T007)** → after Setup. **Blocks all stories.**
- **US1 (P3)** → after Foundational. The MVP.
- **US2 (P4)** → after Foundational; needs T002 force-include and T003 Dockerfile from Setup. The deploy *builds* the image that packages US1, so a real end-to-end deploy (T046) needs US1 merged, but US2's role/CLI/tests can be built in parallel with US1.
- **US3 (P5)** → after US2 (uses `notifier` group + a deployed host; `test` command shares `cli/notifier.py` with `deploy`).
- **US4 (P6)** → after US2 (shares `cli/notifier.py`); independent of US3.
- **Polish (P7)** → after the stories it documents/validates.

### Within US1

T006/T005 (foundational) → T013/T014 [P] → T015 (needs models) → T016 (needs models+ABC) → T017 (needs config/state/transport) → T018 → T019. Tests T008–T012 written first and fail until their targets land.

### Shared-file serialization

- `src/remo_cli/cli/notifier.py`: T028 → T033 → T035 → T036 → T037 (one file; sequential).
- `tests/notifier/test_cli_notifier.py`: T020 → T032 → T034 (one file; sequential).

---

## Parallel Opportunities

- **Setup**: T003, T004 in parallel (after T001).
- **Foundational**: T005, T006 in parallel; T007 after T006.
- **US1 tests**: T008–T012 all [P] (distinct files).
- **US1 impl**: T013, T014 in parallel; then the T015→T017 chain.
- **US2**: role files T021, T022, T023, T024, T026, T027 and ansible edits T030, T031 all [P] (distinct files); T025 after the templates; T028 after T020; T029 after T028.
- **Polish**: T038, T039, T040 in parallel; T043, T044 in parallel.

### Parallel example — US1 tests

```bash
Task: "conftest fixtures in tests/notifier/conftest.py"          # T008
Task: "test_config.py"                                            # T009
Task: "test_state.py"                                             # T010
Task: "test_server.py"                                            # T011
Task: "test_telegram.py"                                          # T012
```

### Parallel example — US2 role scaffolding

```bash
Task: "defaults/main.yml"            # T021
Task: "meta/main.yml"                # T022
Task: "templates/notifier.toml.j2"   # T023
Task: "templates/remo-notifier.service.j2"  # T024
Task: "handlers/main.yml"            # T026
Task: "notifier_deploy.yml"          # T027
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Setup (T001–T004) → Foundational (T005–T007) → US1 (T008–T019).
2. **STOP & VALIDATE**: run `remo-notifier serve` locally against a real bot; confirm allow/deny/timeout (SC-001, SC-002, SC-005). This is a demonstrable MVP without any host deployment.

### Incremental delivery

1. MVP (US1) → demo the approval loop locally.
2. US2 → `remo notifier deploy` stands it up on a host (AC-3, SC-003).
3. US3 → `remo notifier test` one-command wiring check (SC-004).
4. US4 → day-2 status/logs/restart.
5. Polish → docs + quality gates + acceptance run.

### Suggested parallel team split (after Foundational)

- **Dev A**: US1 (service core) — the critical path.
- **Dev B**: US2 (Ansible role + Dockerfile + deploy CLI) — can scaffold immediately; integrates with US1's image at deploy time.
- **Dev C**: US3 + US4 CLI commands — start once the `notifier` group (T028/T029) exists.

---

## Task count

- Setup: 4 (T001–T004)
- Foundational: 4 (T005–T007, T007a)
- US1 (P1, MVP): 12 (T008–T019)
- US2 (P1): 13 (T020–T031, T031a)
- US3 (P2): 2 (T032–T033)
- US4 (P3): 4 (T034–T037)
- Polish: 11 (T038–T046, T044a, T045a)

**Total: 50 tasks.** (46 original + 4 remediation: T007a, T031a, T044a, T045a)
