# Implementation Plan: Remo Web Session Interface

**Branch**: `010-web-session-interface` | **Date**: 2026-07-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/010-web-session-interface/spec.md`

## Summary

Add a home-lab Docker web service — packaged inside the existing Remo repo as an optional `web` extra and a `remo web` command group — that reads a read-only mount of the Remo registry, discovers projects/session state across all instances via a new versioned `remo-host` command over SSH, and brokers many interactive browser terminals, one server-side PTY + SSH attachment each, over WebSockets. It reuses/refactors `core/ssh.py` (KnownHost resolution, direct vs SSM targeting, multiplexing) so a project opened in the browser lands in the **same** Zellij/devcontainer session as `remo shell -p <project>`. The terminal emulator is `ghostty-web` behind a Remo-owned adapter (xterm.js fallback preserved). The MVP boundary is a single trusted user on a LAN/tailnet, with Host/Origin validation and short-lived single-use WebSocket tokens.

## Technical Context

**Language/Version**: Python 3.11+ (backend, matches existing CLI); TypeScript ES2022 (frontend); Bash (remo-host host command, matching existing `project-launch`/`project-menu` style)

**Primary Dependencies**:
- Backend (new `web` extra, lazily imported): `fastapi`, `uvicorn[standard]`, `websockets` (via Starlette), `pydantic` (v2, transitive via FastAPI). Reuse existing `boto3`/`hcloud` for SSM/region behavior already resolved through `core/ssh.py`.
- Frontend: `ghostty-web` **pinned 0.4.0** (+ its WASM asset served locally), `xterm` (fallback, behind same adapter), Vite + React + TypeScript build.
- Host command `remo-host`: pure Bash + coreutils + `zellij` + `docker` (all already present on instances); no new runtime added.

**Storage**: None server-side (all runtime state ephemeral per NFR-006). Browser `localStorage` holds workspace layout/preferences only. Registry is read-only input.

**Testing**: `pytest` + `pytest-asyncio` (backend unit/integration), disposable SSH containers implementing `remo-host` (integration/e2e), Playwright (browser: grid/tab/focus, keyboard routing, reconnect, Origin, WASM load), existing `tests/unit/test_ansible_templates.py` pattern + a fresh/existing-host idempotency harness (Ansible), `docker buildx` multi-arch image tests.

**Target Platform**: Linux Docker host, amd64 **and** arm64. Browsers: current desktop + tablet, basic mobile keyboard input.

**Project Type**: Web (backend service + frontend SPA) added inside the existing single-repo CLI (`src/remo_cli`), plus an Ansible-installed host command.

**Performance Goals** (from spec SC-010…SC-014 / NFRs): discovery of 3 instances / 9 projects renders incrementally < 10 s; first warm-session output < 5 s; web-introduced keystroke-echo latency < 100 ms p95; ≥ 9 concurrent terminals stable for 1 h with no cross-routing, leaks, or unbounded memory; graceful shutdown reaps attachments and leaves remote Zellij sessions intact.

**Constraints**: read-only root filesystem, non-root UID/GID, dropped caps, no-new-privileges, bounded tmpfs for SSH ControlPath + runtime; no Docker socket; strict host-key checking for direct SSH; `BatchMode` (non-interactive) auth; secrets/tokens/proxy-commands never logged; normal CLI path MUST NOT import FastAPI/Uvicorn/ghostty/Node (NFR-008); no server DB.

**Scale/Scope**: Single trusted operator; motivating example 3 instances × 3 projects = 9 targets; default terminal caps 32 global / 16 per-client (configurable, from Clarifications).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 principles (Ansible-centric) + repo three-layer rule from CLAUDE.md:

| Principle | Applicability & Compliance |
|---|---|
| I. Defensive Variable Access (Ansible) | The only new Ansible surface is the `remo-host` install task in the `user_setup` role. All registered-var accesses will use `\| default()` (e.g. `install_result.rc \| default(1)`), following existing tasks. **PASS (design-bound).** |
| II. Test All Conditional Paths | remo-host install task tested on fresh host AND host that already has `project-menu`/`project-launch`; remo-host logic branches (devcontainer present/absent, session active/absent/exited, docker available/not) each covered. **PASS.** |
| III. Idempotent by Default | remo-host install uses `template:` (like `project-launch`) → idempotent; re-running updates in place (FR-007/FR-056). Version marker unchanged mechanism. **PASS.** |
| IV. Fail Fast with Clear Messages | `remo web check` (FR-046) and readiness (FR-045) validate registry/SSH/runtime/executables/reachability/protocol up front with what/why/how errors; missing `web` extra → concise install hint not traceback (FR-041). **PASS.** |
| V. Documentation Reflects Reality | README/operator docs updated with architecture, security boundary, Compose, SSM, discovery states, limits, troubleshooting, upgrade (FR-057). quickstart.md is runnable. **PASS.** |
| Three-layer architecture (CLAUDE.md) | `cli/web.py` parses only; business logic in new `web/` service package + shared `core/` (protocol client, ControlPath refactor); no provider knowledge in core; provider/SSM specifics stay in existing `core/ssh.py`. Web deps lazily imported. **PASS.** |

No violations → Complexity Tracking left empty.

## Project Structure

### Documentation (this feature)

```text
specs/010-web-session-interface/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── remo-host-protocol.md      # remo-host JSON schema + exit codes + versioning
│   ├── rest-api.md                # /api/v1 HTTP contract
│   └── terminal-websocket.md      # WS framing/token/control-message contract
└── checklists/
    └── requirements.md  # Spec quality checklist (from /speckit-specify)
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   └── web.py                     # NEW: `remo web {serve,check}` — parsing/presentation only,
│                                  #      lazy-imports remo_cli.web.* with a clear "pip install remo-cli[web]" hint
├── core/
│   ├── ssh.py                     # REFACTOR: parameterize ControlPath (default ~/.ssh/remo-…;
│   │                              #   override to $REMO_SSH_CONTROL_DIR e.g. /run/remo-ssh); extract
│   │                              #   build_ssh_base_cmd() reused by CLI + web; no behavior change for CLI
│   ├── remo_host_client.py        # NEW (shared, no provider knowledge): build remo-host argv,
│   │                              #   parse/validate versioned JSON, [min,max] version negotiation,
│   │                              #   typed errors, payload-size limit
│   └── config.py                  # ADD: read-only-safe registry path accessor (no mkdir side effect)
├── models/
│   ├── host.py                    # unchanged (no registry schema change)
│   ├── capability.py              # NEW: RemoteCapability
│   ├── session_target.py          # NEW: SessionTarget + typed instance status
│   └── discovery.py               # NEW: DiscoverySnapshot
└── web/                           # NEW service package (only imported by cli/web.py + container entry)
    ├── __init__.py
    ├── app.py                     # FastAPI app factory, CSP/Host/Origin middleware, router mount
    ├── config.py                  # WebSettings (bind addr, timeouts, caps, token TTL, allowed hosts/origins)
    ├── discovery.py               # concurrent per-instance discovery via remo_host_client; cache w/ TTL
    ├── ssh_master.py              # ControlMaster lifecycle in $REMO_SSH_CONTROL_DIR; keyed by user/host/port
    ├── terminal.py                # PTY + ssh attach process, resize, backpressure, reap, error classes
    ├── terminal_registry.py       # terminal lifecycle, global/per-client caps, single-use token store
    ├── tokens.py                  # short-lived single-use WS token issue/consume (30s default)
    ├── health.py                  # liveness vs readiness (config-valid vs process-up)
    └── api/
        ├── hosts.py               # GET /hosts, GET /sessions, POST /discovery/refresh
        └── terminals.py           # POST/GET/DELETE /terminals, WS /terminals/{id}

