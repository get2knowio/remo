# Implementation Plan: CLI-to-Web Adoption

**Branch**: `011-web-adopt` | **Date**: 2026-07-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/011-web-adopt/spec.md`

## Summary

Let a user whose workstation `remo` CLI already manages bootstrapped instances
hand their configuration off to a freshly deployed remo-web container with one
command — without the personal SSH private key ever leaving the workstation.
The service boots into a first-class "unconfigured / awaiting adoption" state
on a writable state volume, generates its own service-scoped ed25519 identity,
and exposes a token-gated setup API (`/api/v1/setup/*`, bearer
`REMO_WEB_API_TOKEN`, fail-closed). A new `remo web adopt` command pushes the
registry (mirror semantics) and workstation-verified SSH host keys, authorizes
the service's public key on every direct-access instance via the user's
existing SSH access (idempotent, marker-tagged `authorized_keys` entries), and
finishes with a server-side verification pass rendered as a per-instance
PASS/FAIL report. A follow-up `remo web push` re-syncs using credentials saved
at adopt time. The existing read-only bind-mount deployment mode is unchanged.

## Technical Context

**Language/Version**: Python 3.11+ (backend + CLI), TypeScript 5 / React 18 (frontend)

**Primary Dependencies**: Click (CLI), FastAPI/Uvicorn + pydantic v2 (`web`
extra, service side only), stdlib `urllib.request` for the CLI's HTTP calls
(no new runtime deps — matches the hetzner provider precedent), OpenSSH client
tools (`ssh`, `ssh-keygen`, `ssh-keyscan`) as subprocesses on both sides,
Vite + ghostty-web (frontend, unchanged)

**Storage**: Flat files in the service's writable state volume
(`~/.config/remo` inside the container): existing colon-delimited registry,
new `web-identity/` subdirectory (service keypair, service-managed SSH
`known_hosts`), all written atomically (temp file + rename, same pattern as
`core/known_hosts.py`). Workstation side: saved adoption credentials at
`~/.config/remo/web-service.json` (0600)

**Testing**: pytest (unit: setup API via starlette TestClient + httpx2,
state detection, token auth, adopt orchestration with mocked subprocess/HTTP;
integration: adopt against a live `remo web serve` on localhost),
`tests/image/` container tests for unconfigured boot (REMO_RUN_IMAGE_TESTS
gate), mypy + ruff, frontend `npm run lint`

**Target Platform**: Linux container (amd64/arm64) for the service;
Linux/macOS workstation for the CLI

**Project Type**: Existing three-layer CLI (`cli/` → `providers|web/` →
`core/`) + FastAPI service + React SPA — this feature adds to all three plus
docker packaging

**Performance Goals**: Adoption of 10 reachable instances completes in
< 2 min end-to-end (SC-002); per-instance operations run with bounded
timeouts and independent failure isolation (mirrors discovery's semantics);
unconfigured boot reaches ready-to-adopt in < 30 s (SC-006)

**Constraints**: Fail-closed token gating (no token → setup surface disabled);
no private key material in transit either direction (FR-007); constant-time
token comparison + existing log-redaction guarantees (FR-022); read-only
root filesystem and current container hardening flags must keep working;
`remo` CLI without the `web` extra must still import (NFR-008 lazy-import
discipline — adopt/push live in `cli/web.py` but must not require FastAPI)

**Scale/Scope**: Home-lab scale — tens of instances, single administrator,
last-write-wins pushes (no merge semantics)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|-----------|------------|
| I. Defensive Variable Access (Ansible) | PASS (N/A) — no Ansible playbook or role changes in this feature. |
| II. Test All Conditional Paths | PASS — the state machine (unconfigured / adopted / mount-configured / broken) and token gating (set / unset / wrong) are explicitly enumerated in spec acceptance scenarios; plan requires tests for every branch, including RO-mount regression tests. |
| III. Idempotent by Default | PASS — FR-011/FR-015 make idempotence a hard requirement: marker-tagged replaceable authorized_keys entries, mirror-semantics registry push, atomic server-side apply, safely re-runnable adopt. |
| IV. Fail Fast with Clear Messages | PASS — fail-closed token gating, explicit mount-configured rejection with machine-readable reason, empty-registry guard, per-instance skip reasons, tunnel fallback error explaining Host allowlist requirements. |
| V. Documentation Reflects Reality | PASS — FR-028/FR-029 make doc updates part of the feature (compose example, web docs, rotation + de-authorization procedures, reverse-proxy caveat). |

**Post-Phase-1 re-check**: PASS — design artifacts introduce no violations;
no Complexity Tracking entries required.

## Project Structure

### Documentation (this feature)

```text
specs/011-web-adopt/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── setup-api.md     # REST contract for /api/v1/setup/*
│   └── cli-web-adopt.md # CLI contract for remo web adopt / push
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   └── web.py                    # + adopt / push commands (workstation side;
│                                 #   stdlib-only, no web-extra imports)
├── core/
│   ├── ssh.py                    # build_ssh_opts(): optional identity_file /
│   │                             #   known_hosts_file threading (default None
│   │                             #   = today's behavior, unchanged)
│   └── web_adopt.py              # NEW: workstation-side adoption logic
│                                 #   (registry snapshot, keyscan+verify,
│                                 #   authorized_keys management, HTTP client,
│                                 #   --via tunnel, saved credentials)
└── web/
    ├── config.py                 # + api_token, state-dir-derived paths
    ├── state.py                  # NEW: configuration-state detection
    │                             #   (unconfigured/adopted/mounted/broken)
    │                             #   + service keypair generation
    ├── health.py                 # readiness gains "unconfigured" state;
    │                             #   identity candidates include state dir
    ├── check.py                  # `remo web check` understands unconfigured
    ├── discovery.py              # SSH opts pick up service identity/known_hosts
    ├── terminal.py               # (same threading via build_ssh_base_cmd args)
    ├── logging_config.py         # + Authorization-header redaction pattern
    └── api/
        └── setup.py              # NEW: /api/v1/setup/* router (status,
                                  #   identity, registry PUT, verify) with
                                  #   bearer-token dependency (fail closed)

frontend/src/
├── api/client.ts                 # + service-state awareness (ready payload)
└── components/
    └── AwaitingAdoption.tsx      # NEW: unconfigured-state page

docker/
├── Dockerfile                    # unchanged runtime; docs comments only
├── entrypoint.sh                 # check gate must pass in unconfigured state
└── compose.example.yml           # + named state-volume variant + token env

tests/
├── unit/web/                     # state detection, setup API, token auth,
│   │                             #   redaction, health states
│   └── cli/                      # adopt/push command behavior (mocked)
├── unit/core/                    # web_adopt: keyscan verify, marker mgmt,
│                                 #   payload build, saved credentials
├── integration/                  # adopt E2E against local serve (no docker)
└── image/                        # unconfigured-boot container test

docs/
└── web-session-interface.md      # + adoption workflow, state volume, token,
                                  #   rotation & de-authorization procedures
```

**Structure Decision**: Extend the existing three-layer layout in place. The
workstation-side adoption logic goes in `core/web_adopt.py` (business logic,
no Click imports, stdlib HTTP only) so `cli/web.py` stays a thin Click layer
and nothing new leaks into the no-web-extra import path. The service-side
state machine gets its own `web/state.py` module consumed by `health.py`,
`check.py`, and the new `web/api/setup.py` router.

## Complexity Tracking

No constitution violations — table not required.
