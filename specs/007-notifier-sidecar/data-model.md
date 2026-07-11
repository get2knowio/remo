# Phase 1 Data Model: Notifier Sidecar

All models are Pydantic v2 (`model_config = ConfigDict(extra="forbid")` for strictness where noted). Times are RFC3339 UTC strings on the wire; internally `datetime` (aware, UTC). No model is persisted — these describe in-flight HTTP payloads, configuration, and the in-memory registry.

## Wire models (`notifier/models.py`)

### Operation

Describes the operation agentsh is asking to approve. All fields optional except `kind`; presence depends on `kind`.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `kind` | enum: `command` \| `file` \| `network` \| `signal` | yes | drives which other fields are meaningful |
| `command` | str | no | e.g. `rm`, `curl` (command/signal ops) |
| `args` | list[str] | no | default `[]` |
| `path` | str | no | file ops |
| `remote_host` | str | no | network ops |
| `remote_port` | int | no | network ops; 1–65535 if present |
| `context` | enum: `direct` \| `nested` | no | default `direct` |
| `depth` | int | no | ≥0; default 0 |

Validation: `extra="forbid"`. No hard cross-field requirement (agentsh owns semantics); notifier renders whatever is present.

### ApprovalRequest (POST /v1/approve body)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `approval_id` | str (UUID) | no | server generates if absent (FR-003); if present and already pending → 409 (FR-003a) |
| `session_id` | str | no | opaque to notifier |
| `operation` | Operation | yes | |
| `policy_rule_name` | str | yes | |
| `policy_message` | str | yes | human-readable prompt |
| `workspace` | str | no | filesystem path; DEBUG-log only (FR-017) |
| `instance_id` | str | no | falls back to configured instance id if absent |
| `project` | str | no | devcontainer/project name |
| `timeout_seconds` | int | no | clamped to `[1, max_timeout_seconds]`; defaults to `default_timeout_seconds` (FR-006) |
| `submitted_at` | str (RFC3339) | no | informational |

Validation: `extra="forbid"` → unknown fields produce 400 (FR-001). `approval_id`, if supplied, must be a valid UUID string.

### ApprovalDecision (internal resolution value)

The value a `PendingApproval`'s Future is resolved with.

| Field | Type | Notes |
|-------|------|-------|
| `decision` | enum: `allow` \| `deny` | `allow` only from authorized human Approve (FR-008) |
| `responder` | str | e.g. `telegram:paulofallon`, or `system:timeout` |
| `reason` | str | may be empty; `"timeout"` on timeout |
| `decided_at` | datetime (UTC) | |

### ApprovalResponse (POST /v1/approve response body)

| Field | Type | Notes |
|-------|------|-------|
| `approval_id` | str | echoes the (possibly generated) id (FR-003) |
| `decision` | enum: `allow` \| `deny` | |
| `responder` | str | |
| `reason` | str | |
| `decided_at` | str (RFC3339) | |
| `latency_ms` | int | wall time from intake to resolution |

### HealthResponse (GET /v1/health body)

| Field | Type | Notes |
|-------|------|-------|
| `status` | str | `"ok"` |
| `version` | str | the **notifier component** version (e.g. `0.1.0`), sourced from a module constant `remo_cli.notifier.__version__` and matching the image tag `remo_notifier_version` — not the `remo-cli` package version (I2) |
| `transport` | str | active transport name, e.g. `"telegram"` |
| `uptime_seconds` | int | since process start |
| `pending_approvals` | int | current registry size |

### ErrorResponse (4xx/503 bodies, where not the 408 deny shape)

| Field | Type | Notes |
|-------|------|-------|
| `error` | str | machine code, e.g. `duplicate_approval_id`, `validation_error`, `unavailable` |
| `detail` | str | human-readable; never contains secrets |
| `approval_id` | str | present on 409 |

408 body is the special fail-secure shape: `{ "approval_id": "...", "decision": "deny", "reason": "timeout", ... }` (a full `ApprovalResponse`), per FR-005.

## Configuration models (`notifier/config.py`)

Loaded from TOML (`--config`, default `/etc/notifier/notifier.toml`). Every model is `extra="forbid"` (FR-018 strict mode → unknown keys raise a clear error).

