# Contract: agentsh Approval Integration

**Source of truth**: agentsh's own approval REST API ([`canyonroad/agentsh`](https://github.com/canyonroad/agentsh)), verified against source on 2026-06-01 (`internal/approvals/manager.go`, `internal/api/app.go`, `pkg/types/events.go`, `config.yml`, `docs/approval-auth.md`). The notifier does **not** define its own approval schema; it consumes agentsh's. Pin to a specific agentsh version when integrating, as these are internal types that may evolve.

## Integration shape

agentsh **hosts** the approval API; the notifier is an **approver client** (plus an optional webhook receiver). agentsh never blocks on us.

```
agentsh (hosts /api/v1/approvals, holds pending + its own timeout)
   ▲ GET pending (poll; source of truth, carries the id)        │ POST {id} {decision}
   │ ◀┄┄ optional: notification webhook = "poll now" trigger ┄┄ │
   │                                                            ▼
notifier-core (approver client)  ── deliver ──►  channel (Telegram / Slack)  ──►  human
```

## Endpoints (agentsh-hosted)

| Method | Path | Purpose | Body |
|--------|------|---------|------|
| `GET`  | `/api/v1/approvals` | List pending approvals (the resolvable source of truth) | → `[]Request` |
| `POST` | `/api/v1/approvals/{id}` | Resolve one approval | `{"decision":"approve"\|"deny","reason":"..."}` → `{"ok":true}` / `{"error":"approval not found"}` |

- **Auth**: `X-API-Key` header; role-gated to `approver`/`admin`. The notifier uses an **approver** key (never an `agent` key). When agentsh auth is disabled the approvals API is disabled entirely (anti-self-approval) — the notifier requires `auth.type=api_key` on agentsh.
- **No watch/long-poll** endpoint exists: pulling pending approvals means **polling** `GET`.

## The pending-approval object (`Request`)

Verbatim from `internal/approvals/manager.go`:

```go
type Request struct {
    ID        string         `json:"id"`
    CreatedAt time.Time      `json:"created_at"`
    ExpiresAt time.Time      `json:"expires_at"`   // agentsh owns the timeout
    SessionID string         `json:"session_id"`
    CommandID string         `json:"command_id,omitempty"`
    Kind      string         `json:"kind"`         // e.g. "file_delete", "command", "network"
    Target    string         `json:"target,omitempty"`
    Rule      string         `json:"rule,omitempty"`
    Message   string         `json:"message,omitempty"`
    Fields    map[string]any `json:"fields,omitempty"`
}
```

The channel message is rendered from these fields (`kind`, `target`, `rule`, `message`, `session_id`, plus `fields`).

## Decision submission

`POST /api/v1/approvals/{id}` with `{"decision":"approve"|"deny","reason":"..."}`. agentsh's internal `Resolution` is `{approved bool, reason, at}`.

**Decision vocabulary mapping** (the notifier's core fail-secure logic is internal; the wire uses agentsh's words):

| Human action | Notifier internal | agentsh wire |
|---|---|---|
| Approve | allow | `"approve"` |
| Deny / timeout / error / shutdown | deny | `"deny"` |

The fail-secure invariant (FR-007/FR-008) is unchanged: only an authorized human Approve maps to `approve`; every other outcome resolves to `deny` (or is left for agentsh's own `ExpiresAt` timeout to deny).

## Optional notification webhook (latency optimization only)

agentsh config (`config.yml`):

```yaml
approvals:
  mode: "api"
  timeout: "5m"
  notification:
    type: "webhook"
    webhook:
      url: "https://<notifier-bridge-bind>/<path>"
      secret: ""        # present in config; NO HMAC signing implemented in current agentsh source
```

- The webhook POSTs `[]types.Event` (generic **audit events**, `application/json`), **unsigned** in current agentsh — see `internal/store/webhook/webhook.go`. The `Event` carries `type`, `session_id`, `command_id`, `policy.approval{required,mode}`, detail fields, and a freeform `fields` map — but **not** a directly resolvable approval `id`.
- Therefore the webhook is treated as a **"poll now" trigger only**: on receipt, the notifier fetches `GET /api/v1/approvals` to obtain the authoritative pending `Request`(s) and their `id`s, correlating by `session_id`/`command_id`. The notifier MUST function on polling alone if the webhook is absent or unverifiable.
- Because it is unsigned, the notifier MUST NOT trust webhook contents as authority — it only triggers a poll. (Re-evaluate if agentsh adds webhook signing.)

## Timeout ownership

agentsh owns the approval lifetime via `ExpiresAt` / its `approvals.timeout`. The notifier delivers and resolves before expiry; if no human responds, agentsh fail-secure-denies on its side. The notifier's own per-request timeout (007) becomes a delivery/resolve deadline aligned to `ExpiresAt`, not an independent contract.

## Open verification items (confirm against a live agentsh before GA)

- Exact webhook event `type` value(s) for "approval required" and whether `fields` ever carries the approval `id` (would allow skipping the correlating GET).
- Whether a future agentsh adds webhook HMAC signing (would let us trust the push directly).
- Pagination/filtering on `GET /api/v1/approvals` for busy sessions.
