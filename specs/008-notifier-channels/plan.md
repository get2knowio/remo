# Implementation Plan: Notifier Channels — interchangeable delivery channels for the notifier sidecar

**Branch**: `008-notifier-channels` | **Date**: 2026-06-01 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/008-notifier-channels/spec.md`

## Summary

Generalize the spec-007 notifier so the notifier responsibility can be fulfilled by any one of several interchangeable **channels**, with Telegram as the first, **and** re-point the approval source from 007's invented `/v1/approve` schema to agentsh's real approval REST API. The channel-agnostic service (agentsh approver-client poll/resolve, the `PendingApprovals` registry, capacity handling, standing grants, fail-secure resolution, structured logging, `/v1/health`) becomes the shared **notifier core**; each delivery medium becomes a thin channel package built into its own image over that core. Operators install a channel only via the explicit `remo notifier deploy` command, choosing it by name or from a fuzzy catalog picker, with a per-channel credential preflight.

Technical approach (low-churn, leaning on existing seams):

1. **Channel package split**: `transports/telegram.py` and the Telegram config model move under a new `channels/telegram/` package; the channel-agnostic modules (`server.py`, `state.py`, `models.py`, `grants.py`, `logging_setup.py`) and the transport ABC (`transports/base.py`) stay put and are the **core** (they already import nothing Telegram-specific, so adding a channel never touches them — SC-002).
2. **Channel catalog**: a new import-light `channels/catalog.py` lists `ChannelDescriptor` entries (id, label, image name, required env vars, secret mapping, transport factory ref, transport-TOML renderer). The laptop CLI imports only this catalog (no FastAPI/telegram), satisfying the no-new-laptop-deps constraint.
3. **Generic transport config**: core's `TransportConfig` becomes `{type, settings}`; the selected channel's own Pydantic model validates `settings`. Telegram's `[transport.telegram]` TOML shape is preserved byte-for-byte (FR-017/FR-018).
4. **Per-channel images**: one parameterized Dockerfile takes a `CHANNEL` build arg selecting a per-channel extra (`.[notifier-telegram]`), producing `remo-notifier-<channel>:<version>`. A channel's delivery deps live only in its extra/image (SC-006).
5. **CLI**: `remo notifier deploy` gains `--channel` + catalog picker + per-channel env preflight; new `remo notifier channels` lists the catalog; the `configure_remo_notifier` provisioning toggle is **removed** (FR-009a).
6. **Ansible role**: parameterized by `remo_notifier_channel` — channel-specific image tag and a transport-TOML fragment supplied by the CLI from the descriptor; the single `remo-notifier.service`, bridge bind, and port are unchanged (FR-013/FR-014).
7. **agentsh edge (core)**: a new `agentsh_client` in the core polls `GET /api/v1/approvals` and resolves via `POST /api/v1/approvals/{id}` with an approver `X-API-Key`; the 007 `/v1/approve` push endpoint is removed. agentsh's `Request` replaces the invented request model; an optional notification webhook is an untrusted "poll now" trigger only. See contracts/agentsh-integration.md.

Telegram's **delivery** behavior (messages, buttons, grants, `/rules` `/revoke` `/pause`, SIGHUP token reread) is preserved; the approval content it renders now comes from agentsh's `Request`.

## Technical Context

**Language/Version**: Python 3.11+ (`requires-python = ">=3.11"`); service container runs Python 3.13-slim (unchanged from 007).
**Primary Dependencies**: Reorganized extras — `notifier-core` (FastAPI ≥0.115, uvicorn[standard] ≥0.32, pydantic ≥2.9, structlog ≥24.4, **httpx ≥0.27** for the agentsh approver client, tomli on py<3.11); `notifier-telegram` = core + python-telegram-bot ≥21.6; `notifier` retained as an alias of `notifier-telegram` for back-compat. CLI/laptop side: Click ≥8.1, InquirerPy (existing) — **no new laptop runtime deps** (catalog is pure-Python metadata). Build: hatchling + uv (in-container). Ansible: `community.docker` (already added in 007).
**Storage**: None. All approval and grant state remains in-memory (FR-009 / 007 carried forward).
**Testing**: pytest + pytest-asyncio + httpx (existing). New tests for the catalog, CLI channel selection, generic transport config, and a **stub second channel** under `tests/` that proves US3 (add-a-channel touches no core/Telegram files) without shipping Slack.
**Target Platform**: Service: Linux/amd64 OCI container on Ubuntu 24.04 hosts. Operator CLI: cross-platform (existing remo install).
**Project Type**: Single project — Python package (`src/remo_cli/`) + Ansible (`ansible/`), matching the existing repo layout.
**Performance Goals**: Unchanged from 007 (decision ≤5 s of a tap; timeout-deny within ~1 s of deadline; health ≤5 s of start).
**Constraints**: Approval contract is agentsh's REST API, not an invented one (FR-018/FR-020..FR-023), pinned to a verified agentsh version; a channel can only fail to deliver, never allow (FR-007/FR-008, enforced in core); one channel per host on the single service/bind/port (FR-013/FR-014); each channel's delivery deps isolated to its image (SC-006); laptop install acquires no channel deps (FR-019); install only via explicit command (FR-009a).
**Scale/Scope**: One channel ships (Telegram). ~250–400 LOC net (mostly moves + catalog + CLI selection + config generalization), ~40 LOC Ansible/Jinja deltas. Catalog built for the second channel to be a drop-in.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Defensive Variable Access (Ansible) | `remo_notifier` role changes (channel-parameterized image/toml) keep `\| default()` on every registered-var access; pre-commit greps (`grep -r '\.rc ==' ansible/`, `grep -r '\.stdout' ansible/`) stay clean | PASS — enforced via a task in the task list |
| II. Test All Conditional Paths | Channel-selection branches each tested: named / picker / non-interactive-no-channel / unknown-channel / missing-credentials / single-vs-multi catalog; role `remo_notifier_channel` path; config `type`-dispatch both for telegram and a stub type | PASS — covered by new CLI + config + catalog tests and a role-toggle test |
| III. Idempotent by Default | Role re-run yields identical state; per-channel image build is conditional (`force_source`); channel switch is a declarative image swap on one service; health-wait is read-only | PASS — verified by a rerun check task |
| IV. Fail Fast with Clear Messages | Per-channel credential preflight names exactly the missing `REMO_NOTIFIER_<CHANNEL>_*` vars (FR-012); unknown channel lists available ones (FR-010); non-interactive-no-channel fails with guidance (FR-011); strict config still rejects unknown keys | PASS |
| V. Documentation Reflects Reality | README "Notifier" section updated for channel selection + `remo notifier channels`; new channel-extension contract doc; wire-protocol stability note; quickstart for operator and channel-author; examples runnable | PASS |

No violations. **Complexity Tracking** is empty.

**Post-Phase-1 re-check**: The design keeps the core untouched when adding channels (channels live under `channels/`, registered via a catalog entry — the explicitly-permitted step), preserves Telegram's delivery behavior, replaces the invented wire protocol with agentsh's real API (contracts/agentsh-integration.md), and adds no laptop runtime deps. The one-time changes (config `{type, settings}` + `[agentsh]` section, the agentsh approver client + httpx, parameterized Dockerfile, role channel var, removal of `/v1/approve`) are confined to this feature. **Still PASS.**

## Project Structure

### Documentation (this feature)

```text
specs/008-notifier-channels/
├── plan.md              # This file
├── spec.md              # Feature spec (clarified)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── channel-descriptor.md     # The catalog descriptor contract
│   ├── channel-extension.md      # "Add a channel" contract (zero core edits)
│   ├── cli-notifier.md           # deploy --channel / channels CLI surface
│   └── agentsh-integration.md    # Verified agentsh approval REST contract the core consumes
├── checklists/
│   └── requirements.md  # Spec quality checklist (done)
└── tasks.md             # /speckit.tasks output (NOT created here)
```

### Source Code (repository root)

```text
src/remo_cli/notifier/
├── __init__.py                 # __version__ (unchanged)
├── cli.py                      # MODIFIED — build_transport() dispatches via the catalog
├── server.py                   # MODIFIED — drop POST /v1/approve; keep GET /v1/health; run the agentsh poll loop in lifespan
├── agentsh_client.py           # NEW (CORE) — approver client: poll GET /api/v1/approvals, POST decision; optional webhook trigger
├── state.py                    # CORE — unchanged (tracks approvals awaiting a human)
├── models.py                   # MODIFIED — adopt agentsh Request fields; drop invented /v1/approve request body
├── grants.py                   # CORE — unchanged
├── logging_setup.py            # CORE — unchanged
├── config.py                   # MODIFIED — TransportConfig -> {type, settings}; Telegram model removed; add [agentsh] section (api_url, api_key_file, poll_interval, webhook)
├── transports/
│   ├── __init__.py
│   └── base.py                 # CORE — the channel contract (NotificationTransport ABC), unchanged
└── channels/                   # NEW — catalog + per-channel packages
    ├── __init__.py
    ├── base.py                 # ChannelDescriptor dataclass + RequiredEnv + protocol (import-light)
    ├── catalog.py              # CHANNELS = [telegram_descriptor, ...]; lookup()/list helpers
    └── telegram/               # NEW — Telegram channel package
        ├── __init__.py
        ├── transport.py        # MOVED from transports/telegram.py
        ├── config.py           # MOVED Telegram Pydantic model out of core config.py
        └── descriptor.py       # ChannelDescriptor: id, label, image, required env, toml renderer

