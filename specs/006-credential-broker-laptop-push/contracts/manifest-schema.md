# Contract: `.remo/manifest.toml`

**Date**: 2026-05-31

This file is the canonical per-project contract shared by the project devcontainer startup flow and the sibling `remo-broker` repository.

## Canonical location

```text
~/projects/<project-name>/.remo/manifest.toml
```

**Contract**
- The host-side file is the source of truth.
- The project devcontainer receives this file as a separate read-only bind mount.
- Updates happen from the host shell or `_remo-vault`, followed by `remo-reload <project>`.
- Project startup injects this mount through a generated `devcontainer --config` file instead of mutating the repo's checked-in devcontainer config.

## Schema

```toml
schema_version = 1
project        = "project-a"

[secrets.gh]
fetch_as = "env"
env_var  = "GH_TOKEN"

[secrets.openai_api_key]
fetch_as = "env"

[secrets.aws]
fetch_as  = "file"
file_path = "~/.aws/credentials"
file_mode = "0600"
template  = """
[default]
aws_access_key_id={{aws_access_key_id}}
aws_secret_access_key={{aws_secret_access_key}}
"""

[cache]
default_ttl_seconds = 900
default_max_entries = 50
```

## Field rules

### Top-level fields

| Field | Required | Rule |
|---|---|---|
| `schema_version` | yes | Must be `1` for the initial sidecar release |
| `project` | yes | Must match the parent directory basename |
| `[secrets.*]` | yes | At least one secret binding for meaningful use |
| `[cache]` | no | Optional broker cache caps, carried through to broker validation |

### Secret binding rules

| Field | Applies to | Rule |
|---|---|---|
| `fetch_as` | all secrets | `"env"` by default; `"file"` opt-in |
| `env_var` | `env` mode | Optional; defaults to uppercased secret name |
| `file_path` | `file` mode | Required |
| `file_mode` | `file` mode | Required four-digit octal string, typically `"0600"` |
| `template` | `file` mode | Required; placeholders resolve against the secret payload |

## Structured credential bundle contract

`file` mode may target a structured credential bundle instead of a scalar secret.

**Example**

```toml
[secrets.aws]
fetch_as  = "file"
file_path = "~/.aws/credentials"
file_mode = "0600"
template  = """
[default]
aws_access_key_id={{aws_access_key_id}}
aws_secret_access_key={{aws_secret_access_key}}
"""
```

**Rules**
- Placeholder names must match bundle field names exactly.
- Missing placeholders are validation errors, not silent empty substitutions.
- The broker enforces the allowlist by top-level secret name only; file rendering happens in the project-side helper.

## Security contract

- The manifest is readable but not writable from inside the project devcontainer.
- Editing the manifest is a privileged workflow step relative to the project container blast radius.
- The manifest controls which secrets can be fetched; it does not store secret values.

## Cross-repo alignment

- `remo-broker` remains the validator for project allowlist semantics and cache caps.
- This repository extends the same manifest with fetch/render directives needed by `remo-fetch-secrets`.
- The final implementation must keep the published broker schema and this document in sync; a dual-manifest design is explicitly out of scope.
