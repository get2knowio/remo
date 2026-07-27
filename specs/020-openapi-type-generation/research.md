# Phase 0 Research: Schema-Derived Frontend Types

**Feature**: `020-openapi-type-generation` | **Date**: 2026-07-27

Every finding below was verified by running it against this repository, not inferred. Commands and
observed output are recorded so the plan can be re-validated cheaply.

---

## R1. Can the app's OpenAPI document be exported without running a server?

**Decision**: Yes — `create_app().openapi()` returns the document as a plain dict. Export is a
~10-line stdlib script; no server, port, credential, or registry state involved (FR-006).

**Evidence** (run in this repo):

```text
openapi version: 3.1.0
deterministic across two app builds: True
bytes: 24824
paths: 13   (/api/v1/{health,ready,hosts,sessions,discovery/refresh,pairing/mint,pairing/end,
             setup/{status,identity,registry,verify},terminals,terminals/{terminal_id}})
components.schemas: 19
```

**Rationale**: `create_app(settings=None)` builds from `WebSettings` defaults and touches no external
state. It does emit an INFO log line about operator auth on construction, so the export script must
write JSON to a file (or keep logging on stderr) rather than assuming a clean stdout.

**Alternatives considered**: Booting uvicorn and fetching `/openapi.json` (rejected — needs a port,
is slower, and is nondeterministic under CI contention).

---

## R2. Is the export byte-reproducible?

**Decision**: Yes, with `json.dumps(doc, indent=2, sort_keys=True)` plus a trailing newline
(FR-007, SC-005). Two independent `create_app()` builds produced identical strings.

**Rationale**: FastAPI builds the document deterministically from route registration order, and
`sort_keys=True` removes any residual dict-ordering sensitivity — including across Python 3.11/3.12/3.13,
which the CI matrix runs.

**Residual risk, accepted**: a FastAPI or Pydantic upgrade can legitimately change the emitted
document with no first-party source change, reddening the drift check. `pyproject.toml` pins neither
(`"fastapi"`, `"pydantic>=2"`). This is the same class of churn the spec already accepted for the
TypeScript generator: the fix is a regeneration commit, and the failure message must say so
explicitly so the contributor does not go hunting for a phantom source change.

---

## R3. What does the current document actually publish for the console's endpoints?

**Decision**: Four of the console's calls publish nothing usable and must be declared (FR-001, FR-002).

**Evidence**:

| Endpoint | Published response schema today |
|---|---|
| `GET /api/v1/hosts` | `$ref HostsResponse` ✅ |
| `GET /api/v1/sessions` | `$ref SessionsResponse` ✅ |
| `POST /api/v1/discovery/refresh` | `$ref RefreshResponse` ✅ |
| `GET /api/v1/terminals` | `$ref TerminalsListResponse` ✅ |
| `DELETE /api/v1/terminals/{id}` | 204, no body ✅ |
| `GET /api/v1/health` | `{"type":"object","additionalProperties":true}` ⚠️ untyped |
| `GET /api/v1/ready` | `{}` ❌ generates `unknown` |
| `POST /api/v1/pairing/mint` | `{}` ❌ generates `unknown` |
| `POST /api/v1/pairing/end` | `{}` ❌ generates `unknown` |
| `POST /api/v1/terminals` | `{}` ❌ generates `unknown` — `CreateTerminalResponse` **is** defined in `terminals.py` but is not attached to the route, so it never reaches `components` |

No route declares the `{"error": {...}}` envelope, so `ErrorOut` reaches `components` only
incidentally (via `InstanceOut.error`) and is not connected to any failure response.

**Approach**: attach `response_model` / `responses={...}` declarations. `/ready` returns 200 **or**
503 with the same body shape and must keep doing so — declare both via `responses=` while leaving the
handler returning `JSONResponse`, which changes the document without touching runtime behavior (FR-005).

---

## R4. How are the closed vocabularies published today?

**Decision**: As bare strings. Narrowing them is a small, local annotation change (FR-003).

**Evidence**:

```json
InstanceOut.status         -> {"type": "string", "title": "Status"}
SessionTargetOut.zellij_state -> {"type": "string", "title": "Zellij State"}
InstanceOut.instance_type  -> {"type": "string", "title": "Instance Type"}
```

The domain enums **already exist** and are already closed:

- `models/discovery.py::InstanceStatus(str, Enum)` — 7 members
- `models/session_target.py::ZellijState(str, Enum)` — 3 members
- `models/session_target.py::DevcontainerRunning(str, Enum)` — 3 members

`web/api/hosts.py` widens them to `str` at the boundary (`status=snapshot.status.value`). Annotating
the response models with the existing enums and dropping the `.value` unwrapping publishes them
without changing a single serialized byte — a `str`-Enum serializes to its value.

