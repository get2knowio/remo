# Maintaining the generated API/type artifacts

remo-web's browser console consumes types generated from the service's own definitions
rather than hand-written duplicates (feature `020-openapi-type-generation`). This doc
covers the four generated artifacts, when to regenerate them, and how to read a
drift-check failure. Normative specs:
`specs/020-openapi-type-generation/contracts/drift-checks.md` and
`specs/020-openapi-type-generation/contracts/terminal-frames-v1.md`.

## The four artifacts

| Artifact | What it is | Generated from | Regenerate with |
|---|---|---|---|
| `frontend/src/api/generated/openapi.json` | The REST OpenAPI 3.x document | `create_app().openapi()` (FastAPI) | `uv run python scripts/export_openapi.py` |
| `frontend/src/api/generated/schema.d.ts` | TypeScript types for the REST surface | `openapi-typescript` over `openapi.json` | `npm run generate:types` (from `frontend/`) |
| `frontend/src/api/generated/terminal-frames.json` | The `remo-terminal.v1` WebSocket control-frame contract | `TypeAdapter(...).json_schema()` over `src/remo_cli/web/frames.py` | `uv run python scripts/export_openapi.py` |
| `frontend/src/api/generated/terminal-frames.d.ts` | TypeScript types for the control frames | `frontend/scripts/generate-frame-types.mjs` over `terminal-frames.json` | `npm run generate:types` (from `frontend/`) |

`uv run python scripts/export_openapi.py` writes both `.json` artifacts in one
invocation. `npm run generate:types` (run from `frontend/`) regenerates both `.d.ts`
artifacts in one invocation — it now runs `openapi-typescript` for the REST schema
*and* `node scripts/generate-frame-types.mjs` for the frame contract.

All four files begin with a generated-file header naming their regeneration command —
they are never hand-edited.

## The REST contract and the frame contract version independently

`openapi.json`/`schema.d.ts` describe the REST surface; `terminal-frames.json`/
`terminal-frames.d.ts` describe the separately-versioned `remo-terminal.v1` WebSocket
control-frame protocol (`frame_version` in the envelope, `remo-terminal.v1` in the WS
subprotocol name). These two contracts are deliberately decoupled (FR-023, F-6 in
`contracts/terminal-frames-v1.md` §3): a REST change never forces a frame version bump,
and a frame change never forces a REST/OpenAPI version bump. There is no shared version
number between them, and no CI check requires them to move together.

## When regeneration is required

Regenerate and commit the affected artifact(s) whenever:

- A FastAPI route, request/response model, or `KnownProviderType` member changes → run
  `uv run python scripts/export_openapi.py`, then `npm run generate:types`.
- A frame model in `src/remo_cli/web/frames.py` changes (a field, a frame type, the
  `InboundFrame`/`OutboundFrame` union membership) → same two commands.
- A FastAPI, Pydantic, or `openapi-typescript` dependency is upgraded — even with no
  first-party source change, a generator version bump can change the emitted bytes. The
  fix is the same: regenerate and commit.

## How the drift checks work

Three checks enforce that the checked-in artifacts always match what the current
sources would produce (`contracts/drift-checks.md`):

- **Check A** (`tests/unit/test_schema_drift.py`, pytest): live `create_app().openapi()`
  vs. checked-in `openapi.json`, byte-for-byte.
- **Check B** (`frontend/scripts/check-types-fresh.mjs`, `npm run check:types-fresh`):
  regenerating `schema.d.ts` from the checked-in `openapi.json` vs. the checked-in
  `schema.d.ts`, byte-for-byte.
- **Check C**: the frame contract, split the same way the Python/Node toolchains are
  split in CI — a Python half (`test_schema_drift.py`, live frame models vs. checked-in
  `terminal-frames.json`) and a Node half (`check-types-fresh.mjs`, regenerated
  `terminal-frames.d.ts` vs. checked-in), both reusing the exact same comparison
  machinery as checks A/B respectively.

None of these checks write to a tracked file — they only compare against temporary
regeneration output, so a failing check never silently "fixes" the repo for you.

### Reading a failure message

Every failure names the artifact that drifted, groups the specific things that changed
(e.g. which OpenAPI path or which frame model), and closes with the exact command to
run. For example, a Python-side (check A/C) failure looks like:

```text
frontend/src/api/generated/openapi.json is out of sync with the FastAPI application.

  Paths present in the app but not in the checked-in schema (1):
    - /api/v1/ready  (get)

To fix: regenerate and commit the artifact:

    uv run python scripts/export_openapi.py

If you did not change the API, a FastAPI/Pydantic/generator dependency upgrade can
also cause this failure with no first-party source change -- regenerating and
committing is still the correct fix.

See docs/maintaining-generated-types.md.
```

The frame check's messages (`test_schema_drift.py`'s frame tests, and
`check-types-fresh.mjs`'s frame half) read as the same family, sharing the exact
`To fix:`/dependency-bump/doc-link boilerplate, adapted to name the drifted frame (e.g.
`ResizeFrame`) or union (`the inbound union`) instead of an OpenAPI path or component.

### The dependency-bump case

If none of these checks fail because of a change you made, check whether `fastapi`,
`pydantic`, or `openapi-typescript` was recently bumped. A generator version change can
alter the emitted bytes (formatting, ordering, added metadata) with zero first-party
source change. The fix is identical either way: regenerate and commit.

## Not an external contract

All four artifacts are an **internal build input** for this repository's own console —
they carry no external compatibility promise and no deprecation policy (FR-029). Nothing
outside this repo is expected to parse `openapi.json` or `terminal-frames.json` as a
stable public API; both can change shape freely between releases as long as the checked-in
artifact and the source it was generated from stay in sync. Don't treat a shape change
here as a breaking-change event requiring a deprecation window — regenerate and commit.
