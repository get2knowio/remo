# Implementation Plan: Notifier Source Registration — dynamic multi-agentsh polling

**Branch**: `009-notifier-source-registration` | **Date**: 2026-06-08 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/009-notifier-source-registration/spec.md`

## Summary

Turn the spec-008 notifier's **single static** agentsh poller (`[agentsh] api_url`, one `_poll_loop`, one `AgentshClient`) into a **dynamic registry of sources**, where each source is one agentsh approval endpoint (1:1 with a devcontainer) and the notifier runs one independent poll/resolve loop per source. Registration is expressed as a **persistent connection** the source holds open to the notifier — a held-open HTTP/1.1 streaming request (`POST /v1/sources`) carrying `{source_id, api_url, api_key, labels}`, with application-level keepalive ticks. **The open connection is the registration**; its drop (graceful close, reset, or keepalive/idle timeout) is the only de-registration signal — no heartbeat, lease, or reconcile machinery. The static `[agentsh]` endpoint is retained as an optional always-present **seed source**. An opt-in **devcontainer Feature** opens and reconnects the connection. Everything inherits 007/008's fail-secure invariant and open bridge-only trust model.

Technical approach (low-churn, leaning on existing seams):

1. **Per-source loop, not a global loop.** The current module-level `_poll_loop` / `_handle` / `inflight` / `agentsh` inside `create_app` are refactored into a per-source **`SourcePoller`** (its own `AgentshClient`, its own in-flight set, its own backoff state). `AgentshClient` is already per-instance (`api_url`, `api_key`) — one is created per source with no client change.
2. **`SourceRegistry`** (new, in-memory, bounded, lock-guarded — mirrors `PendingApprovals`'s concurrency discipline): add/reconcile/remove sources, supervise one `asyncio.Task` poll loop per source, enforce `max_sources`, drain a removed source's in-flight approvals. Never persisted (FR-001/FR-013, 007 FR-009).
3. **Presence control plane.** New `POST /v1/sources` reads the registration body, validates capacity + reconciles a duplicate `source_id` (latest-connection-wins via a per-source epoch), registers the source + starts its poller, then returns a `StreamingResponse` that emits keepalive ticks. The generator's disconnect (detected via the stream write failing or `request.is_disconnected()` past the idle timeout) triggers de-registration. Capacity rejection returns an explicit `503` typed reason **before** the stream is held open (FR-004).
4. **Approval identity & routing.** Decisions never cross-route by construction: each `SourcePoller` closes over its own source, so the resolve goes to that source's agentsh. To keep the shared `PendingApprovals` registry and the channel's callback space collision-free across sources (and away from Telegram's `callback_data` `:`-delimited parsing), the core assigns each delivered approval a colon-free **delivery id** and maps it back to `(source, agentsh approval id)` for resolve (research R3).
5. **Poll-health backoff** (FR-014): per-source exponential backoff (base, factor, cap, jitter) on agentsh poll/resolve failure while the connection stays up; a poll failure throttles, never de-registers. Successful poll resets the backoff.
6. **In-flight drain on removal** (FR-009): removing a source locally abandons its pending approvals to a fail-secure deny (cancel the channel prompt; never an allow) and best-effort `POST`s a deny to that source's agentsh only if still reachable.
7. **Seed source** (FR-005): the retained optional `[agentsh]` config registers one source at startup with no presence connection (epoch pinned; never removed by connection drop).
8. **Status surface** (FR-020): `GET /v1/sources` lists connected sources (id, labels, poll state, last-success); `/v1/health` reports the source count; CLI gains `remo notifier sources <host>`.
9. **Devcontainer Feature** (FR-016/FR-017): a new opt-in `features/remo-notifier-source/` (devcontainer-feature.json + install.sh + a `remo-source-connect` reconnect loop using `curl --no-buffer`, with jitter/backoff). agentsh is not modified.

The channel model (008) is untouched: channels stay unaware of sources; the core fans many sources into the one installed channel and routes each decision back to its origin (FR-019).

## Technical Context

**Language/Version**: Python 3.11+ (`requires-python = ">=3.11"`); service container runs Python 3.13-slim (unchanged from 007/008). Devcontainer Feature: POSIX `sh` + `curl` (no new runtime language).
**Primary Dependencies**: **No new dependencies.** Service core unchanged from 008 — FastAPI ≥0.115 (`StreamingResponse`, `Request.is_disconnected`), uvicorn[standard] ≥0.32, httpx ≥0.27 (per-source agentsh client), pydantic ≥2.9, structlog ≥24.4, tomli on py<3.11. CLI/laptop side: Click ≥8.1, InquirerPy (existing) — **no new laptop runtime deps**. Build: hatchling + uv (in-container). Ansible: `community.docker` (already present).
**Storage**: None. The source registry, per-source poll-health, pending approvals, and grants are all in-memory and lost on restart by design (FR-001/FR-013; 007 FR-009). Recovery is by source reconnection.
**Testing**: pytest + pytest-asyncio + httpx `ASGITransport` (existing). New: streaming presence-endpoint tests (connect → polled; disconnect → removed; capacity 503; duplicate `source_id` reconcile), per-source registry/poller unit tests with a fake multi-endpoint agentsh, backoff-policy tests, in-flight-drain tests, seed-source tests, and a Feature smoke test (`shellcheck` + an install/connect dry-run).
**Target Platform**: Service: Linux/amd64 OCI container on Ubuntu 24.04 hosts (bridge-bound). Feature: Debian/Ubuntu-family devcontainer base images. Operator CLI: cross-platform (existing remo install).
**Project Type**: Single project — Python package (`src/remo_cli/`) + Ansible (`ansible/`) + a new top-level `features/` dir for the devcontainer Feature, matching the existing repo layout.
**Performance Goals**: A newly connected source is polled within one poll interval (SC-001). A dropped connection removes the source within the keepalive/idle-timeout window in the ungraceful case (SC-002), instantly on FIN/RST. Fail-secure outcomes within agentsh's own `ExpiresAt` regardless of churn (SC-007).
**Constraints**: In-memory only; fail-secure (a source that is unconnected, dropped, backed-off, or rejected simply delivers no approvals — never an allow); open bridge-only control plane with no caller auth (007 trust model, accepted residual risk); one channel per host fans all sources; the approver `api_key` is sent **inline** in the registration payload (clarified); `max_sources` is a hard cap, never silently exceeded; no agentsh modification; no new laptop runtime deps.
**Scale/Scope**: One notifier per host serving up to `max_sources` (default 64, configurable) concurrent devcontainer sources. ~400–600 LOC net Python (registry + poller + presence endpoints + status + config/model deltas), ~40 LOC Ansible/Jinja deltas, ~120 LOC Feature (shell + JSON). Per-source poll cost is one `GET` per interval (default 5 s) with exponential backoff when failing.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Defensive Variable Access (Ansible) | `remo_notifier` role gains `[sources]` config keys (`max_sources`, keepalive interval/timeout, backoff base/factor/cap/jitter) rendered into `notifier.toml.j2`; every registered-var access keeps `\| default()`; pre-commit greps (`grep -r '\.rc ==' ansible/`, `grep -r '\.stdout' ansible/`) stay clean | PASS — enforced via a task in the task list |
| II. Test All Conditional Paths | Each lifecycle branch tested: connect→polled; graceful close→removed; ungraceful drop→removed-by-idle-timeout; duplicate `source_id`→reconcile (latest wins); capacity→503 typed reject + Feature backoff; poll failure→backoff (not de-register); recovery→reset; seed source (always present); notifier restart→empty→reconnect; Feature present vs absent | PASS — covered by new registry/poller/server/Feature tests |
| III. Idempotent by Default | Role re-run yields identical state (config render + single service, unchanged bind/port); Feature `install.sh` is re-runnable; reconnect loop converges to one connection per `source_id` (reconcile); registry add is idempotent for a live `source_id` | PASS — verified by a rerun check task |
| IV. Fail Fast with Clear Messages | Strict config still rejects unknown keys; new `[sources]` bounds validated (timeout > keepalive interval, cap ≥ base, jitter 0–1); capacity rejection returns an explicit typed `503` reason; Feature preflight names missing options (notifier address, agentsh `api_url`, `api_key`) and exits non-zero | PASS |
| V. Documentation Reflects Reality | README "Notifier" section gains multi-source + Feature setup + shared-network prerequisite; new `contracts/source-registration.md` + `contracts/devcontainer-feature.md`; Feature ships a runnable README/quickstart; status surface documented | PASS |

No violations. **Complexity Tracking** is empty.

**Post-Phase-1 re-check**: The design keeps the channel model and agentsh wire contract (008) untouched (channels stay source-unaware; `AgentshClient` is reused per source); the single new mechanism — a presence connection whose existence is the registration — replaces heartbeat/lease/reconcile rather than adding to it; all state stays in-memory; the control plane stays open bridge-only (accepted residual risk documented). New surface (`SourceRegistry`, `SourcePoller`, `POST/GET /v1/sources`, `[sources]` config, the Feature) is additive and confined to this feature. **Still PASS.**

## Project Structure

### Documentation (this feature)

```text
specs/009-notifier-source-registration/
├── plan.md              # This file
├── spec.md              # Feature spec (clarified 2026-06-08)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── source-registration.md    # The presence-connection control-plane protocol (POST/GET /v1/sources)
│   └── devcontainer-feature.md   # The opt-in Feature's options + reconnect behavior contract
└── tasks.md             # /speckit.tasks output (NOT created here)
```

### Source Code (repository root)

```text
src/remo_cli/notifier/
├── __init__.py                 # __version__ (unchanged)
├── cli.py                      # MODIFIED — drop the single-AgentshClient build/read_api_key; the registry (built in create_app) owns per-source clients and the [agentsh] seed
├── server.py                   # MODIFIED — POST/GET /v1/sources; supervisor wiring; refactor _handle/_deliver to be source-scoped; /v1/health reports source count
├── agentsh_client.py           # CORE — unchanged (already per-instance api_url/api_key)
├── state.py                    # MODIFIED — PendingApprovals gains source-scoped drain (drain_source) + delivery-id mapping helpers
├── models.py                   # MODIFIED — SourceRegistration (wire payload), SourceStatus, extend HealthResponse with source count
├── config.py                   # MODIFIED — new [sources] section (max_sources, keepalive_interval, idle_timeout, backoff base/factor/cap/jitter); [agentsh] becomes OPTIONAL (seed source) instead of required
├── grants.py                   # CORE — unchanged
├── logging_setup.py            # CORE — unchanged
├── transports/
│   └── base.py                 # CORE — unchanged (channels stay source-unaware)
├── channels/                   # unchanged (catalog + telegram)
└── sources/                    # NEW — the dynamic source registry
    ├── __init__.py
    ├── source.py               # Source dataclass (id, api_url, api_key, labels, epoch, AgentshClient, poll-health, task)
    ├── registry.py             # SourceRegistry — bounded, lock-guarded add/reconcile/remove/drain/snapshot
    └── poller.py               # SourcePoller — per-source poll/resolve loop + exponential backoff; source-scoped _handle/_deliver