**Verified generation** (openapi-typescript 7.13.0 over a full document):

```ts
InstanceStatus: "ok" | "unreachable" | "auth_failed" | "no_remo_host"
              | "incompatible_protocol" | "malformed" | "timeout";
ZellijState: "active" | "exited" | "absent";
```

---

## R5. How is an *open* vocabulary published without an orphan schema?

**Problem**: FR-004 wants the known provider set published as a named, referenceable vocabulary while
FR-014/SC-009 keep the wire field open. A standalone enum that no model references never reaches
`components` — FastAPI only emits schemas reachable from a route.

**Decision**: Type the field as `KnownProviderType | str`.

**Evidence**:

```text
Probe(instance_type='aws').model_dump_json()    -> {"instance_type":"aws"}
Probe(instance_type='vultr').model_dump_json()  -> {"instance_type":"vultr"}

schema: {"instance_type": {"anyOf": [{"$ref": "#/components/schemas/KnownProviderType"},
                                     {"type": "string"}]}}
```

This gets all three properties at once: wire output is **byte-identical** for both known and
third-party values (FR-005), the vocabulary becomes a genuinely referenced component that the console
can enumerate (FR-004), and the field stays open (FR-014, SC-009).

`providerMeta.ts` reads `components["schemas"]["KnownProviderType"]` to enumerate the known set. TypeScript
collapses `KnownProviderType | string` to `string` at the field, which is correct — the field really is open.

**Alternatives considered**:

- *Override `app.openapi()` to inject the orphan enum into `components`.* Works, but adds a
  document-mutation hook and puts a schema in the doc that no path references — the same smell the
  clarification session rejected for the WebSocket frames.
- *Read the live provider registry at export time.* Rejected by FR-004a: installing a third-party
  provider would change the exported artifact and redden the drift check with no source change
  (SC-011). `KnownProviderType` is declared explicitly, with a unit test asserting it equals
  `provider_registry.all_descriptors()`'s built-in names — so a *first-party* provider addition is
  caught, while a third-party install cannot perturb the artifact.

---

## R6. Generator choice and output shape

**Decision**: `openapi-typescript` v7, pinned to an exact version (no caret) in
`frontend/devDependencies`.

**Evidence**: v7.13.0 consumed the real 921-line exported document in 62 ms and emitted a
`components["schemas"][...]` tree with correct optionality (`capability?: … | null`,
`refreshed_at?: string | null`, `@default` annotations preserved).

**Rationale**: it is the de-facto FastAPI/OpenAPI-3.1 companion, emits types only (no runtime code, so
nothing ships in the bundle), and needs no schema preprocessing. Node 20 in CI and Node 22 locally both
satisfy its engine requirement. An exact pin keeps SC-005's reproducibility claim honest — a caret
range would let a transitive patch bump redden the drift check.

**Alternatives considered**: `openapi-fetch` / `orval` / `openapi-generator` (all rejected — they
generate a *client*, and the spec is explicit that this is a type-provenance change, not a client
rewrite; FR-011 requires the existing `request()`/`ApiError` path to survive untouched).

---

## R7. Where must the artifacts live?

**Decision**: under `frontend/src/api/generated/` — both `openapi.json` and `schema.d.ts`.

**Rationale — this is a hard constraint, not a preference.** `docker/Dockerfile` stage 1 is:

```dockerfile
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/ .
RUN npm ci && npm run build
```

The frontend build stage copies **only `frontend/`** and has no Python and no `src/`. Any artifact
the build reads must therefore sit inside `frontend/`. This independently confirms the spec's
checked-in-artifact decision: build-time generation from the Python app is impossible in the image.

**Corollary**: `npm run build` must **not** regenerate. Generation is an explicit script; the drift
check is a separate CI step. The Docker build stays byte-for-byte unchanged.

---

## R8. Where does each drift check run?

**Decision**: split across the two existing CI jobs, because neither job has both toolchains.

| Check | Compares | Job | Mechanism |
|---|---|---|---|
| A. Schema freshness (FR-015) | `create_app().openapi()` vs. checked-in `openapi.json` | `test` (Python matrix, `uv sync --all-extras`) | pytest module, mirroring `test_docs_structure.py` |
| B. Type freshness (FR-016) | `openapi.json` → regenerate vs. checked-in `schema.d.ts` | `frontend` (Node only) | `npm run check:types-fresh`, regenerate to a temp file and diff |
| C. Frame freshness (FR-022) | frame models → schema vs. checked-in frame artifact | `test` (Python) + `frontend` | same split as A/B |

