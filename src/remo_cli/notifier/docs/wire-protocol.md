# Notifier Wire Protocol (v1)

The durable contract between agentsh (or any future approval emitter) and the
notifier. The machine-readable schema is
[`contracts/openapi.yaml`](../../../../specs/007-notifier-sidecar/contracts/openapi.yaml);
this document is the human reference.

The notifier listens on `0.0.0.0:18181` inside its container; the host binds it
to the Docker bridge (e.g. `172.17.0.1:18181`), reachable only by co-located
devcontainers. No TLS, no caller authentication in v1.

## `POST /v1/approve`

Submit an approval request. The connection is held open until a decision
exists: a human tap, the timeout, or service shutdown. Exactly one response is
returned.

### Request — `ApprovalRequest`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `approval_id` | string (UUID) | no | Server generates if absent. Duplicate-of-pending → `409`. |
| `session_id` | string | no | Opaque agentsh session id. |
| `operation` | object | yes | See below. |
| `operation.kind` | `command`\|`file`\|`network`\|`signal` | yes | |
| `operation.command` | string | no | |
| `operation.args` | string[] | no | |
| `operation.path` | string | no | file ops |
| `operation.remote_host` | string | no | network ops |
| `operation.remote_port` | int (1–65535) | no | network ops |
| `operation.context` | `direct`\|`nested` | no | default `direct` |
| `operation.depth` | int ≥ 0 | no | default 0 |
| `policy_rule_name` | string | yes | |
| `policy_message` | string | yes | Human-readable prompt. |
| `workspace` | string | no | DEBUG-log only. |
| `instance_id` | string | no | Falls back to the configured instance id. |
| `project` | string | no | |
| `timeout_seconds` | int ≥ 1 | no | Clamped to `[1, max_timeout_seconds]`; defaults to `default_timeout_seconds`. |
| `submitted_at` | string (RFC3339) | no | Informational. |

Unknown fields are rejected (`400`).

### Response — `ApprovalResponse`

```json
{
  "approval_id": "…",
  "decision": "allow | deny",
  "responder": "telegram:paulofallon",
  "reason": "",
  "decided_at": "2026-05-31T14:23:00Z",
  "latency_ms": 1234
}
```

### Status codes

| Code | Meaning | Body |
|------|---------|------|
| `200` | An authorized human approved or denied. | `ApprovalResponse` |
| `400` | Schema validation failure (unknown field, bad type, bad UUID). | `ErrorResponse{error:"validation_error"}` |
| `408` | Timeout — no human responded. **Fail-secure deny.** | `ApprovalResponse{decision:"deny", reason:"timeout"}` |
| `409` | `approval_id` already pending; original left running, no second notification. | `ErrorResponse{error:"duplicate_approval_id"}` |
| `503` | Shutting down, transport unreachable, no human-side config, **at capacity**, or this request's notification failed to send (no slot held). | `ErrorResponse{error:"unavailable"}` |

## Timeout contract (fail-secure)

A request that is not answered within its effective timeout returns `408` with a
`deny` decision and `reason: "timeout"`. **No outcome other than an explicit
authorized human Approve ever yields `allow`** — timeout, validation failure,
transport failure, capacity exhaustion, shutdown, and a dropped connection all
resolve to deny (or to no response, which the caller treats as deny). agentsh
applies its own `approval_timeout_action` on top of this.

## Cancellation semantics

`cancel(approval_id)` exists on the transport interface for approvals resolved
by means other than this transport (e.g. another channel, or shutdown). It edits
the human-facing message to reflect the final non-human outcome and resolves the
caller. In v1 it is used internally on timeout (message shows "Timed out") and
on shutdown ("Cancelled"); there is no external cancel endpoint.

## `GET /v1/health`

No auth. Returns `200`:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "transport": "telegram",
  "uptime_seconds": 1234,
  "pending_approvals": 0
}
```

`version` is the notifier component version (matches the container image tag),
not the `remo-cli` package version.

## State & restart

All approval state is in-memory. A restart loses every pending approval; the
caller's dropped connection is treated as a fail-secure deny. There is no
persistence and no replay.

## Standing grants — "Always" auto-approval (Addendum 001)

Before anything else, `POST /v1/approve` checks the in-memory standing-grant
store. If the request matches an **active, unexpired, unrevoked, human-created**
grant (created earlier via the Telegram "Always" flow), the notifier returns
`200` immediately:

- `decision: "allow"`, `responder: "rule:{grant_id}"`,
  `reason: "auto-approved via standing grant"`, and the `grant_id` field set.
- No notification is sent and no pending slot is reserved.

On no match (or when grants are disabled/paused), the normal pipeline runs
unchanged. The match is deterministic and **fail-closed**: an expired/revoked
grant, a scope/predicate mismatch, a missing field, or any evaluation error all
mean "no match" → the human is prompted. Grants are in-memory only (a restart
clears them → re-prompts) and TTL-bounded (default 8h; no indefinite grants).
The `grant_id` field also appears on the response that *creates* a grant (the
"Always" tap). Full grant schema + Telegram surface: `contracts/grant-schema.md`.
