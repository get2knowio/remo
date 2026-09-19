# Implementation Plan: Discovery Resilience — Tolerate Intermittent Instance Connectivity

**Branch**: `024-discovery-resilience` | **Date**: 2026-09-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/024-discovery-resilience/spec.md`

## Summary

One discovery blink currently destroys the console's working state: a
`timeout` snapshot with zero targets unconditionally replaces an `ok` snapshot
(`web/discovery.py:476-480`), the OK-only target index (`:510-520`) then drops
the host's targets from `GET /sessions` and from terminal open/reattach
authorization (`web/api/terminals.py:261`, `:381`), the workspace filters the
now-unresolvable panes out and unmounts their live terminals
(`WorkspacePane.tsx:140-151`), and the rail collapses the host into a red
error block (`railModel.ts:167`). Fix in four coordinated parts: (1) the
service retains the last-known-good snapshot through a monotonic-time grace
budget on retryable failures, serving `status=ok` with additive advisory
fields (`stale`, `last_ok_at`, `consecutive_failures`); (2) the per-instance
outer timeout gets a total budget of 2× the per-call timeout plus slack so a
slow capabilities call can no longer starve the sessions call
(`discovery.py:139-140` vs `:255-258`); (3) the rail renders staleness as a
dimmed badge, never the error block; (4) the workspace resolves panes against
a sticky last-known-target map so discovery liveness can never unmount a
terminal — the card's own `connectionState` is the liveness truth. The
`InstanceOut` change rides the generated-contract pipeline (Principle IV).

All code anchors verified against main@5780f3b by a read-only sweep this run;
see [research.md](research.md) for the full verification and decisions D1-D10.

## Technical Context

**Language/Version**: Python 3.11+ (backend), TypeScript 5 / React 18 (frontend)

**Primary Dependencies**: FastAPI/pydantic (existing `web` extra), stdlib
`asyncio`/`time.monotonic`; Vite/React/vitest (existing). **No new runtime deps.**

**Storage**: none — retention state is in-memory on `DiscoveryService`
(process lifetime); sticky pane targets are in-memory in the SPA. No registry
schema change.

**Testing**: pytest + pytest-asyncio (strict mode — explicit
`@pytest.mark.asyncio`) for the service; vitest/jsdom + Testing Library for
the console; two schema-drift gates (pytest + Node) for the contract.

**Target Platform**: `remo web serve` (local or Docker) + browser SPA

**Project Type**: web service + SPA (existing layers; no new modules — every
change lands in an existing file)

**Performance Goals**: no new per-request work on read paths (retention merge
happens on the write path, under the existing store lock); reads stay
lock-free (fresh-dict + atomic swap preserved)

**Constraints**: `status` enum unchanged (no new member — would break the
exhaustive `STATUS_META` Record and the wire contract); advisory fields
additive with defaults; grace measured on the monotonic clock; budget 0
disables retention; non-retryable statuses bypass retention entirely

**Scale/Scope**: 2 backend modules + 1 model + 1 config + 2 API-shape touches;
3 frontend components + generated types; ~6 test files; docs update

## Constitution Check

Source: `.specify/memory/constitution.md` (v2.1.0).

| # | Principle | Check for this feature | Status |
|---|-----------|------------------------|--------|
| I | Layered Architecture | All backend changes in `web/` + `models/` + `web/config.py`; no `core/` or `providers/` involvement; no Click anywhere | PASS |
| II | Providers Are Declared | Feature is provider-agnostic (discovery treats every instance uniformly); no `host.type` literals introduced | PASS |
| III | Typed Errors, One Exit Boundary | No new error paths at the CLI boundary; service keeps mapping probe failures to `TypedError` payloads; config misuse raises `WebConfigError` at startup (existing pattern) | PASS |
| IV | Generated Contracts | `InstanceOut` gains three fields → regenerate `openapi.json`/`schema.d.ts` (+ no-op `terminal-frames.*`); both drift gates must pass; frontend consumes only generated types (`client.ts` re-exports) | PASS |
| V | Defensive Variable Access | No Ansible changes | N/A |
| VI | Test Skip/Fail Paths | Explicit tests for: grace-window retention, grace exhaustion, zero budget, non-retryable immediate surface, first-failure-no-prior, recovery reset, prune/evict cleanup, sticky-map drop-on-close, stale-vs-error rail branches | PASS |
| VII | Idempotent & Re-runnable | Discovery cycles remain idempotent; repeated retryable failures converge on the same retained snapshot until grace expires; no registry writes | PASS |
| VIII | Docs Reflect Reality | `docs/web-session-interface.md` gains the new env var; CLAUDE.md structure-diagram comments for `discovery.py`/`railModel`-adjacent lines updated in the same change (no new modules, so the diagram's file set is unchanged) | PASS |
| IX | Pre-Release Off-Index | No packaging-surface change; no release in this flow | N/A |

No violations; Complexity Tracking not needed.

## Project Structure

### Documentation (this feature)

```text
specs/024-discovery-resilience/
├── plan.md              # This file
├── spec.md              # Feature specification (with Clarifications session)
├── research.md          # Phase 0 — verified anchors + decisions D1-D10
├── data-model.md        # Phase 1 — extended snapshot/InstanceOut, retention state, sticky map
├── quickstart.md        # Phase 1 — validation guide
├── contracts/
│   └── instance-out-stale.md  # Phase 1 — additive InstanceOut fields + regeneration procedure
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks — not created by /speckit-plan)
```

### Source Code (repository root) — all EXISTING files, no new modules

```text
src/remo_cli/
├── models/discovery.py           # DiscoverySnapshot + stale/last_ok_at/consecutive_failures
└── web/
    ├── config.py                 # + discovery_offline_grace_s (REMO_WEB_DISCOVERY_OFFLINE_GRACE_S)
    ├── discovery.py              # retention merge at the store site; _last_ok_monotonic;
    │                             #   _RETRYABLE_STATUSES; outer wait_for budget 2x+slack
    └── api/hosts.py              # InstanceOut + 3 fields; _instance_out mapping

