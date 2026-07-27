# Implementation Plan: Schema-Derived Frontend Types

**Branch**: `020-openapi-type-generation` | **Date**: 2026-07-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/020-openapi-type-generation/spec.md`

## Summary

Make the FastAPI service the machine-checked source of truth for every shape the browser console
consumes, and make drift a build failure with an actionable message.

Three moves, in dependency order:

1. **Make the published contract describe reality.** Four console-facing endpoints publish `{}` or an
   untyped object today, no route declares the error envelope, and the status/provider fields are bare
   strings even though closed enums already exist in `models/`. Declaration-only changes; not one
   serialized byte moves.
2. **Generate and consume.** A stdlib export script produces `openapi.json`; `openapi-typescript`
   turns it into `schema.d.ts`; `api/client.ts` and `providerMeta.ts` import from it. `ApiError` and
   the forward-auth re-auth path are untouched.
3. **Gate it.** Three drift checks modeled directly on `tests/unit/test_docs_structure.py`, split
   across the two CI jobs because neither has both toolchains.

The control frames get their own versioned artifact (FR-025) — and, because the service builds them as
bare dict literals today, a real service-side refactor so the service uses the contract it publishes.

**The whole pipeline was prototyped end-to-end against this repo before this plan was written.** See
[research.md](./research.md); every load-bearing claim below carries observed evidence.

## Technical Context

**Language/Version**: Python 3.11/3.12/3.13 (CI matrix); TypeScript 5 / Node 20 (CI), Node 22 (local)

**Primary Dependencies**: FastAPI 0.139.0, Pydantic 2.13.4 (both already present, unpinned);
`openapi-typescript` v7 (**new**, frontend devDependency, exact pin)

**Storage**: N/A — four checked-in generated files under `frontend/src/api/generated/`

**Testing**: pytest (schema + frame checks, `test` job); Vitest + `tsc` (console); npm script (type
freshness, `frontend` job)

**Target Platform**: Linux; the `remo-web` container and local `remo web serve`

**Project Type**: Web application — Python service (`src/remo_cli/web/`) + React SPA (`frontend/`)

**Performance Goals**: full regeneration < 60 s (SC-004); measured today: export ~1 s, generation 62 ms

**Constraints**:

- Zero runtime behavior change (FR-005, FR-011, FR-024)
- Byte-reproducible export (FR-007, SC-005)
- No new **runtime** dependency for the service
- **Docker frontend stage copies only `frontend/` and has no Python** — artifacts must live inside
  `frontend/`, and `npm run build` must not regenerate (research R7). This is a hard constraint.

**Scale/Scope**: 13 OpenAPI paths (9 console-facing), 19 component schemas, 6 control frames,
~12 hand-declared console interfaces to replace, 5 ad-hoc frame literals to eliminate.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

The constitution is Ansible-centric; principles I–III bind Ansible code, which this feature does not
touch. Principles IV and V apply directly and are the ones with teeth here.

| Principle | Applies? | Assessment |
|---|---|---|
| I. Defensive Variable Access (Ansible) | No | No Ansible changes |
| II. Test All Conditional Paths | **Yes** | Every conditional branch introduced is tested: artifact present/absent/unparseable (T-4, T-5), known vs. third-party provider type (T-8, T-9), in-union vs. off-union status value (S5, S6), and — critically — the frame parser's malformed/non-object/unknown-`type` branches, which must keep silently dropping (F-3). The one place this feature *could* change behavior is guarded by dedicated tests. |
| III. Idempotent by Default | **Yes** | Regeneration is idempotent by construction: SC-005 requires byte-identical output over 3 runs, and R-1 forbids the checks from writing tracked files. Running the pipeline twice produces an identical tree. |
| IV. Fail Fast with Clear Messages | **Yes** | The entire feature *is* this principle. FR-018 and drift-checks §3 (M-1..M-6) mandate messages that name the drifted artifact, group findings, and close with the exact remediation command. M-6 is added specifically so a dependency-bump failure does not send a contributor hunting for a source change that does not exist. |
| V. Documentation Reflects Reality | **Yes** | FR-027 removes the false "mirrors the spec exactly" comment — a textbook instance of the stale-docs failure this principle names. FR-026 adds `docs/maintaining-generated-types.md`; FR-028 updates `CLAUDE.md`/`AGENTS.md`, whose structure gate will fail otherwise. |

**Result**: PASS (initial). **Re-check after Phase 1**: PASS — no new violations; the design added no
component that principles II–V do not already cover. **Complexity Tracking**: not required.

## Project Structure

### Documentation (this feature)

```text
specs/020-openapi-type-generation/
├── plan.md                          # This file
├── spec.md                          # Feature specification (5 clarifications integrated)
├── research.md                      # Phase 0 — 11 findings, all verified against the repo
├── data-model.md                    # Phase 1 — vocabularies, model deltas, artifacts
├── quickstart.md                    # Phase 1 — 11 runnable validation scenarios
├── checklists/
│   └── requirements.md              # Spec quality checklist (16/16)
├── contracts/
│   ├── drift-checks.md              # Normative: check semantics + failure messages
│   └── terminal-frames-v1.md        # Normative: remo-terminal.v1 frame contract
└── tasks.md                         # Phase 2 — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
scripts/
└── export_openapi.py                # NEW — stdlib export of both contract artifacts