notifier/
├── Dockerfile                  # MODIFIED — ARG CHANNEL; installs ".[notifier-${CHANNEL}]"
└── (optional per-channel Dockerfile override only if a channel needs extra system deps)

ansible/
├── roles/remo_notifier/
│   ├── defaults/main.yml       # MODIFIED — remo_notifier_channel (default telegram); image tag templated by channel
│   ├── tasks/main.yml          # MODIFIED — build/run channel image; render transport fragment from CLI-supplied var
│   ├── templates/
│   │   ├── remo-notifier.service.j2  # MODIFIED — image = remo-notifier-{{channel}}:{{version}} + CHANNEL build arg
│   │   └── notifier.toml.j2          # MODIFIED — generic [transport] type + injected transport fragment
│   ├── handlers/main.yml       # unchanged (single service)
│   └── meta/main.yml           # unchanged (depends: docker)
├── group_vars/all.yml          # MODIFIED — channel-namespaced creds; drop configure_remo_notifier default
├── tasks/configure_dev_tools.yml  # MODIFIED — remove the notifier include (FR-009a)
└── notifier_deploy.yml         # unchanged (role still applied; channel passed as extra-var)

src/remo_cli/cli/notifier.py    # MODIFIED — --channel + picker + per-channel preflight + `channels` subcommand

tests/notifier/
├── core/                       # existing server/state/models/grants tests (relocated/renamed as needed)
├── channels/
│   ├── telegram/               # existing telegram transport tests (relocated)
│   └── test_stub_channel.py    # NEW — registers a fake channel, proves catalog + zero-core-edit (US3)
├── test_catalog.py             # NEW
├── test_config.py              # MODIFIED — generic {type, settings} + telegram round-trip
└── test_cli_notifier.py        # MODIFIED — channel selection branches

pyproject.toml                  # MODIFIED — notifier-core / notifier-telegram extras (+ notifier alias)
README.md                       # MODIFIED — channel selection, `remo notifier channels`
```

**Structure Decision**: Single-project layout, unchanged. The notifier core stays in place (it is already channel-free) and a new `channels/` subpackage holds the catalog and per-channel packages; Telegram is the first entry. This minimizes diff/churn (protecting the non-regression goal) while making "add a channel" a self-contained drop-in whose diff never touches core files — the reviewable form of SC-002. The transport ABC remains the channel contract.

## Complexity Tracking

> No constitution violations — section intentionally empty.
