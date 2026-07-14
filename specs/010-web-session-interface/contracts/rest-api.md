# Contract: Web Service REST API (`/api/v1`)

FastAPI app, served same-origin with the SPA. All state-changing requests and the WS handshake validate
`Host` (allowlist) and `Origin` (configured origins); no wildcard CORS (FR-048). Errors are typed and
never contain secrets, tokens, or full proxy commands (FR-028).

## `GET /api/v1/health` — liveness

`200 {"status":"alive"}` whenever the process is up. Independent of configuration validity (FR-045).

## `GET /api/v1/ready` — readiness

Distinguishes process health from operator-config validity (FR-045). Returns `200` only when registry
is readable, SSH identity is available, runtime dir is writable, and required executables exist.
```json
// 200
{"status":"ready","checks":{"registry":"ok","ssh_identity":"ok","runtime_dir":"ok","ssh":"ok","aws_cli":"ok","ssm_plugin":"ok"}}
// 503 (e.g. only registry mounted, no key — US4 scenario 2)
{"status":"not_ready","checks":{"registry":"ok","ssh_identity":"missing"},
 "detail":"Registry is readable but no SSH identity is mounted. The registry is metadata, not authentication material. Mount a private key read-only (see docs)."}
```

## `GET /api/v1/hosts` — instances + status

Returns the current `DiscoverySnapshot` per instance (from cache; typed status), grouped-friendly for
the dashboard (FR-029). Does not block on unreachable hosts.
```json
{"instances":[
  {"instance_id":"…","instance_type":"proxmox","instance_name":"pve/dev","status":"ok","region":"local",
   "capability":{"protocol_version":1,"host_tools_version":"2.1.0","projects_root":"/home/remo/projects"},
   "refreshed_at":"2026-07-13T12:00:00Z"},
  {"instance_id":"…","instance_type":"aws","instance_name":"box","status":"unreachable",
   "error":{"code":"unreachable","message":"connect timeout","retryable":true,"remediation":"Check instance is running / reachable."}},
  {"instance_id":"…","instance_type":"hetzner","instance_name":"web","status":"no_remo_host",
   "error":{"code":"no_remo_host","message":"remo-host not installed","retryable":false,
            "remediation":"Update this instance's Remo host tools (re-run configure)."}}
]}
```

## `GET /api/v1/sessions` — discovered targets

Flattened `SessionTarget[]` across `ok` instances (FR-029/FR-030 selection).
```json
{"targets":[
  {"id":"opaque…","instance_type":"proxmox","instance_name":"pve/dev","project":"my-api",
   "has_devcontainer":true,"zellij_state":"active","devcontainer_running":"running",
   "git_tracked":true,"git_dirty":true,"git_ahead":2,"git_behind":0,
   "discovered_at":"2026-07-13T12:00:00Z"}
]}
```
Instances running an older `remo-host` omit the `git_*` fields, which the server defaults to
`false`/`0`. `region` on `/hosts` defaults to `""` when the registry entry has no region.

## `POST /api/v1/discovery/refresh` — force re-discovery

Body optional: `{"instance_id":"…"}` to refresh one, else all. Re-reads the registry (hot reload,
FR-004) and re-runs concurrent discovery. `202 {"refreshing":true}`; results arrive via subsequent
`GET /hosts`·`/sessions` (incremental, FR-035).

## `POST /api/v1/terminals` — create a terminal

Accepts **only** an opaque target id + initial dims (FR-015). No hostname/user/SSH option/command/path.
```json
// request
{"session_target_id":"opaque…","cols":120,"rows":32}
// 201
{"terminal_id":"opaque…","ws_token":"<single-use secret>","ws_subprotocol":"remo-terminal.v1",
 "expires_in":30,"state":"pending"}
// 400 invalid dims (clamped/ rejected, FR-060) · 404 unknown/no-longer-discovered target (FR-050)
// 429 global/per-client terminal cap reached (defaults 32/16, FR-022)
```
`cols`/`rows` clamped to safe bounds (FR-060). The token is returned in the body once and MUST be sent
via the WS subprotocol, never a URL (FR-049).

## `GET /api/v1/terminals` — list this client's terminals

`{"terminals":[{"terminal_id":"…","session_target_id":"…","state":"ready","created_at":"…","last_activity_at":"…"}]}`

## `DELETE /api/v1/terminals/{terminal_id}` — close

Reaps the PTY/SSH attachment (FR-019); remote Zellij session is left running. `204`. Unknown id → `404`.

## `WS /api/v1/terminals/{terminal_id}` — terminal stream

See [terminal-websocket.md](./terminal-websocket.md). Handshake requires the matching single-use token
via subprotocol, valid `Origin`, and server-side re-authorization of the bound target (FR-049/FR-050).

## Error envelope (non-2xx)

```json
{"error":{"code":"…","message":"human-safe, secret-free","retryable":true|false,"remediation":"…"}}
```
Codes include `unreachable, auth_failed, no_remo_host, incompatible_protocol, malformed, timeout,
missing_project, cap_reached, invalid_dimensions, forbidden_origin, invalid_host, token_expired`.