frontend/src/
├── api/generated/                # openapi.json + schema.d.ts REGENERATED (never hand-edited)
├── components/
│   ├── railModel.ts              # RailGroup.isStale (beside isError, never replacing it)
│   ├── SessionRail.tsx           # dimmed stale group + "not responding · retrying" chip
│   └── WorkspacePane.tsx         # sticky last-known-target map (useRef, in-memory)
└── (styles file used by SessionRail/TerminalCard for the dimmed presentations)

tests/unit/web/
├── test_discovery.py             # retention/grace/budget/cleanup coverage
└── test_hosts_mapping.py         # _instance_out maps the new fields

frontend/src/components/
├── railModel.test.ts             # stale vs error vs ok grouping
├── SessionRail.test.tsx          # chip + dimming render
└── WorkspacePane.test.tsx        # sticky resolution; drop-on-close

docs/web-session-interface.md     # REMO_WEB_DISCOVERY_OFFLINE_GRACE_S documented
```

**Structure Decision**: web-service + SPA layout already exists; this feature
adds zero files outside `specs/` and tests — every production change is an
edit to an existing module, keeping the CLAUDE.md structure diagram's file set
stable (Principle VIII gate `test_docs_structure.py` unaffected by new paths).

## Design (Phase 1 summary — details in research.md/data-model.md)

### Part 1 — Backend retention (D1-D4)

- `_run_and_store` (`discovery.py:476-480`) becomes: probe → under
  `self._lock`, pass the fresh snapshot through a pure, unit-testable merge
  step against the stored prior + retention bookkeeping, store the result,
  rebuild index. Merge rules (data-model.md "State transitions"):
  - fresh success → store as-is + `last_ok_at`/counter reset; record
    `_last_ok_monotonic[instance_id]`.
  - retryable failure (`TIMEOUT`/`UNREACHABLE`) with a prior `OK` snapshot,
    budget > 0, and `monotonic() - last_ok <= budget` → retained-ok snapshot
    (NEW object: prior's capability/targets/region + `stale=True`,
    counter+1, fresh `error`/`refreshed_at`).
  - retryable failure otherwise (no prior OK / budget 0 / grace exhausted) →
    store the failure, carrying `last_ok_at` + counter, `stale=False`.
  - non-retryable → store immediately, `stale=False`, counter 0,
    `last_ok_at` kept.
- Because retained snapshots keep `status=OK`, `_rebuild_target_index`
  (`:510-520`) and the terminals authorization seam
  (`terminals.py:261`/`:381`) need **no changes** — that is the point of D1.
- Cleanup: `evict` and the deregistered-host prune also drop
  `_last_ok_monotonic` entries. Readers stay lock-free.

### Part 2 — Timeout budget (D5)

`asyncio.wait_for(..., timeout=2 * settings.discovery_timeout_s + 5.0)` at
`discovery.py:255-258` via a small helper; the timeout `TypedError` message
(`:266`) updated to name the total budget. Per-call timeouts unchanged.

### Part 3 — Rail staleness (D6)

`RailGroup.isStale` computed in `buildRailModel`; `SessionRail.tsx` renders
the chip + a dimmed modifier when `isStale` (rows unchanged — they exist
because the backend kept the targets). `isError` path byte-identical.

### Part 4 — Sticky panes (D7-D8)

`WorkspacePane` resolves attached ids via `targetsById` with fallback to a
`useRef` sticky map (refresh on live resolve, delete on close, never
persisted). `TerminalCard` behavior unchanged — presentation already keys off
`connectionState`; add only CSS so disconnected/reconnecting/error states read
dimmed on the mounted card.

### Contract & docs (D9)

Regenerate all four artifacts; update `docs/web-session-interface.md`
(new env var) and README/CLAUDE.md touchpoints per Principle VIII.

## Post-Design Constitution Re-check

Re-evaluated after Phase 1: unchanged — all PASS/N/A as tabled above. The
design deliberately avoids the two tempting violations: a new `InstanceStatus`
member (wire-breaking, IV) and a parallel target store (would fork the single
source of truth the terminals seam authorizes against).

## Complexity Tracking

No constitution violations — table intentionally empty.