src/remo_cli/web/
├── frames.py                        # NEW — Pydantic control-frame models + unions (FR-021)
├── app.py                           # unchanged
├── health.py                        # MODIFIED — declare Health/Readiness responses (200 + 503)
└── api/
    ├── hosts.py                     # MODIFIED — enum annotations; KnownProviderType; ErrorEnvelope
    ├── terminals.py                 # MODIFIED — attach CreateTerminalResponse; route the 5 frame
    │                                #            literals through frames.py (FR-021a, SC-012)
    └── pairing.py                   # MODIFIED — declare mint/end response models

tests/unit/
├── test_schema_drift.py             # NEW — checks A + C-python (T-1..T-10)
└── web/
    └── test_frames.py               # NEW — frame round-trip + lenient-inbound invariant (F-3)

frontend/
├── package.json                     # MODIFIED — openapi-typescript (exact pin);
│                                    #            generate:types + check:types-fresh scripts
├── scripts/
│   └── check-types-fresh.mjs        # NEW — check B + C-node
└── src/
    ├── api/
    │   ├── generated/               # NEW — all four artifacts, never hand-edited
    │   │   ├── openapi.json
    │   │   ├── schema.d.ts
    │   │   ├── terminal-frames.json
    │   │   └── terminal-frames.d.ts
    │   └── client.ts                # MODIFIED — import generated types; ApiError untouched
    ├── components/providerMeta.ts   # MODIFIED — map over schema-derived values; keep default branch
    └── terminal/TerminalConnection.ts  # MODIFIED — import generated frame types

.github/workflows/ci.yml             # MODIFIED — one step in `frontend` (check B/C-node);
                                     #            check A/C-python rides the existing pytest step