The `frontend` job (`.github/workflows/ci.yml:109`) installs only Node; the `test` job
(`ci.yml:10`) installs Python with `--all-extras`, so the `web` extra is present and check A cannot
silently skip (FR-017).

**On FR-017's "not skippable"**: check A lives in the always-collected `tests/unit/` tree and must
**fail**, not `pytest.skip`, when the `web` extra is missing — the same reasoning
`test_docs_structure.py` records for refusing to skip on a missing heading ("CI would stay green with
zero coverage"). Since `uv sync --all-extras` is what CI runs, a missing extra means a broken
environment, which is a legitimate failure.

---

## R9. Failure-message style

**Decision**: reuse `tests/unit/test_docs_structure.py`'s established shape verbatim in structure:
a header naming the drifted artifact, findings grouped by kind with counts, one item per line, then a
trailing `To fix:` block naming the exact command and linking a contributor how-to.

**Rationale**: FR-018 and the spec's Assumptions call for the checks to "read as one family, not three
inventions". That module already encodes the useful details — group by kind, show counts, align paths,
close with remediation plus a doc link.

**Concrete addition for this feature**: the remediation text must name the *dependency-bump* case
(R2, R6) explicitly — "if you did not change the API, a FastAPI/Pydantic/generator upgrade can also
cause this; regenerate and commit" — otherwise a contributor hunts for a source change that does not exist.

---

## R10. Control frames: what exists today?

**Decision**: nothing publishable exists; the frame definition must be created (FR-021, FR-021a).

**Evidence** — `src/remo_cli/web/api/terminals.py` builds frames as bare dict literals at five call
sites (lines 308, 324, 394, 404, 449):

```python
await _send_control(websocket, {"v": 1, "type": "ready"})
await _send_control(websocket, {"v": 1, "type": "exit", "code": rc})
await _send_control(websocket, {"v": 1, "type": "pong"})
async def _send_control(websocket: WebSocket, payload: dict) -> None: ...
```

and parses inbound frames by key lookup (`payload.get("type")`, `payload.get("cols", 80)`).

The WebSocket route does **not** appear in the OpenAPI document — confirmed: `/api/v1/terminals/{terminal_id}`
lists only `delete`. This is why the frames need their own artifact rather than a corner of the REST document.

**Approach**: Pydantic models for the six frame types in a new `web/frames.py`, a discriminated union
per direction, `_send_control` typed to accept a model, `_handle_control` validating through the union.
The frame artifact is `TypeAdapter(...).json_schema()` output wrapped in a small envelope carrying
`protocol: "remo-terminal.v1"`. The console generates from that JSON Schema.

**Behavior risk to control (FR-024)**: `_handle_control` currently swallows malformed frames silently
(`return` on bad JSON / non-dict / unknown type). Validation must preserve that — an invalid frame is
ignored, never an exception that would tear down the socket. This is the one place where the refactor
could change observable behavior, so it needs explicit tests.

---

## R11. What must *not* change

Pinned by FR-005/FR-011/FR-024 and re-verified as a design constraint:

- `request()`'s `redirect: "manual"` / `opaqueredirect` forward-auth re-authentication path, its
  sessionStorage cooldown, and the synthesized `auth_required` / `auth_challenge` / `network_error`
  errors — all console-owned, none schema-derived.
- `getReady()`'s deliberate read of the body on **both** 200 and 503, and its
  `status ?? (ok ? "ready" : "not_ready")` fallback.
- `mintPairingCode()`'s hand-synthesized 403 `forbidden` error.
- `ServiceStatus`'s open-union `"ok" | "unconfigured" | (string & {})` — console-owned, deliberately
  open, stays local per FR-012.
- The `providerMeta.ts` `default:` branch — FR-013a and SC-010 require it to survive.

---

## Resolved unknowns

| Unknown | Resolution |
|---|---|
| Server-free export possible? | Yes — `create_app().openapi()` (R1) |
| Deterministic? | Yes — `sort_keys=True` (R2) |
| Which endpoints need declaring? | `/ready`, `/pairing/mint`, `/pairing/end`, `POST /terminals`, plus the error envelope; `/health` needs tightening (R3) |
| How to publish closed enums? | Annotate with the existing domain enums (R4) |
| How to publish an open vocabulary? | `KnownProviderType \| str` → `anyOf` (R5) |
| Generator? | `openapi-typescript` v7, exact pin (R6) |
| Artifact location? | `frontend/src/api/generated/` — forced by the Docker frontend stage (R7) |
| CI placement? | Schema check in `test`, type check in `frontend` (R8) |
| Frame definition source? | New `web/frames.py` Pydantic models; none exists today (R10) |

No `NEEDS CLARIFICATION` markers remain.
