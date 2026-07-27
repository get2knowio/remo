# Contract: Type-Provenance Drift Checks

**Feature**: `020-openapi-type-generation` | **Status**: normative

This document is the normative specification for the three drift checks (FR-015, FR-016, FR-022) and
their failure messages (FR-018). It is modeled on
`specs/019-hygiene-deps-docs/contracts/docs-structure-check.md`, which governs
`tests/unit/test_docs_structure.py`; the two must read as one family.

---

## 1. The checks

| ID | Name | Generates | Compares against | Runs in |
|---|---|---|---|---|
| **A** | schema freshness | `create_app().openapi()` | `frontend/src/api/generated/openapi.json` | pytest (`test` job) |
| **B** | REST type freshness | `openapi-typescript` over the checked-in `openapi.json` | `frontend/src/api/generated/schema.d.ts` | npm script (`frontend` job) |
| **C** | frame contract freshness | frame-model JSON Schema | `frontend/src/api/generated/terminal-frames.json` + `.d.ts` | pytest (A-half) + npm (B-half) |

**Why split**: the `frontend` CI job installs only Node; the `test` job installs Python with
`--all-extras`. Neither has both toolchains. Checks A and C-python live with Python; B and C-node live
with Node.

---

## 2. Normative rules

- **R-1 (no side effects, FR-019)**: a check MUST NOT write to any tracked file. Regeneration is a
  separate, explicit contributor action. Checks write only to temporary paths.
- **R-2 (byte comparison)**: comparison is on exact bytes, including trailing newline. The generator
  and its serialization options are part of the artifact's identity.
- **R-3 (no silent skip, FR-017)**: a check MUST NOT `skip` when its toolchain or the `web` extra is
  absent — it MUST fail. Rationale, inherited verbatim from the docs-structure gate: a skip leaves CI
  green with zero coverage, which contradicts the guarantee the check exists to provide.
- **R-4 (missing artifact, FR-020)**: if the checked-in artifact is absent or unparseable, the check
  fails with a message that says so specifically — never a diff against empty, and never a silent pass.
- **R-5 (determinism, FR-007)**: the schema export MUST use `json.dumps(doc, indent=2, sort_keys=True)`
  and a single trailing newline. Verified by SC-005 (three consecutive runs, byte-identical).
- **R-6 (generated-file header)**: every generated artifact begins with a header naming the
  regeneration command and stating that the file is not hand-edited.

---

## 3. Failure-message requirements

Adopting M-1..M-6 from the docs-structure contract, adapted:

- **M-1**: name the artifact that drifted, by repo-relative path.
- **M-2**: group findings by kind with a count per group.
- **M-3**: one item per line; for the schema check, name the affected OpenAPI path or component
  schema, not just "the file differs".
- **M-4**: close with a `To fix:` block naming the **exact** regeneration command.
- **M-5**: link the contributor how-to (`docs/maintaining-generated-types.md`).
- **M-6** *(new, specific to this feature)*: state the dependency-bump case explicitly. A FastAPI,
  Pydantic, or `openapi-typescript` upgrade can redden these checks with no first-party source
  change; without this line a contributor hunts for a change that does not exist.

### Message skeleton

```text
frontend/src/api/generated/openapi.json is out of sync with the FastAPI application.

  Paths present in the app but not in the checked-in schema (2):
    - /api/v1/pairing/mint  (post)
    - /api/v1/ready         (get)

  Component schemas that differ (1):
    - InstanceOut

To fix: regenerate and commit the artifact:

    uv run python scripts/export_openapi.py

If you did not change the API, a FastAPI/Pydantic upgrade can also cause this —
regenerating and committing is still the correct fix.

See docs/maintaining-generated-types.md.
```

---

## 4. Test matrix

Mirrors the docs-structure gate's split between one real-repository test and hermetic synthetic tests.

| ID | Scenario | Expected |
|---|---|---|
| **T-1** | Real repo, post-implementation | All three checks pass, zero findings |
| **T-2** | Synthetic: app has a path the artifact lacks | Fail; message names the path and method |
| **T-3** | Synthetic: a component schema's properties differ | Fail; message names the component |
| **T-4** | Synthetic: artifact file missing | Fail with the R-4 "missing artifact" message, not a diff |
| **T-5** | Synthetic: artifact unparseable | Fail with the R-4 message naming the parse error |
| **T-6** | Export run 3× on unchanged sources | Byte-identical every time (SC-005) |
| **T-7** | Check run against a drifted repo | No tracked file modified afterward (R-1) |
| **T-8** | `KnownProviderType` vs. built-in descriptors | Equal; a first-party provider addition fails with instructions |
| **T-9** | Third-party provider registered, then export | Artifact byte-identical (SC-011) |
| **T-10** | Frame model set vs. checked-in frame artifact | Drift fails naming the frame (FR-022) |

---

## 5. Non-goals

- The artifacts are **not** an externally supported API contract (FR-029). No deprecation policy, no
  compatibility guarantee to third-party consumers.
- The checks do not validate that the *console uses* every generated type — only that generation is
  current. Unused-type detection is the type checker's job, not this gate's.
- The `remo-host` protocol (`core/remo_host_client.py`) is a CLI↔host contract and is out of scope.
