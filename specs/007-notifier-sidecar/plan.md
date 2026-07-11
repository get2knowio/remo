# Implementation Plan: Notifier Sidecar — Telegram approval bridge for agentsh

**Branch**: `007-notifier-sidecar` | **Date**: 2026-05-31 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/007-notifier-sidecar/spec.md`

## Summary

Add a self-contained notifier service to the existing `remo-cli` package: a long-running FastAPI daemon that runs in a hardened OCI container on each remo-provisioned host. It accepts agentsh approval requests over HTTP, delivers them to one authorized human via a Telegram bot (long-polling, no public URL), and returns the human's allow/deny decision synchronously — failing secure (deny) on timeout, shutdown, send failure, or capacity exhaustion. The service holds no persistent state.

Technical approach: a new `src/remo_cli/notifier/` sub-package (a fourth peer of `cli/`, `providers/`, `core/`) carries the server, config, in-memory pending-approval registry, wire-protocol models, and a pluggable `NotificationTransport` ABC with a single Telegram implementation. The Telegram `Application` runs long-polling inside the FastAPI lifespan on the same asyncio loop. A new `remo_notifier` Ansible role (depends-on `docker`) builds the image on the host from a build context shipped inside the wheel, renders config + secret, and installs a systemd unit that runs `docker run` bound to the Docker bridge address. New `remo notifier {deploy,status,logs,test,restart}` CLI subcommands drive deployment and day-2 ops using the existing host-resolution, picker, SSH, and ansible-runner helpers. The notifier's runtime deps live behind a `[notifier]` optional extra so the laptop install is unaffected.

## Technical Context

**Language/Version**: Python 3.11+ (package `requires-python = ">=3.11"`); service container runs Python 3.13-slim  
**Primary Dependencies**: Service (new `[notifier]` extra): FastAPI ≥0.115, uvicorn[standard] ≥0.32, pydantic ≥2.9, python-telegram-bot ≥21.6, structlog ≥24.4, tomli (py<3.11 only). CLI side: Click ≥8.1 (existing), no new laptop runtime deps. Build: hatchling (existing), uv (in-container). Ansible: new `community.docker` collection.  
**Storage**: None. All approval state is in-memory in a `PendingApprovals` registry; never persisted (FR-009).  
**Testing**: pytest (existing) + new dev deps pytest-asyncio and httpx (FastAPI `TestClient`/`AsyncClient`). Tests under `tests/notifier/`.  
**Target Platform**: Service: Linux/amd64 OCI container on Ubuntu 24.04 hosts. Operator CLI: cross-platform (existing remo install).  
**Project Type**: Single project — Python package (`src/remo_cli/`) + Ansible (`ansible/`), matching existing repo layout.  
**Performance Goals**: Decision delivered to caller within 5 s of a human tap (SC-001); timeout-deny returned within ~1 s of the request deadline (SC-002); health check answers within 5 s of container start (SC-003).  
**Constraints**: Container image <250 MB (SC/AC-2); listener bound to the Docker bridge address only, no TLS, no caller auth (FR-021); no secrets at INFO+ log level (FR-017); fail-secure — never "allow" except an explicit human tap (FR-008); no persistence (FR-009); default max 50 concurrent pending approvals (FR-034).  
**Scale/Scope**: One authorized Telegram chat per instance; ~600–900 LOC Python, ~150 LOC Ansible/Jinja, ~50 LOC CLI; configurable cap (default 50) on concurrent pending approvals per instance.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The constitution (v1.0.0) is centered on Ansible quality plus fail-fast and docs-reflect-reality. Evaluating the new `remo_notifier` role and surrounding work:

| Principle | Gate | Status |
|-----------|------|--------|
| I. Defensive Variable Access (Ansible) | All registered-var attribute access in `remo_notifier` role uses `\| default()`; `.rc`/`.stdout` never bare; `when:` guards use `is defined` / `\| default()` | PASS — committed; pre-commit grep (`grep -r '\.rc ==' ansible/`, `grep -r '\.stdout' ansible/`) in the task list |
| II. Test All Conditional Paths | Role toggles (`configure_remo_notifier`, `remo_notifier_build_from_source`) and Python fail-secure branches (timeout, send-failure, capacity, shutdown, duplicate-id) are each tested both ways | PASS — Python branches covered by T010/T011 (incl. timeout-clamp); role toggle/pull paths covered by **T031a** (or explicitly deferred there) |
| III. Idempotent by Default | Role re-run yields identical state: config/secret templates use `changed_when`/handlers; image build is conditional; `docker run` via systemd is declarative; health-wait is a read-only check | PASS — verified by the rerun check in **T045a** |
| IV. Fail Fast with Clear Messages | Pre-flight `assert` for empty bot token / chat id with actionable `fail_msg` (FR-023); config strict-validation rejects unknown keys with a clear error (FR-018); deploy fails if health never comes up (FR-025) | PASS |
| V. Documentation Reflects Reality | wire-protocol.md + config-schema.md + notifier README + top-level README "Notifier" section land with the code; examples are runnable (FR/req 10) | PASS |

No violations. **Complexity Tracking** is empty (no deviations to justify).

Post-Phase-1 re-check: design keeps the notifier strictly additive (no edits to `docker`/`devcontainers`/provider roles per spec constraint), the role self-contained with a single `meta` dependency on `docker`, and the laptop install free of service deps — all consistent with the gates above. **Still PASS.**

## Project Structure

### Documentation (this feature)

```text
specs/007-notifier-sidecar/
├── plan.md              # This file
├── spec.md              # Feature spec (already written + clarified)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── openapi.yaml      # HTTP wire protocol (POST /v1/approve, GET /v1/health)
│   ├── transport.md      # NotificationTransport ABC contract
│   └── telegram-message.md # Telegram message + callback_data contract
├── checklists/
│   └── requirements.md  # Spec quality checklist (done)
└── tasks.md             # /speckit.tasks output (NOT created here)
```

### Source Code (repository root)

```text
src/remo_cli/
├── notifier/                    # NEW — fourth layer, peer of cli/ providers/ core/
│   ├── __init__.py
│   ├── cli.py                   # `remo-notifier serve` entry point (Click or argparse)
│   ├── server.py                # FastAPI app, lifespan (start/stop transport), routes
│   ├── config.py                # Pydantic config models + strict TOML loader
│   ├── state.py                 # PendingApprovals registry (asyncio, cap-aware)
│   ├── models.py                # ApprovalRequest/Response/Operation/Decision (Pydantic)
│   ├── logging_setup.py         # structlog config; secret-safe processors
│   ├── transports/
│   │   ├── __init__.py
│   │   ├── base.py              # NotificationTransport ABC
│   │   └── telegram.py          # Telegram Application (long-polling) implementation
│   ├── docs/
│   │   ├── wire-protocol.md
│   │   └── config-schema.md
│   └── README.md
├── cli/
│   └── notifier.py              # NEW — `remo notifier {deploy,status,logs,test,restart}`
├── cli/main.py                  # MODIFIED — register the `notifier` group
├── core/ providers/ models/     # UNCHANGED (constraint: no notifier code here)
└── ansible/                     # force-included into wheel (existing mechanism)