features/                       # NEW — opt-in devcontainer Feature (in-repo; publication later, Out of Scope)
└── remo-notifier-source/
    ├── devcontainer-feature.json   # id, options (notifier_address, agentsh_api_url, api_key/api_key_ref, labels, source_id)
    ├── install.sh                  # installs the connector + a startup hook (idempotent)
    ├── scripts/
    │   └── remo-source-connect.sh  # held-open POST /v1/sources via curl --no-buffer; reconnect with jitter/backoff
    └── README.md                   # Feature usage + shared-network prerequisite

ansible/
├── roles/remo_notifier/
│   ├── defaults/main.yml       # MODIFIED — remo_notifier_max_sources, keepalive/idle, backoff base/factor/cap/jitter
│   ├── templates/notifier.toml.j2  # MODIFIED — add [sources] section; [agentsh] kept (seed)
│   └── (tasks/main.yml, service.j2, handlers) — unchanged (same bind/port/service)
└── notifier_deploy.yml         # unchanged

src/remo_cli/cli/notifier.py    # MODIFIED — add `remo notifier sources <host>` (curls GET /v1/sources)

tests/notifier/
├── core/
│   ├── test_server.py          # MODIFIED — presence endpoints, status, source-scoped delivery
│   └── test_state.py           # MODIFIED — drain_source / delivery-id mapping
├── sources/                    # NEW
│   ├── test_registry.py        # capacity, reconcile, remove, drain, snapshot, seed
│   ├── test_poller.py          # backoff policy, recovery, fail-secure
│   └── test_presence.py        # streaming connect/disconnect, idle-timeout removal, 503 capacity
├── test_feature.py             # NEW — shellcheck + install/connect dry-run of the Feature
├── test_config.py              # MODIFIED — [sources] validation; [agentsh] now optional
└── test_cli_notifier.py        # MODIFIED — `sources` subcommand

pyproject.toml                  # unchanged (no new deps)
README.md                       # MODIFIED — multi-source + Feature + shared-network prerequisite
```

**Structure Decision**: Single-project layout, unchanged. The notifier core stays in place; the dynamic registry lands in a new `sources/` subpackage (analogous to how 008 added `channels/`), so the per-source loop, registry, and backoff are self-contained and the channel/agentsh contracts are untouched. The devcontainer Feature lives in a new top-level `features/` directory (standard Features layout), keeping it opt-in and shippable in-repo before any registry publication. The Ansible role and CLI gain only additive deltas (a `[sources]` config block and a `sources` status subcommand); the bind, port, and single service are unchanged.

## Complexity Tracking

> No constitution violations — section intentionally empty.