```text
NotifierConfig
├── server: ServerConfig
│   ├── listen_host: str = "0.0.0.0"
│   ├── listen_port: int = 18181          # 1–65535
│   └── log_level: enum(debug|info|warning|error) = "info"
├── approval: ApprovalConfig
│   ├── default_timeout_seconds: int = 300         # ≥1
│   ├── max_timeout_seconds: int = 1800            # ≥ default
│   └── max_pending_approvals: int = 50            # ≥1 (FR-034)
├── transport: TransportConfig
│   ├── type: enum(telegram) = "telegram"          # only telegram in v1
│   └── telegram: TelegramConfig
│       ├── bot_token_file: str = "/run/secrets/telegram_bot_token"
│       ├── authorized_chat_id: int                # required
│       └── message_parse_mode: str = "MarkdownV2"
└── instance: InstanceConfig
    └── id: str                                    # shown to humans (FR-020)
```

Validation rules:
- `max_timeout_seconds >= default_timeout_seconds` (model validator).
- `transport.type == "telegram"` requires `transport.telegram` present and `authorized_chat_id` set.
- `bot_token_file` must exist and be readable at startup; the token is read then (never stored in config) (FR-019). Empty/missing token → fail fast with a clear message (mirrors role pre-flight, FR-023/Constitution IV).

## In-memory registry (`notifier/state.py`)

### PendingApproval

| Field | Type | Notes |
|-------|------|-------|
| `approval_id` | str | key |
| `request` | ApprovalRequest | the originating request (effective timeout already computed) |
| `future` | `asyncio.Future[ApprovalDecision]` | resolved exactly once |
| `created_at` | datetime (UTC) | for latency + uptime accounting |

### PendingApprovals (registry)

State: `dict[str, PendingApproval]` + `asyncio.Lock` + `max_pending` cap.

Operations (all cap/dup checks under the lock):
- `try_register(request) -> PendingApproval | RegisterError` — returns `DUPLICATE` if id pending (→409), `AT_CAPACITY` if `len == max_pending` (→503), else inserts and returns the entry. **Caller registers only after a successful notification send** (R2/FR-010a) — so the public flow is: capacity/dup *pre-check* under lock to reserve, send, then finalize; on send failure, release the reservation. Implementation reserves the slot+id atomically, then rolls back on send failure to honor both FR-034 and FR-010a.
- `resolve(approval_id, decision)` — if still pending, `future.set_result(decision)` and remove; else no-op (late/duplicate callback edge case).
- `cancel(approval_id, decision)` — transport-driven external resolution; same as resolve plus message edit responsibility lies with the transport.
- `count()` — current size (for health).
- `drain(decision)` — on shutdown, resolve all pending with a non-allow outcome / release callers (edge case: shutdown).

### State transitions

```text
            try_register (slot reserved, id locked)
   (none) ─────────────────────────────────────────▶ RESERVED
                                                         │
                       send notification                │
              ┌──────────────────────────────┬──────────┘
        send fails                       send ok
              │                               │
              ▼                               ▼
   release slot → 503 (FR-010a)            PENDING ──────────────┐
                                              │                  │
                 human Approve/Deny ──────────┤                  │ timeout
                                              │                  ▼
                                              │              RESOLVED(deny,"timeout") → 408
                 cancel(external) ────────────┤
                                              │                  shutdown
                                              ▼                  ▼
                                       RESOLVED(decision) → 200   drained → conn drop/503
```

Invariants:
- A Future is resolved exactly once; all post-resolution callbacks are no-ops.
- `allow` enters the system only via the authorized-human Approve edge (FR-008, SC-005).
- `len(registry) <= max_pending` always (FR-034).
- At most one PENDING entry per `approval_id` (FR-003a).

## Relationships

- `ApprovalRequest` 1—1 `PendingApproval` (while in flight) 1—1 `ApprovalResponse` (at resolution).
- `NotifierConfig.transport` selects exactly one `Transport` instance for the process lifetime.
- `Transport` delivers a message per `PendingApproval` and reports decisions back through the registry's `resolve`/`cancel`.
