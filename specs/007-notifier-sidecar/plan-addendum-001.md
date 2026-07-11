# Implementation Plan — Addendum 001: Standing Grants ("Always")

**Branch**: `007-notifier-sidecar` | **Date**: 2026-06-01
**Spec**: [`addendum-001-standing-grants.md`](./addendum-001-standing-grants.md)
**Parent plan**: [`plan.md`](./plan.md) (shipped v1 notifier)

**Note**: This plans the *addendum* only. The base notifier (server, registry,
Telegram transport, config, CLI, role, tests) is implemented and merged on this
branch; this layers standing-grant auto-approval on top. It does not overwrite
the base artifacts.

## Summary

Add human-authored **standing grants** so a third Telegram choice — **Always** —
auto-approves a *class* of operation without a round-trip. A new
`src/remo_cli/notifier/grants.py` holds the `Grant`/predicate/scope models, an
in-memory `GrantStore` (TTL-bounded, capped, fail-closed), a **deterministic
per-operation matcher**, and a **candidate-generalization** proposer. `server.py`
gains an intake short-circuit (`/v1/approve` checks the store before
reserve/notify). The Telegram transport gains the `[✅ Approve] [⏩ Always…]
[❌ Deny]` flow with a scope/granularity picker, plus `/rules`, `/revoke`,
`/pause` command handlers and an auto-approval digest. Config grows a `[grants]`
block; the `remo_notifier` role's config template + defaults grow matching
variables. All grant state is in-memory (no persistence); everything stays
fail-secure — nothing is auto-`allow`ed except by an active, unexpired,
unrevoked, human-created grant whose deterministic predicate the request
satisfies.

## Technical Context

**Language/Version**: Python 3.11+ (service container 3.13) — unchanged.
**Primary Dependencies**: unchanged (`[notifier]` extra: FastAPI, uvicorn,
pydantic, python-telegram-bot, structlog). **No new runtime dependencies.**
**Storage**: None — grants are in-memory in a `GrantStore`, never persisted
(extends parent FR-009; restart re-prompts, fail-closed).
**Testing**: pytest + pytest-asyncio + httpx (existing). New `tests/notifier/
test_grants.py`; extensions to `test_server.py` and `test_telegram.py`.
**Target Platform**: unchanged (Linux container; operator CLI cross-platform).
**Project Type**: single project — additive within `src/remo_cli/notifier/`.
**Performance Goals**: auto-approve match returns `allow` in < 500 ms (SC-G1) —
trivially met by an in-memory dict/predicate match.
**Constraints**: deterministic exact matcher (allow-capable — highest
criticality, FR-G4); narrow default scope (FR-G7); single global TTL default 8h,
no indefinite grants (FR-G8); max-grants cap (FR-G9); fail-closed on miss /
ambiguity / restart; no secrets in grant logs (FR-017); listing/revoke
Telegram-only, no bridge read endpoint (OQ4).
**Scale/Scope**: one authorized chat; ≤ `max_grants` (default 100) active grants
per instance; ~250–400 LOC Python + ~15 LOC Ansible (config block) + tests/docs.

## Constitution Check

*GATE: must pass before Phase 0. Re-check after Phase 1.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Defensive Variable Access (Ansible) | Only the role's `notifier.toml.j2` + `defaults/main.yml` change (a `[grants]` block + vars); no registered-variable access added | PASS — no new `.rc`/`.stdout` usage |
| II. Test All Conditional Paths | Matcher branches (match / no-match / expired / revoked / paused / at-capacity / scope-mismatch) and the short-circuit vs miss paths are each tested both ways | PASS — enumerated in tasks/tests |
| III. Idempotent by Default | The role change is a templated config block (handler-restart on change); re-runs are no-ops. Runtime grant state is in-memory, not config | PASS |
| IV. Fail Fast with Clear Messages | `GrantsConfig` is strict (`extra="forbid"`); invalid grant config fails startup with a clear message, like the rest of the config | PASS |
| V. Documentation Reflects Reality | wire-protocol.md (short-circuit + `grant_id`), config-schema.md (`[grants]`), notifier README, top-level README updated alongside code | PASS |

**Fail-secure invariant evolution (explicit):** parent FR-008 ("`allow` only from
an explicit human tap") becomes "`allow` only from an explicit human tap **or** a
matching active human-created standing grant." Still human-authored; every
non-matching path still prompts or fails closed. This is a deliberate,
documented relaxation, not a violation. No other constitution gate is affected.

**Complexity Tracking**: empty (no deviations to justify).

Post-Phase-1 re-check: design keeps grants additive under `notifier/`, the
matcher deterministic and transport-agnostic (enforcement in `server.py`), grant
UI confined to the Telegram transport, and the store in-memory. **Still PASS.**

## Project Structure (delta)

```text
src/remo_cli/notifier/
├── grants.py            # NEW — Grant/predicate/scope models, GrantStore,
│                        #       deterministic matcher, candidate proposer
├── server.py            # MODIFIED — intake short-circuit; own GrantStore;
│                        #            inject into transport; TTL sweep + digest
│                        #            in lifespan
├── config.py            # MODIFIED — GrantsConfig (enabled/ttl/max/global)
├── models.py            # MODIFIED — ApprovalResponse gains optional grant_id
└── transports/
    ├── base.py          # MODIFIED (minimal) — transport may accept a grant
    │                    #   service; grant UI is implementation-specific
    └── telegram.py      # MODIFIED — Always button + picker callback flow,
                         #            grant creation, /rules /revoke /pause,
                         #            digest sender

ansible/roles/remo_notifier/
├── defaults/main.yml            # MODIFIED — grant vars (enabled/ttl/max/global)
└── templates/notifier.toml.j2   # MODIFIED — [grants] block

tests/notifier/
├── test_grants.py       # NEW — store, matcher, candidates, TTL, cap, revoke,
│                        #       pause, scope, fail-closed, concurrency
├── test_server.py       # MODIFIED — short-circuit allow; miss→existing flow
└── test_telegram.py     # MODIFIED — Always flow, picker callback_data,
                         #            grant creation, /rules /revoke /pause

src/remo_cli/notifier/docs/   # MODIFIED — wire-protocol.md, config-schema.md
README.md                     # MODIFIED — notifier "always" note
specs/007-notifier-sidecar/contracts/
├── openapi.yaml          # MODIFIED — ApprovalResponse.grant_id; 200 may be auto
└── grant-schema.md       # NEW — Grant/predicate grammar, matching, callback_data
```

**Structure Decision**: Additive within the existing notifier package. Critical
separation: **enforcement (matching) lives in `server.py`/`grants.py`
(transport-agnostic); the grant UI (button, picker, slash-commands) lives in the
Telegram transport.** The `GrantStore` is owned by the app (constructed in
`create_app`) and injected into the transport so the transport can propose/create
grants and serve `/rules`/`/revoke`/`/pause`, while the server alone performs the
allow-capable match. No CLI changes (OQ4); no new top-level integration points.

## Complexity Tracking

> No constitution violations — section intentionally empty.
