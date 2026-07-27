# Quickstart: Validating Schema-Derived Frontend Types

**Feature**: `020-openapi-type-generation` | **Date**: 2026-07-27

Runnable scenarios that prove the feature works. Each maps to a success criterion. Run from the repo
root unless noted.

## Prerequisites

```bash
uv sync --all-extras        # Python + the `web` extra (needed to build the app)
cd frontend && npm ci       # Node toolchain + the pinned generator
```

---

## S1 — Regenerate everything (SC-004)

Two commands, one per toolchain — the service side and the console side share no runtime, and the
Docker frontend stage has no Python at all (research R7):

```bash
uv run python scripts/export_openapi.py     # REST contract + frame contract   (Python)
cd frontend && npm run generate:types        # schema.d.ts + terminal-frames.d.ts (Node)
```

**Expected**: four artifacts under `frontend/src/api/generated/`, no network access, no running
service, no credentials, well under 60 s combined. `git status` clean if nothing drifted.

---

## S2 — All three drift checks pass on a clean tree (SC-002 baseline)

```bash
uv run pytest tests/unit/test_schema_drift.py -q      # checks A + C-python
cd frontend && npm run check:types-fresh              # checks B + C-node
```

**Expected**: green, zero findings.

---

## S3 — A backend field rename is caught (SC-002, FR-015)

```bash
# Rename a field on a scratch branch, e.g. InstanceOut.region -> InstanceOut.location
uv run pytest tests/unit/test_schema_drift.py -q
```

**Expected**: fails, naming `InstanceOut` and printing:

```text
To fix: regenerate and commit the artifact:
    uv run python scripts/export_openapi.py
```

Then regenerate and re-run the console type check:

```bash
uv run python scripts/export_openapi.py
cd frontend && npm run generate:types && npm run lint
```

**Expected**: `tsc` fails at the exact call sites reading the removed field. Revert when done.

---

## S4 — Export determinism (SC-005)

```bash
for i in 1 2 3; do
  uv run python scripts/export_openapi.py --stdout | sha256sum
done
```

**Expected**: three identical digests.

---

## S5 — A new status value is caught at build time (SC-003, FR-013)

Add a member to `InstanceStatus` in `src/remo_cli/models/discovery.py`, then:

```bash
uv run python scripts/export_openapi.py
cd frontend && npm run generate:types && npm run lint
```

**Expected**: `tsc` fails in `components/providerMeta.ts` — the status presentation mapping is no
longer exhaustive. Supply a presentation, re-run, green. Revert when done.

---

## S6 — The runtime fallback survives (SC-010, FR-013a)

```bash
cd frontend && npm run test
```

**Expected**: the test that feeds an off-union status value to the presentation layer passes — a
neutral fallback renders, nothing throws. This is the guard against "achieving exhaustiveness" by
deleting the `default:` branch.

---

## S7 — Third-party providers cannot perturb the artifact (SC-011, FR-004a)

```bash
uv run pytest tests/unit/test_schema_drift.py -k third_party -q
```

**Expected**: registering a provider type outside the built-in set leaves the exported artifact
byte-identical. A first-party built-in addition, by contrast, fails `T-8` with instructions to update
`KnownProviderType`.

---

## S8 — Unchanged behavior (SC-007, FR-011, FR-024)

```bash
uv run pytest -q
cd frontend && npm run test
```

**Expected**: both suites green with **zero test files modified** to accommodate the change. Pay
attention to the terminal-connection tests — reconnect budget, ping/pong RTT, and close-code handling
must be untouched by the frame refactor.

---

## S9 — The frame refactor is complete (SC-012, FR-021a)

```bash
grep -n '"v": 1' src/remo_cli/web/api/terminals.py
```

**Expected**: no matches. All five ad-hoc frame literals now go through `web/frames.py`.

Then confirm the lenient-inbound invariant (F-3) explicitly:

```bash
uv run pytest tests/unit/web -k "malformed or unknown_frame" -q
```

**Expected**: malformed JSON, non-object payloads, and unknown `type` values are silently dropped —
no exception, no socket close.

---

## S10 — The Docker image still builds (research R7)

```bash
docker build -f docker/Dockerfile -t remo-web:drift-check .
```

**Expected**: succeeds unchanged. The frontend stage copies only `frontend/`, so the checked-in
artifacts must be inside it and `npm run build` must not attempt regeneration.

---

## S11 — Contributor recovery path (SC-008)

Hand a colleague a repo with a deliberately stale artifact. They should reach green using only the
failure message and `docs/maintaining-generated-types.md` — without opening the check's source.

---

## Reference

- Requirements: [`spec.md`](./spec.md)
- Verified findings: [`research.md`](./research.md)
- Shapes and artifacts: [`data-model.md`](./data-model.md)
- Check semantics and messages: [`contracts/drift-checks.md`](./contracts/drift-checks.md)
- Frame contract: [`contracts/terminal-frames-v1.md`](./contracts/terminal-frames-v1.md)