ansible/                         # (mirrors into wheel at remo_cli/ansible)
├── roles/remo_notifier/         # NEW role
│   ├── tasks/main.yml
│   ├── defaults/main.yml
│   ├── handlers/main.yml
│   ├── meta/main.yml            # dependencies: [docker]
│   └── templates/
│       ├── remo-notifier.service.j2
│       └── notifier.toml.j2
├── notifier_deploy.yml          # NEW playbook: apply remo_notifier to a target host
├── tasks/configure_dev_tools.yml# MODIFIED — include remo_notifier when toggled
├── group_vars/all.yml           # MODIFIED — notifier vars (env-backed secrets)
└── requirements.yml             # MODIFIED — add community.docker collection

notifier/
└── Dockerfile                   # NEW — multi-stage build (repo root, NOT under src/)

tests/notifier/                  # NEW — mirrors the package
├── __init__.py
├── conftest.py                  # fixtures: fake transport, test config, async loop
├── test_models.py
├── test_config.py
├── test_state.py                # registry: register/cancel/timeout/resolve/cap/dup-id
├── test_server.py               # all status codes; TestClient + AsyncClient
├── test_telegram.py             # Bot mock; message/keyboard/callback/edit/cancel
└── test_cli_notifier.py         # subcommands with mocked SSH + fake host registry

pyproject.toml                   # MODIFIED — [notifier] extra, remo-notifier script,
                                 #   dev deps (pytest-asyncio, httpx), force-include build ctx
README.md                        # MODIFIED — "Notifier" + "Notifier setup" sections
```

**Structure Decision**: Single-project layout, matching the existing repo. The notifier is an additive fourth peer package under `src/remo_cli/notifier/`; the only edits to existing files are pure additions/registrations (`cli/main.py`, `pyproject.toml`, `group_vars/all.yml`, `tasks/configure_dev_tools.yml`, `requirements.yml`) so no existing command's behavior changes (FR-032). The Ansible role is self-contained with one `meta` dependency on `docker`; no existing role is modified (spec constraint).

## Complexity Tracking

> No constitution violations — section intentionally empty.