frontend/                          # NEW: TypeScript SPA (built by Docker stage, served same-origin)
├── package.json                   # pins ghostty-web 0.4.0, xterm, vite, react
├── vite.config.ts
├── index.html
├── public/                        # ghostty WASM asset copied here, served locally (no CDN)
└── src/
    ├── main.tsx
    ├── api/client.ts              # typed REST + WS client (token via WS subprotocol, never URL)
    ├── state/                     # discovery + workspace stores (localStorage layout only)
    ├── terminal/
    │   ├── RendererAdapter.ts     # interface: create/open/write/onInput/fit/resize/focus/title/selection/dispose
    │   ├── GhosttyRenderer.ts     # default impl
    │   └── XtermRenderer.ts       # fallback impl (same interface)
    └── components/                # Dashboard, InstanceGroup, TargetCard, TerminalCard, GridView, TabView

docker/
├── Dockerfile                     # multi-stage: node build frontend → python runtime w/ openssh+aws cli+ssm plugin
├── compose.example.yml            # amd64/arm64 home-lab example: RO mounts, tmpfs, healthcheck, safe defaults
└── entrypoint.sh                  # non-root; runs `remo web check` gate then `remo web serve`

ansible/roles/user_setup/
├── templates/remo-host.sh.j2      # NEW host command (Bash), installed to ~/.local/bin/remo-host
└── tasks/main.yml                 # ADD idempotent install task (mirrors project-launch install)

tests/
├── unit/core/test_ssh_controlpath.py, test_remo_host_client.py
├── unit/web/test_tokens.py, test_terminal_resize.py, test_backpressure.py, test_discovery.py, test_health.py
├── unit/test_ansible_templates.py         # EXTEND: remo-host template assertions
├── integration/test_remo_host_e2e.py      # disposable SSH targets: healthy/unreachable/malformed/incompatible/slow
├── integration/test_nine_terminals.py     # 3×3 fixture, nine PTY/WS terminals, cross-routing + 1h resource test
├── e2e/ (Playwright)                       # grid/tab/focus, keyboard routing, reconnect, Origin, WASM load
└── ansible/test_remo_host_idempotency      # fresh + already-configured host branches
```

**Structure Decision**: Extend the existing single repo. Backend lives in a new lazily-imported `src/remo_cli/web/` package plus shared additions in `core/`/`models/`; the CLI entry is a thin `cli/web.py` group. The frontend is a separate `frontend/` TypeScript SPA compiled at image-build time and served same-origin by the FastAPI app (satisfies CSP/no-CDN). The host command is a new Ansible-installed Bash template in the existing `user_setup` role, so it ships and updates through the established host-configuration flow. This keeps the three-layer architecture intact and guarantees the ordinary CLI never imports web/Node dependencies.

## Complexity Tracking

> No Constitution Check violations — section intentionally empty.
