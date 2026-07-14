# Phase 1 Data Model: Remo Web Session Interface

All entities are in-memory/ephemeral (NFR-006). No database, no server-side persistence. The Remo
registry (`KnownHost`) is read-only input and its schema is **unchanged**. Types below are the
conceptual contract; backend uses Python dataclasses/Pydantic models, the frontend mirrors them in
TypeScript.

## KnownHost *(existing — unchanged)*

Loaded from `~/.config/remo/known_hosts`. Fields: `type, name, host, user, instance_id, access_mode,
region`. See `src/remo_cli/models/host.py`. **No schema change** (spec constraint). Instance identity
for this feature = `(type, name)` (FR-002).

## RemoteCapability

Reported by `remo-host capabilities --json`.

| Field | Type | Notes |
|---|---|---|
| `protocol_version` | int | Major protocol version; compatible when within client `[min,max]` (=`[1,1]`). |
| `host_tools_version` | string | Value of `~/.remo-version` (may be empty). |
| `projects_root` | string | Absolute `dev_workspace_dir` (e.g. `/home/remo/projects`). |
| `operations` | string[] | Supported verbs, e.g. `["capabilities","sessions.list","sessions.attach"]`. |
| `zellij` | bool | Whether `zellij` is on PATH. |
| `docker` | bool | Whether `docker` is available (drives `devcontainer_running` determinability). |

**Validation**: `protocol_version` required int > 0; unknown extra fields ignored (additive-compatible);
payload ≤ size cap.

## SessionTarget

The `(instance, project)` pair openable in a terminal.

| Field | Type | Notes |
|---|---|---|
| `id` | string (opaque) | Stable, non-guessable public ID; encodes nothing executable (FR-002/FR-015). Derived server-side from `(type, name, project)`, e.g. HMAC/hash — never a command or path. |
| `instance_type` | string | Provider type (`aws`/`hetzner`/`incus`/`proxmox`). |
| `instance_name` | string | Remo name (display via `KnownHost.display_name`). |
| `project` | string | Project directory name. |
| `has_devcontainer` | bool | `.devcontainer` present (FR-058 — false ⇒ plain Zellij, not shown as devcontainer). |
| `zellij_state` | enum | `active` \| `exited` \| `absent`. |
| `devcontainer_running` | enum | `running` \| `stopped` \| `unknown` (unknown when docker unavailable). |
| `discovered_at` | timestamp | When this datum was produced (stamped by server after workflow). |

**Validation / rules**: `project` must match the discovered set at attach time (revalidated, FR-011/
FR-050); names may contain spaces/Unicode/punctuation but attach refuses absolute paths, `..`
traversal, control chars, or non-existent projects. `id` is authorization-bearing: the server maps
`id → (instance, project)` via current registry+cache; the client never supplies raw targets.

## InstanceStatus / DiscoverySnapshot

Per-instance discovery result; typed status, never an empty-success (FR-006).

| Field | Type | Notes |
|---|---|---|
| `instance_id` | string (opaque) | Public ID for the instance. |
| `instance_type` / `instance_name` | string | As above. |
| `status` | enum | `ok` \| `unreachable` \| `auth_failed` \| `no_remo_host` \| `incompatible_protocol` \| `malformed` \| `timeout`. |
| `capability` | RemoteCapability? | Present when `status = ok`. |
| `targets` | SessionTarget[] | Present when `status = ok`; may be empty (instance has no projects). |
| `error` | TypedError? | `{code, message, retryable, remediation}` for non-ok; e.g. `no_remo_host` → remediation names the Remo update action (FR-059). |
| `refreshed_at` | timestamp | Snapshot time; replaced on refresh, not authoritative for provider lifecycle. |

**State transitions**: snapshots are immutable and wholesale-replaced per instance on each refresh
cycle (manual or interval). A previously-`ok` instance can become `unreachable` without dropping other
instances' snapshots (host-failure isolation, US1 scenario 2).

## TerminalAttachment

Server-side ephemeral terminal.

| Field | Type | Notes |
|---|---|---|
| `terminal_id` | string (opaque) | Public ID. |
| `session_target_id` | string | Bound target; immutable after creation. |
| `state` | enum | `pending` \| `connecting` \| `ready` \| `disconnected` \| `closed` \| `error`. |
| `cols` / `rows` | int | Clamped to safe bounds (FR-060). |
| `token_expires_at` | timestamp | WS token deadline (default +30 s; FR-049). |
| `created_at` / `last_activity_at` | timestamp | For idle/limits/observability. |
| `client_id` | string | For per-client cap accounting (FR-022). |
| `exit` | {code:int, classification:enum}? | On terminate/error. |
| `error` | TypedError? | Classified: `auth` \| `network` \| `remote_capability` \| `missing_project` \| `remote_launch` (FR-023), surfaced only to this terminal. |

**Lifecycle**: `pending`(created, token issued) → `connecting`(WS upgraded, ssh+pty spawned) →
`ready`(first output) → `disconnected`(WS lost; process reaped, remote Zellij intact) → new attachment
on reconnect. `closed` reaps PTY/SSH (FR-019). Reconnect creates a **new** TerminalAttachment to the
same `session_target_id` (FR-020). Never reuses/retargets a token (FR-050).

## WsToken

| Field | Type | Notes |
|---|---|---|
| `value` | string (secret) | ≥128-bit opaque; never logged/URL'd (FR-028/FR-049). |
| `terminal_id` | string | Bound terminal. |
| `session_target_id` | string | Bound target (re-checked at upgrade, FR-050). |
| `expires_at` | timestamp | +TTL. |
| `consumed` | bool | Single-use; set atomically on successful WS upgrade. |

## SshMaster *(runtime only)*

| Field | Type | Notes |
|---|---|---|
| `key` | tuple | `(user, host, port, access_mode)` — effective SSH destination (FR-024). |
| `control_path` | string | Socket under `$REMO_SSH_CONTROL_DIR` (e.g. `/run/remo-ssh`). |
| `attachments` | int | Live children count (for teardown when zero + persist window). |
| `healthy` | bool | `ssh -O check`; unhealthy ⇒ per-child reconnect, siblings unaffected. |

No durable state; sockets in tmpfs.

## BrowserWorkspace *(client-only)*

`localStorage` only (FR-034): open terminal IDs + order, layout mode (`grid`\|`tabs`\|`focused`),
focused terminal, display prefs. Not persisted server-side; no schema guarantees across app versions.

## Relationships

```text
KnownHost (registry, RO)
   └─(discovery)→ DiscoverySnapshot[instance]  ── status + RemoteCapability
                        └── SessionTarget[]  (id ↔ (instance, project))
                                 └─(POST /terminals)→ TerminalAttachment ──1:1── WsToken
                                                          └── uses ── SshMaster[per instance]
BrowserWorkspace (client) → references TerminalAttachment.terminal_id (+ SessionTarget.id)
```
