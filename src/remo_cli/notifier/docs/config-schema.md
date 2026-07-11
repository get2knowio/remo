# Notifier Configuration Schema

The notifier reads a single TOML file (`--config`, default
`/etc/notifier/notifier.toml`). Validation is **strict**: unknown keys are
rejected with a clear error. The bot token is **never** in this file — it is
read from a separate secret file at startup and kept in memory.

```toml
[server]
listen_host = "0.0.0.0"        # default "0.0.0.0"
listen_port = 18181            # default 18181 (1–65535)
log_level   = "info"           # debug | info | warning | error

[approval]
default_timeout_seconds = 300  # applied when a request omits timeout_seconds
max_timeout_seconds     = 1800 # requests are clamped to this; must be >= default
max_pending_approvals   = 50   # concurrent in-flight cap; over this -> 503

[transport]
type = "telegram"              # only "telegram" is supported in v1

[transport.telegram]
bot_token_file     = "/run/secrets/telegram_bot_token"  # read at startup, kept in memory
authorized_chat_id = 123456789                          # int; only this chat may decide
message_parse_mode = "MarkdownV2"

[instance]
id = "hetzner-prod-1"          # shown to the human in the approval message
```

## Fields

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `server.listen_host` | string | `0.0.0.0` | Bind inside the container. |
| `server.listen_port` | int | `18181` | |
| `server.log_level` | enum | `info` | `debug` also logs secrets/bodies; `info`+ never does. |
| `approval.default_timeout_seconds` | int ≥ 1 | `300` | |
| `approval.max_timeout_seconds` | int ≥ 1 | `1800` | Must be ≥ default. |
| `approval.max_pending_approvals` | int ≥ 1 | `50` | Backpressure / flood protection. |
| `transport.type` | enum | `telegram` | |
| `transport.telegram.bot_token_file` | path | `/run/secrets/telegram_bot_token` | Read once at startup; empty/missing → fail fast. |
| `transport.telegram.authorized_chat_id` | int | — | Required. |
| `transport.telegram.message_parse_mode` | string | `MarkdownV2` | |
| `instance.id` | string | — | Required. |

## Standing grants — `[grants]` (Addendum 001)

```toml
[grants]
enabled = true                  # master switch for "Always" auto-approval
default_ttl_seconds = 28800     # 8h; applied to every grant (no indefinite grants)
max_grants = 100                # cap on concurrent active grants
allow_global_scope = true       # if false, "everywhere"-scoped grants are refused
digest_interval_seconds = 3600  # periodic auto-approval digest; 0 disables it
```

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | `true` | When false, every request is prompted as before. |
| `default_ttl_seconds` | int ≥ 1 | `28800` | Every grant expires; revocation is always available. |
| `max_grants` | int ≥ 1 | `100` | Over the cap, "Always" approves once but doesn't remember. |
| `allow_global_scope` | bool | `true` | Grants default to the narrowest scope regardless. |
| `digest_interval_seconds` | int ≥ 0 | `3600` | `0` disables the Telegram digest. |

Grants are in-memory (cleared on restart → fail-closed re-prompt) and managed
from Telegram (`/rules`, `/revoke <id>`, `/pause`, `/resume`).

## Secret handling & rotation

- The bot token lives only in `bot_token_file` (mode `0400`), never in this TOML
  and never in logs.
- To rotate: rewrite the secret file and restart (`remo notifier restart`). The
  process also re-reads the file on `SIGHUP` (best-effort; applied on the
  transport's next start).
