# Phase 1 Data Model: Schema-Derived Frontend Types

**Feature**: `020-openapi-type-generation` | **Date**: 2026-07-27

This feature adds almost no new domain data. It changes the *provenance and precision* of existing
shapes. The tables below record, for each entity, what exists today, what it becomes, and which
requirement forces the change.

---

## 1. Vocabularies (service-owned, closed unless noted)

| Vocabulary | Source of truth | Members | Wire form | Publishes as |
|---|---|---|---|---|
| `InstanceStatus` | `models/discovery.py` (**exists**) | `ok`, `unreachable`, `auth_failed`, `no_remo_host`, `incompatible_protocol`, `malformed`, `timeout` | string | closed enum → TS string union |
| `ZellijState` | `models/session_target.py` (**exists**) | `active`, `exited`, `absent` | string | closed enum → TS string union |
| `DevcontainerRunning` | `models/session_target.py` (**exists**) | `running`, `stopped`, `unknown` | string | closed enum → TS string union |
| `KnownProviderType` | **new**, `web/api/hosts.py` | `incus`, `hetzner`, `aws`, `proxmox` | string | `anyOf[$ref, string]` — **open** |

**`KnownProviderType` invariants**

- **Fixed by the built-in set, not by what is installed** (FR-004a, SC-011). Declared explicitly as a
  literal enum, *not* derived at runtime from `provider_registry.all_descriptors()`.
- Guarded by a unit test asserting its members equal the built-in descriptor names. A first-party
  provider addition therefore fails a test with a clear instruction; a third-party install cannot
  perturb the exported artifact.
- The field it annotates stays open (`KnownProviderType | str`), so an unrecognized type is still
  valid data (FR-014, SC-009).

**Runtime-value rule (FR-013a, SC-010)**: the console must tolerate a vocabulary value outside its
compiled union. Compile-time exhaustiveness governs the *mapping*; the *runtime* keeps a fallback.

---

## 2. Service response models — declaration changes

`Δ` = what changes. No entry changes a serialized byte (FR-005).

| Model | Location | Δ | Requirement |
|---|---|---|---|
| `InstanceOut` | `web/api/hosts.py` | `status: str` → `InstanceStatus`; `instance_type: str` → `KnownProviderType \| str` | FR-003, FR-004 |
| `SessionTargetOut` | `web/api/hosts.py` | `zellij_state: str` → `ZellijState`; `devcontainer_running: str` → `DevcontainerRunning`; `instance_type` as above | FR-003, FR-004 |
| `CapabilityOut`, `HostsResponse`, `SessionsResponse`, `RefreshRequest`, `RefreshResponse`, `TerminalOut`, `TerminalsListResponse` | `web/api/hosts.py`, `web/api/terminals.py` | none — already correct | — |
| `CreateTerminalResponse` | `web/api/terminals.py` | **exists but is not attached to its route**; attach so it reaches `components` | FR-001 |
| `ErrorOut` | `web/api/hosts.py` | promote to the shared error payload; today it reaches `components` only incidentally via `InstanceOut.error` | FR-002 |

### New models (declaration-only)

| Model | Shape | Why |
|---|---|---|
| `ErrorEnvelope` | `{ error: ErrorOut }` | The wire envelope every failure response already uses; nothing declares it (FR-002) |
| `HealthResponse` | `{ status: str }` | `/health` publishes an untyped open object today (FR-001) |
| `ReadinessResponse` | `{ status: str, checks: dict[str, str], detail?: str }` | `/ready` publishes `{}` today. Declared on **both** 200 and 503 via `responses=`, because the console deliberately reads the body on both (FR-001, R11) |
| `MintPairingResponse` | `{ code: str, expires_in: int }` | `/pairing/mint` publishes `{}` today (FR-001) |
| `DetailResponse` | `{ detail: str }` | `/pairing/mint`'s **403** returns this, *not* the error envelope — verified in `web/api/pairing.py` |

> **Constraint on every new model**: it documents what the handler *already returns*. If a handler's
> current body does not match a proposed model, the model is wrong — not the handler.

### Verified handler-body facts that override earlier assumptions

Read from the source, not inferred. These correct two guesses made before Phase 1:

