# Implementation Plan: Ephemeral Device-Pairing Adoption (Forward-Auth Gated)

**Branch**: `012-web-adopt-pairing` | **Date**: 2026-07-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/012-web-adopt-pairing/spec.md`

## Summary

Replace 011's static `REMO_WEB_API_TOKEN` gate on `/api/v1/setup/*` with an
**ephemeral device-pairing** model. The awaiting-adoption page (and the
dashboard's re-sync affordance) mints a short-lived, in-memory **pairing code**;
the operator copies it to the clipboard and pastes it into `remo web adopt` /
`remo web push`, where it authorizes exactly one adoption/push handoff. The
setup surface is **dormant** (`404`, byte-identical to an unknown route)
whenever no pairing session is live, and a pairing session exists only while an
operator is actively on the page. Minting is gated by **operator
authentication**: v1 reads a trusted, proxy-injected identity header (**forward
auth**) behind a pluggable provider seam that a future in-app OIDC verifier can
slot into without touching the pairing core. Nothing durable is persisted — no
static service credential anywhere — so every first adoption and every later
re-sync obtains a fresh code from the page. The registry mirror, host-key
verification, key authorization, and verification pass are all inherited
unchanged from 011; this feature changes only how the setup surface is
authorized and when it exists.

## Technical Context

**Language/Version**: Python 3.11+ (backend + CLI), TypeScript 5 / React 18 (frontend)

**Primary Dependencies**: FastAPI/Uvicorn + pydantic v2 (`web` extra, service
side only), stdlib `secrets` (code generation) and `time.monotonic` (TTL) —
**no new runtime dependency**; stdlib `urllib.request` for the CLI's HTTP calls
(unchanged from 011); Vite + ghostty-web (frontend, unchanged)

**Storage**: **None for pairing state** — pairing sessions/codes live only in
process memory and are dropped on restart (FR-008). The service identity
keypair and service-managed `known_hosts` under `web-identity/` persist exactly
as in 011. Workstation side: the saved *credentials* file
(`~/.config/remo/web-service.json`) loses its URL/token fields (FR-019); an
optional non-secret push cache (deployment id + per-instance fingerprints) may
remain for the push skip-optimization only.

**Testing**: pytest (unit: pairing-session lifecycle with a fake monotonic
clock, dormancy `404` semantics via starlette TestClient, forward-auth
gating/fail-fast, mint rotation + idle expiry + end-on-adoption; adopt/push
orchestration with mocked HTTP; integration: adopt against a live `remo web
serve` behind a stub forward-auth header), frontend `npm run lint`, mypy + ruff

**Target Platform**: Linux container (amd64/arm64) for the service;
Linux/macOS workstation for the CLI

**Project Type**: Existing three-layer CLI (`cli/` → `providers|web/` →
`core/`) + FastAPI service + React SPA — this feature edits the service auth
layer, the SPA adopt/dashboard pages, the CLI adopt/push commands, and docs

**Performance Goals**: Mint is O(1) in-memory (< 5 ms); dormancy check adds a
single lock-guarded lookup to each setup call; adoption end-to-end timing is
unchanged from 011 (the sliding TTL default of 15 min covers the interactive
fingerprint pause, SC-003)

**Constraints**: Dormant surface indistinguishable from absent (same `404`
body, never a `401`, FR-006); code never logged, never in the DOM, never in the
served bundle (FR-016/SC-006); TTL measured against a monotonic source so a
wall-clock change cannot extend/expire a session (Edge Cases); forward-auth
header trusted only under the documented proxy trust boundary (FR-014);
network-restricted posture never entered silently (FR-013); `remo` CLI without
the `web` extra must still import (NFR-008 lazy-import discipline preserved)

**Scale/Scope**: Home-lab scale — one operator drives one adoption/push at a
time; at most one live pairing session (most-recent-wins rotation, FR-003)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|-----------|------------|
| I. Defensive Variable Access (Ansible) | PASS (N/A) — no Ansible playbook or role changes in this feature. |
| II. Test All Conditional Paths | PASS — dormant/live/expired/rotated setup states, mint refused/allowed under forward-auth vs network-restricted, and fail-fast-on-missing-header are all enumerated in spec acceptance scenarios; plan requires a unit test per branch (fake monotonic clock for the TTL branches). |
| III. Idempotent by Default | PASS — mint is rotation-on-open (most-recent-wins, no accumulation); the underlying registry apply / key authorization remain the idempotent 011 operations; ending a session is safe to call repeatedly (beacon + completion + expiry all converge to dormant). |
| IV. Fail Fast with Clear Messages | PASS — forward auth enabled without a header name is a startup fail-fast (FR-009); minting with no operator-auth provider configured is refused with an actionable diagnostic; the CLI maps the dormant `404` to "reopen the page for a fresh code" (FR-020); network-restricted posture is loudly surfaced (FR-013). |
| V. Documentation Reflects Reality | PASS — FR-021/FR-022 make the breaking-change note, compose example, and hola forward-auth trust-boundary docs part of the feature. |

**Post-Phase-1 re-check**: PASS — design artifacts introduce no new violations;
no Complexity Tracking entries required.

## Project Structure

### Documentation (this feature)

```text
specs/012-web-adopt-pairing/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── pairing-api.md   # NEW: forward-auth-gated mint/end endpoints
│   ├── setup-api.md     # DELTA: dormancy now driven by live pairing session
│   └── cli-web-adopt.md # DELTA: code (not token); no saved credentials
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   └── web.py                    # adopt/push: "token" prompt → "pairing code";
│                                 #   drop --save + saved-credential fallback (FR-018/019)
├── core/
│   └── web_adopt.py              # remove SavedCredentials/save/load credential
│                                 #   fields (keep non-secret push cache only);
│                                 #   409/404 mapping → "reopen page" guidance (FR-020)
└── web/
    ├── pairing.py                # NEW: PairingSessionManager (in-memory, one live
    │                             #   session, monotonic sliding TTL, rotate/touch/end)
    ├── operator_auth.py          # NEW: provider seam — OperatorAuthProvider protocol,
    │                             #   ForwardAuthProvider(header), NetworkRestricted;
    │                             #   fail-fast construction from config
    ├── config.py                 # remove api_token; add pairing_ttl_s,
    │                             #   operator_auth mode + forward_auth_header
    ├── app.py                    # mount pairing router; readiness/CSP unchanged;
    │                             #   construct auth provider (fail-fast) at startup
    ├── health.py                 # readiness reports operator-auth posture (FR-013)
    ├── check.py                  # `remo web check` reports posture; no token check
    ├── logging_config.py         # code redaction pattern (defense-in-depth, FR-016)
    └── api/
        ├── pairing.py            # NEW: POST /api/v1/pairing/mint (forward-auth
        │                         #   gated), POST /api/v1/pairing/end (beacon)
        └── setup.py              # require_setup_token → require_pairing_code:
                                  #   dormant 404 unless live session; touch on success

frontend/src/
├── api/client.ts                 # + mintPairingCode()/endPairing(); no token concept
├── components/
│   ├── AwaitingAdoption.tsx      # mint-on-open + "Copy pairing code" (value never
│   │                             #   rendered); pagehide beacon (FR-003/004/015/016)
│   └── (dashboard re-sync)       # "Pair CLI to sync" affordance (FR-017)
└── state/                        # pairing lifecycle wiring for the two pages

docker/
└── compose.example.yml           # drop REMO_WEB_API_TOKEN; forward-auth front door,
                                  #   mint-gated / setup-passthrough split (FR-022)

tests/
├── unit/web/                     # pairing lifecycle (fake clock), dormancy 404,
│                                 #   forward-auth gate + fail-fast, readiness posture
├── unit/core/                    # web_adopt: credential removal, 404→reopen mapping
└── integration/                  # adopt/push E2E behind a stub forward-auth header

docs/
└── web-session-interface.md      # breaking-change note (token removed), pairing flow,
                                  #   forward-auth trust boundary + hola config (FR-014/021/022)
```

**Structure Decision**: Extend the existing three-layer layout in place. The
pairing lifecycle and the operator-auth provider seam get their own service
modules (`web/pairing.py`, `web/operator_auth.py`) so the setup router shrinks
to a thin dormancy gate and the mint path is a small new router
(`web/api/pairing.py`). The workstation side stays in `core/web_adopt.py` (no
Click, stdlib HTTP only) so `cli/web.py` remains a thin Click layer and the
no-web-extra import path is unaffected (NFR-008).

## Complexity Tracking

No constitution violations — table not required.
