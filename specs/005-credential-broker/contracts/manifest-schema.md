# Contract: Project Manifest Schema (Remo-side consumer view)

Date: 2026-05-25
Branch: 005-credential-broker

**Authoritative source**: `get2knowio/remo-broker:docs/manifest-schema.md`.
**This document**: Remo's consumer view of the schema, what Remo synthesizes / validates / surfaces. Source-of-truth conflicts are resolved in favor of the broker repo.

## Schema version

Initial: `schema_version = 1`. Remo's `core/manifest.py` declares `SUPPORTED_SCHEMA_VERSIONS = {1}`. Future bumps require an explicit Remo release that widens the set.

## Discovery (FR-012)

```
1. <project>/.devcontainer/remo-broker.toml   (committed, repo-shared)
2. <project>/.remo/broker.toml                (auto-synthesized, gitignored)
```

First found wins. Missing both → Remo synthesizes (2) with the default (FR-013).

## TOML shape

```toml
schema_version = 1

[mcp]
secrets = ["github_token", "npm_token"]
notes   = "Frontend project; needs gh + npm publish."
```

### Field reference

| Path | Type | Required | Semantics |
|---|---|---|---|
| `schema_version` | integer | yes | Must be in `SUPPORTED_SCHEMA_VERSIONS`. Unknown values rejected with line number. |
| `mcp.secrets` | array of strings | yes | Backend-resolvable secret names. Per-secret name match: `^[a-z][a-z0-9_]{0,63}$`. Names duplicated within the array are treated as one. |
| `mcp.notes` | string | no | Free-form; surfaced verbatim in `remo audit` group headers. |

## Synthesized default (FR-013)

When Remo synthesizes `.remo/broker.toml`:

```toml
# This file was synthesized by `remo shell` because no broker manifest was found
# in this project. It declares which backend secrets the broker may serve to this
# project's devcontainer. Edit freely. Committed `.devcontainer/remo-broker.toml`
# takes precedence over this file.
schema_version = 1

[mcp]
secrets = ["github_token"]
```

Synthesis also ensures `.remo/` is in `.gitignore` (appends one line if missing).

## Validation

Two-stage (research R6):

1. **Laptop (`core/manifest.py`)** — Parse with `tomllib`. Validate against cached `manifest-schema-v1.json` using `jsonschema`. On error, print TOML position + JSON-Schema error path.
2. **Broker (instance side)** — Re-validates on every devcontainer start. Refuses unknown `schema_version`; logs a structured `manifest-invalid` audit line; project socket is not created.

## Mutability rules

- Adding a secret to the manifest, restarting the devcontainer → the secret becomes available (US4 AS#3 / SC-006).
- Removing a secret takes effect on the next devcontainer restart; an active devcontainer's already-cached values are *not* invalidated mid-session (broker holds the in-memory cache until TTL per NFR-004).
- The TOML file may be edited inside the devcontainer (visible via the project workspace mount) — but the broker reads the host-side file via the absolute project path, not via the devcontainer mount, so the developer must restart the devcontainer for changes to take effect.