- **`POST /pairing/end` returns HTTP 204 with no body** (`Response(status_code=204)`), not a JSON
  object. Declare it as a 204 with no response model. The console's `endPairing()` uses
  `sendBeacon` and ignores the response, so nothing downstream changes.
- **The `{"error": {...}}` envelope is not universal.** It is used by `web/api/terminals.py`,
  `web/api/setup.py`, and the `app.py` middleware — but `web/api/pairing.py` returns
  `{"detail": "..."}` on 403, and FastAPI's own 422 responses use `HTTPValidationError`. FR-002 is
  therefore satisfied by declaring `ErrorEnvelope` **per route where that route actually returns it**,
  and declaring `DetailResponse` for pairing's 403. Declaring the envelope everywhere would publish a
  contract the service does not honor — the exact failure mode this feature exists to prevent.
  The console is unaffected either way: it synthesizes its own `forbidden` error for the pairing 403
  and never reads that body.

---

## 3. Terminal control frames (new: `web/frames.py`)

No definition exists today; frames are bare dict literals at five call sites (research R10).

**Common envelope**: every frame carries `v: 1` and a `type` discriminator.

| Direction | Frame | Fields |
|---|---|---|
| browser → service | `resize` | `cols: int`, `rows: int` (clamped to safe bounds by existing logic) |
| browser → service | `ping` | — |
| service → browser | `ready` | — |
| service → browser | `exit` | `code: int` |
| service → browser | `error` | `class: ErrorClass`, `message: str` |
| service → browser | `pong` | — |

**`ErrorClass`** (closed): `auth`, `network`, `remote_capability`, `missing_project`, `remote_launch`.

**Unions**: `InboundFrame` (resize | ping) and `OutboundFrame` (ready | exit | error | pong),
discriminated on `type`.

**Behavioral invariant (FR-024)**: `_handle_control` today silently ignores malformed JSON, non-dict
payloads, and unknown `type` values. Validation must preserve exactly that — an invalid inbound frame
is dropped, never raised. This is the single highest-risk behavior change in the feature and needs
dedicated tests.

---

## 4. Artifacts

| Artifact | Path | Produced by | Consumed by | Hand-edited? |
|---|---|---|---|---|
| REST contract | `frontend/src/api/generated/openapi.json` | `create_app().openapi()`, `sort_keys=True, indent=2`, trailing newline | the type generator; drift check A | **never** |
| REST types | `frontend/src/api/generated/schema.d.ts` | `openapi-typescript` (exact pin) | `api/client.ts`, `components/providerMeta.ts`; drift check B | **never** |
| Frame contract | `frontend/src/api/generated/terminal-frames.json` | Pydantic `TypeAdapter(...).json_schema()` in an envelope carrying `protocol: "remo-terminal.v1"` | frame type generation; drift check C | **never** |
| Frame types | `frontend/src/api/generated/terminal-frames.d.ts` | generator | `terminal/TerminalConnection.ts` | **never** |

All four live under `frontend/` because the Docker frontend build stage copies only `frontend/` and
has no Python (research R7). All four carry a generated-file header naming the regeneration command.

---

## 5. Console-owned types that stay hand-written (FR-012)

Not service contract; must **not** be pushed through the generated artifact:

- `ApiError` class, and the synthesized `network_error` / `auth_required` / `auth_challenge` /
  `forbidden` / `unknown` error values.
- `ServiceStatus` — deliberately an open union (`"ok" | "unconfigured" | (string & {})`).
- `ReadinessCheck` alias, `TerminalConnectionState`, `TerminalConnectionCallbacks`.
- Renderer, workspace-layout, and settings types.

---

## 6. Traceability

| Requirement | Realized by |
|---|---|
| FR-001 | §2 new models + attach `CreateTerminalResponse` |
| FR-002 | §2 `ErrorEnvelope` |
| FR-003 | §1 + §2 annotation changes |
| FR-004 / FR-004a | §1 `KnownProviderType`, explicit literal + guard test |
| FR-005 | §2 constraint; enum values serialize identically; `anyOf` verified byte-identical (research R5) |
| FR-013 / FR-013a | §1 runtime-value rule |
| FR-021 / FR-021a | §3 |
| FR-024 | §3 behavioral invariant |
| SC-011 | §1 fixed-by-built-ins invariant |
| SC-012 | §3 — five dict literals eliminated |
