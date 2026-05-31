# Contract: Sidecar-to-Broker Admin Protocol Dependency

**Date**: 2026-05-31

This repository depends on the sibling `/workspaces/remo-broker` repository for the daemon-side implementation. This document captures the specific v2 contract that `remo` relies on.

## Socket and protocol baseline

| Item | Contract |
|---|---|
| Socket path | `/run/remo-broker/admin.sock` |
| Transport | Unix `SOCK_STREAM` with NDJSON framing |
| Protocol version | `2` |
| Access model | Sidecar can reach the socket via bind mount and group permissions |

## Required admin operations

### `push-creds`

Request:

```json
{
  "op": "push-creds",
  "secrets": {
    "gh": "ghp_xxx",
    "aws": {
      "aws_access_key_id": "AKIA...",
      "aws_secret_access_key": "..."
    }
  }
}
```

Response:

```json
{
  "ok": true,
  "loaded_at": "2026-05-31T02:30:00Z",
  "secret_count": 2
}
```

**Contract**
- A successful push atomically replaces the full in-memory secret snapshot.
- Omitted secrets are removed as part of the same atomic swap.
- A successful push immediately invalidates all per-project broker cache entries.
- Payload limit must support up to 1 MiB for realistic bundled credential sets.
- Success emits `AuditEvent::SecretsPushed { timestamp, secret_count }` without logging values.

### `clear-creds`

Request:

```json
{ "op": "clear-creds" }
```

Response:

```json
{ "ok": true }
```

**Contract**
- Replaces broker memory with an empty snapshot.
- Emits `AuditEvent::SecretsCleared { timestamp }`.

### `reload`

Request:

```json
{ "op": "reload", "name": "project-a" }
```

Response:

```json
{ "ok": true, "allowlist": ["gh", "openai_api_key", "aws"] }
```

**Contract**
- Re-reads and revalidates the host-side project manifest.
- Swaps allowlist state atomically without tearing down the project socket.
- Surfaces manifest errors synchronously to `remo-reload`.

### `status`

Request:

```json
{ "op": "status" }
```

Response excerpt:

```json
{
  "ok": true,
  "protocol_version": 2,
  "secret_count": 2,
  "secrets_loaded_at": "2026-05-31T02:30:00Z"
}
```

**Contract**
- `secret_count` and `secrets_loaded_at` are present in v2 status responses.
- `bootstrap_mode` is gone in v2.
- `_remo-vault` helper commands depend on this response shape for status reporting.

## Project-socket dependency

The project devcontainer startup helper depends on these existing data-plane operations remaining stable:

| Operation | Required fields |
|---|---|
| `get` | `ok`, `value` or `value_b64`, optional `ttl_seconds` |
| `ping` | `ok`, `broker_version`, `protocol_version`, `project` |
| `info` | `ok`, `project`, `allowlist`, `schema_version` |

**Important compatibility note**
- `Outcome::BackendError` / `Outcome::BackendUnreachable` may remain in the broker enum for wire compatibility, but this feature assumes the normal happy path is now purely in-memory with no external backend dependency.
- Project startup preflights `ping` before requesting secret data and treats a protocol mismatch as a hard failure.
- `_remo-vault` helper flows preflight `status` before `push-creds` so the sidecar also fails closed on protocol skew.

## Failure handling expectations

- Invalid `push-creds` payloads fail with explicit protocol errors.
- Missing required secrets during project startup are handled by the project-side retry loop; they are not hidden by the broker.
- `remo` should treat protocol-version mismatch as a hard compatibility error during provisioning or helper execution.