docs/maintaining-generated-types.md  # NEW — contributor how-to (FR-026)
CLAUDE.md / AGENTS.md                # MODIFIED — structure diagrams (FR-028, gated by 019's check)
```

**Structure Decision**: The existing three-layer CLI architecture is untouched — this feature lives
entirely in `web/`, `frontend/`, `scripts/`, and `tests/`. No provider, registry, or `core/` module
changes. Artifact placement under `frontend/src/api/generated/` is dictated by the Docker frontend
stage (research R7), not chosen for taste.

## Implementation Phases

Ordered by dependency. Each phase is independently verifiable.

### Phase A — Contract completeness (US1/US2 foundation)

Declare what the service already returns. Purely additive to the document.

1. Annotate `InstanceOut.status: InstanceStatus`, `SessionTargetOut.zellij_state: ZellijState`,
   `devcontainer_running: DevcontainerRunning`; drop the `.value` unwrapping in `_instance_out`.
2. Add `KnownProviderType(str, Enum)` and type `instance_type: KnownProviderType | str` — verified to
   emit `anyOf[$ref, string]` with byte-identical wire output for known **and** third-party values
   (research R5).
3. Add `HealthResponse`, `ReadinessResponse`, `MintPairingResponse`, and `DetailResponse`. Declare
   `/ready` on **both** 200 and 503 via `responses=` — the console reads the body on both and that
   must not change. `POST /pairing/end` is a **204 with no body** (verified in the handler), so it gets
   no response model.
3a. Add `ErrorEnvelope` and declare it **only on the routes that actually return it** (`terminals.py`,
   `setup.py`, the `app.py` middleware). `pairing.py` returns `{"detail": ...}` on 403 and FastAPI's
   own 422s use `HTTPValidationError` — the envelope is *not* universal. Declaring it everywhere would
   publish a contract the service does not honor, which is the exact failure this feature prevents.
4. Attach the already-defined `CreateTerminalResponse` to `POST /terminals`.

**Verify**: existing service tests pass unmodified; response payloads byte-identical.

### Phase B — Export + drift check A (US1)

5. `scripts/export_openapi.py` — `json.dumps(create_app().openapi(), indent=2, sort_keys=True)` plus a
   trailing newline; `--stdout` mode for S4. Logging stays on stderr.
6. `tests/unit/test_schema_drift.py` following the docs-structure gate's structure: normative rules
   R-1..R-6, messages M-1..M-6, tests T-1..T-10. Must **fail**, not skip, without the `web` extra.

**Verify**: quickstart S2, S3, S4, S7.

### Phase C — Generation + console consumption (US2)

7. Pin `openapi-typescript` exactly; add `generate:types` and `check:types-fresh` scripts. `build` is
   **not** modified.
8. Rewrite `client.ts`'s type layer to import from `generated/schema.d.ts`. `ApiError`, `request()`,
   the `opaqueredirect` re-auth path, the sessionStorage cooldown, `getReady()`'s dual-status body
   read, and `mintPairingCode()`'s synthesized 403 all stay exactly as they are (FR-011).
9. `frontend/scripts/check-types-fresh.mjs` — regenerate to a temp path, byte-compare, never write a
   tracked file.

**Verify**: quickstart S2, S8; `npm run test` passes with zero test files modified.

### Phase D — Vocabularies in the console (US3)

10. `providerMeta.ts` maps over `components["schemas"]["KnownProviderType"]` and `InstanceStatus`
    instead of re-declaring them. Exhaustiveness enforced at compile time (FR-013) **and** the
    `default:` branch retained (FR-013a) — add a test that feeds an off-union value (SC-010).
11. Guard test: `KnownProviderType` members equal the built-in descriptor names (T-8), so a
    first-party provider addition fails loudly while a third-party install cannot move the artifact (T-9).

**Verify**: quickstart S5, S6, S7.

### Phase E — Control frames (US4)

12. `web/frames.py` — six frame models, two discriminated unions, `ErrorClass`.
13. Route all five `_send_control` literals and `_handle_control`'s parsing through it (SC-012). **The
    lenient-inbound invariant (F-3) is the risk**: malformed JSON, non-object payloads, and unknown
    `type` values must keep being silently dropped, never raise. Dedicated tests.
14. Emit `terminal-frames.json` from the same script; generate `.d.ts`; `TerminalConnection.ts` imports
    it; extend checks A/B to cover it (check C).

**Verify**: quickstart S9; terminal tests pass unmodified.

### Phase F — Documentation (FR-026..FR-029)

15. `docs/maintaining-generated-types.md`; delete the false "mirrors the spec exactly" comment; update
    `CLAUDE.md`/`AGENTS.md` structure diagrams (019's gate fails otherwise); state that the artifact is
    an internal build input with no external compatibility promise.

**Verify**: quickstart S10, S11; `uv run pytest tests/unit/test_docs_structure.py` green.

## Risks

| Risk | Mitigation |
|---|---|
| **Frame refactor changes WS behavior** — the one place runtime behavior can actually move. `_handle_control` silently swallows bad frames today; naive Pydantic validation would raise and could tear down the socket. | F-3 is normative; dedicated tests for malformed/non-object/unknown-type; existing terminal tests must pass unmodified (FR-024, S8) |
| **Dependency bumps redden the checks with no source change** — FastAPI and Pydantic are unpinned. | Accepted and documented; M-6 requires the failure message to name this case explicitly so nobody hunts a phantom change |
| **Docker build breaks** — if artifacts land outside `frontend/` or `build` starts regenerating. | Artifact location is a hard constraint (R7); `build` deliberately untouched; S10 builds the image |
| **"Exhaustiveness" achieved by deleting the fallback** — a plausible misreading of FR-013 that would break stale-bundle clients. | FR-013a + SC-010 + an explicit off-union test (S6) |
| **Provider vocabulary drifts from the real registry** | T-8 asserts equality with the built-in descriptors; T-9 asserts a third-party install cannot perturb the artifact |
| **A new model documents an idealized shape rather than the real one** | data-model.md §2 constraint: if a handler's body does not match a proposed model, the model is wrong — not the handler |

## Post-Design Constitution Re-Check

PASS. Phase 1 introduced no component requiring justification. Principle II is satisfied by the
conditional-path coverage listed in the gate table; Principle IV is the feature's core deliverable;
Principle V is discharged by Phase F. No entries in Complexity Tracking.
