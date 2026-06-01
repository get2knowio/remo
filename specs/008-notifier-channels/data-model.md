# Phase 1 Data Model: Notifier Channels

Entities introduced or changed by this feature. The approval-domain entities (ApprovalRequest/Response/Decision, PendingApproval, Grant) are **unchanged** from spec 007 and are not restated here.

## ChannelDescriptor (new)

The catalog entry for one channel. Import-light dataclass in `channels/base.py`; instances live in `channels/catalog.py`. Read by the laptop CLI (selection, preflight, `channels` listing) and, in-container, used to build the transport.

| Field | Type | Notes |
|-------|------|-------|
| `id` | str | Stable channel identifier; matches `[transport].type` and the image suffix. Lowercase, e.g. `telegram`. Unique across the catalog. |
| `label` | str | Human-facing name shown in the picker and `channels` list (e.g. "Telegram"). |
| `image_name` | str | Image repository name, e.g. `remo-notifier-telegram`. Tagged at deploy with the notifier version → `remo-notifier-telegram:<version>`. |
| `required_env` | list[RequiredEnv] | The environment variables the operator must set for this channel; checked by the deploy preflight (FR-012). |
| `transport_factory` | str | Dotted import path to the transport builder (e.g. `remo_cli.notifier.channels.telegram.transport:build`). Imported lazily **only in the container** (keeps the laptop CLI free of channel deps). |
| `render_transport_toml` | callable | `(values: dict[str, str]) -> str` → the `[transport]` + `[transport.<id>]` TOML fragment for `notifier.toml`. Owns all channel-specific TOML so the Ansible role stays generic. |

**Validation rules**:
- `id` MUST be unique in the catalog and a valid TOML key / image-name segment.
- `required_env` MUST be non-empty (a channel with no credentials is allowed only if it explicitly declares an empty list; the catalog flags a malformed entry otherwise — edge case "catalog entry malformed").
- Exactly one `RequiredEnv` per channel MAY be marked `secret=True` and is the token written to the secret file; others render into the TOML fragment.

## RequiredEnv (new)

One credential/config input a channel needs.

| Field | Type | Notes |
|-------|------|-------|
| `name` | str | Environment variable name; MUST follow `REMO_NOTIFIER_<CHANNEL>_<NAME>` (FR-012a). E.g. `REMO_NOTIFIER_TELEGRAM_BOT_TOKEN`. |
| `secret` | bool | If true, value is written to the on-host secret file (mode 0400) and never placed in `notifier.toml` or logs; if false, it renders into the transport TOML fragment. |
| `purpose` | str | Short human description for the `channels` listing and preflight error messages. |

## ChannelCatalog (new)

The set of available channels. Realized as `CHANNELS: list[ChannelDescriptor]` plus helpers:
- `list_channels() -> list[ChannelDescriptor]`
- `get(channel_id) -> ChannelDescriptor | None` (None → CLI emits the "unknown channel, available: …" error, FR-010)

Fixed at build time (not runtime-extensible, FR-006). Adding a channel appends one entry.

## TransportConfig (changed — core `config.py`)

Generalized from the 007 telegram-specific union to a type-dispatched container.

| Field | Type | Notes |
|-------|------|-------|
| `type` | str | The active channel id. Replaces the 007 hardcoded `"telegram"`-only validator. |
| `<type>` sub-table | raw mapping | Per-channel settings, validated by that channel's own Pydantic model (strict, `extra="forbid"`). Core does not import the channel model; it passes the mapping to the channel for validation. |

**Migration/compat**: Telegram's TOML remains `[transport]\ntype = "telegram"` + `[transport.telegram]` with the same three keys — the rendered `notifier.toml` is byte-identical to 007 (FR-017/FR-018). The Telegram Pydantic model (`bot_token_file`, `authorized_chat_id`, `message_parse_mode`, `read_token()`) moves verbatim to `channels/telegram/config.py`.

## TelegramChannelConfig (moved, not changed)

The 007 `TelegramConfig` model, relocated to `channels/telegram/config.py`. Fields and `read_token()` behavior unchanged. Core no longer references it.

## InstalledChannel (host runtime state — conceptual)

Not persisted (FR-009). The currently installed channel on a host is observable as:
- the running image tag `remo-notifier-<channel>:<version>` under the single `remo-notifier.service`, and
- the `transport` field of `GET /v1/health` (the active channel id, R6).

A new install replaces it (one channel per host, FR-013); a switch is a restart that clears in-flight approvals and in-memory grants (FR-015).

## agentsh Approval (`Request`) — external, consumed not defined

The unit of work, owned by agentsh and fetched via `GET /api/v1/approvals`. The notifier does not define this shape; it adopts it (contracts/agentsh-integration.md). Replaces 007's invented `/v1/approve` request body in `models.py`.

| Field | Type | Notes |
|-------|------|-------|
| `id` | str | The resolvable approval id (used in `POST /api/v1/approvals/{id}`). |
| `created_at` / `expires_at` | datetime | agentsh owns the lifetime; the notifier resolves before `expires_at`. |
| `session_id` | str | Correlates a webhook trigger event back to a pending approval. |
| `command_id` | str (opt) | Secondary correlation key. |
| `kind` | str | e.g. `file_delete`, `command`, `network` — rendered to the human. |
| `target` | str (opt) | The operation target (path/host/etc.). |
| `rule` | str (opt) | Triggering policy rule. |
| `message` | str (opt) | Human-readable policy message. |
| `fields` | map | Freeform extra detail. |

## Decision (resolution) — agentsh vocabulary

`POST /api/v1/approvals/{id}` body: `{"decision": "approve"|"deny", "reason": str}`. The notifier's internal allow/deny maps to agentsh's `approve`/`deny`:

| Human action | Internal | agentsh wire |
|---|---|---|
| Approve | allow | `approve` |
| Deny / timeout / error / shutdown | deny | `deny` |

The 007 internal `ApprovalDecision`/`ApprovalResponse` (responder, latency_ms, grant_id) remain as internal/observability records; only the agentsh-facing resolve body is `{decision, reason}`.

## AgentshConfig (new — core `config.py`, `[agentsh]` section)

| Field | Type | Notes |
|-------|------|-------|
| `api_url` | str | Base URL of agentsh's approval API (the host-bridge address). |
| `api_key_file` | str | Path to the approver `X-API-Key` secret (file-based, like the Telegram token; mode 0400). |
| `poll_interval_seconds` | int | How often to poll `GET /api/v1/approvals`. Default `5`; lower bound `1`. |
| `webhook_enabled` | bool | Whether to expose a local "poll now" trigger endpoint (default off). |

The approver key is a **secret** delivered via the same on-host secret-file mechanism as channel secrets, named per the `REMO_NOTIFIER_AGENTSH_API_KEY` convention.

## Relationships

```text
ChannelCatalog 1───* ChannelDescriptor 1───* RequiredEnv
                              │
                              │ id ==
                              ▼
                        TransportConfig.type ──selects──► channel Pydantic config (validates [transport.<id>])
                              │
                              │ transport_factory (lazy, in-container only)
                              ▼
                     NotificationTransport (ABC, core contract) ── delivers ──► human

agentsh /api/v1/approvals ──poll──► agentsh_client ──► [Request] ──► core ──► NotificationTransport ──► human
                       ▲                                                                                   │
                       └──────────── POST {id} {decision:approve|deny} ◀── core ◀── human taps ◀───────────┘
```
