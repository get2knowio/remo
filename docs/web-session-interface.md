# Web Session Interface

`remo web` is a home-lab Docker service that gives you a browser-based terminal into any project on
any of your registered Remo instances, without a local CLI, SSH key, or SSH config on the client
device. It reads your existing Remo registry, connects to each instance over SSH (same transport and
provider behavior as `remo shell`), discovers projects and session state through a new `remo-host`
command installed on every instance, and streams interactive terminals to the browser over
WebSockets.

**A project opened in the browser and the same project opened with `remo shell -p <project>` attach
to the same remote Zellij session and the same devcontainer.** The web service does not implement a
second launcher; it reuses the same host-side scripts (`project-launch`) that the CLI does.

> ⚠️ **Security boundary — read this before deploying.** `remo web` is a **single-trusted-user MVP**.
> There are no accounts, no login, no per-user isolation. Anyone who can reach the service's HTTP/WS
> endpoint can open an interactive shell on **every instance in your registry**. It is designed to sit
> behind a trusted LAN, a Tailscale/tailnet interface, or a loopback reverse proxy — see
> [Security boundary](#security-boundary) below for the full trust model. **Do not expose it to the
> public internet.**

## Contents

- [Architecture](#architecture)
- [Security boundary](#security-boundary)
- [Docker Compose deployment](#docker-compose-deployment)
- [Credentials and SSM](#credentials-and-ssm)
- [Discovery states](#discovery-states)
- [Terminal limits](#terminal-limits)
- [Troubleshooting](#troubleshooting)
- [Upgrade compatibility](#upgrade-compatibility)
- [Configuration reference](#configuration-reference)

## Architecture

```text
~/.config/remo/known_hosts (read-only mount)
        │
        ▼
  remo-web (FastAPI + Uvicorn)
        │  per-instance SSH ControlMaster (multiplexed)
        ▼
  ssh <instance>  "remo-host capabilities --json"        ── discovery (US1)
  ssh <instance>  "remo-host sessions list --json"        ── discovery (US1)
  ssh -tt <instance>  "remo-host sessions attach --project <name>"  ── terminal (US2)
        │
        ▼
  remote Zellij session / devcontainer (unchanged from `remo shell`)
        │
        ▼
  server-side PTY  ⇄  WebSocket (binary PTY bytes + JSON control frames)  ⇄  browser (ghostty-web)
```

The service never talks to an instance except over SSH, and it never accepts a raw hostname,
username, SSH option, or shell command from the browser — only opaque, server-issued instance and
session-target IDs. Three protocol layers make this work, each documented in full under
[`specs/010-web-session-interface/contracts/`](../specs/010-web-session-interface/contracts/):

- **`remo-host` protocol** ([`remo-host-protocol.md`](../specs/010-web-session-interface/contracts/remo-host-protocol.md)) —
  a versioned, non-daemon command installed at `~/.local/bin/remo-host` on every instance (via the
  same `user_setup` Ansible role that installs `project-menu`/`project-launch`). It exposes
  `capabilities --json`, `sessions list --json`, and `sessions attach --project <name>`. It listens on
  no port and never accepts an arbitrary shell command — only these explicit, validated verbs.
- **REST API** ([`rest-api.md`](../specs/010-web-session-interface/contracts/rest-api.md)) — `GET
  /api/v1/health`, `GET /api/v1/ready`, `GET /api/v1/hosts`, `GET /api/v1/sessions`, `POST
  /api/v1/discovery/refresh`, and `POST`/`GET`/`DELETE /api/v1/terminals`. Terminal creation returns
  an opaque terminal ID plus a short-lived WebSocket token — never a hostname or command.
- **Terminal WebSocket protocol** ([`terminal-websocket.md`](../specs/010-web-session-interface/contracts/terminal-websocket.md)) —
  `WS /api/v1/terminals/{terminal_id}`, subprotocol `remo-terminal.v1`. Binary frames carry raw PTY
  bytes in both directions; JSON text frames carry control messages (`resize`, `ready`, `exit`,
  `error`, `ping`/`pong`).

Backend package: `src/remo_cli/web/` (`app.py` FastAPI factory, `config.py` settings, `discovery.py`,
`ssh_master.py`, `terminal.py`, `terminal_registry.py`, `tokens.py`, `health.py`, `check.py`, plus
`api/hosts.py` and `api/terminals.py`). Frontend: `frontend/` (Vite + React + TypeScript), using
[ghostty-web](https://github.com/ghostty-org) as the default terminal renderer behind a Remo-owned
adapter (`frontend/src/terminal/RendererAdapter.ts`), with an xterm.js fallback
(`frontend/src/terminal/XtermRenderer.ts`) if a compatibility gap ever requires swapping renderers.

## Security boundary

**Trust model.** `remo web` has exactly one implicit trust boundary: network reachability. There is no
login screen, no account system, no per-project or per-instance authorization. If a device can send
HTTP/WebSocket requests to the service, it can:

- See every instance and project in your registry, including reachability and session state.
- Open an interactive shell on any of them (subject only to the SSH identity mounted into the
  container already being authorized on that instance).

This is a deliberate MVP scope decision (see the feature spec's "Required Architectural Decisions"),
not an oversight — a later authentication layer can be added without changing the terminal protocol,
but it does not exist yet.

**What an attacker with network access could do.** Anyone who can reach the bound address and pass
the `Host`/`Origin` checks can list your projects and instances and attach a shell to any of them —
equivalent to having your SSH private key and registry. Treat network reachability to this service as
equivalent to handing out that access.

**Mitigations that do exist, even though authentication doesn't:**

- **Host/Origin validation** (`REMO_WEB_ALLOWED_HOSTS`/`REMO_WEB_ALLOWED_ORIGINS`) — state-changing
  HTTP requests and the WebSocket handshake are rejected (`403`/close code `1008`) unless the `Host`
  and `Origin` headers match an explicit allowlist. There is no wildcard CORS.
- **Single-use, short-lived WebSocket tokens** — `POST /api/v1/terminals` returns a token good for one
  WebSocket upgrade, expiring by default 30 seconds after issuance (`REMO_WEB_WS_TOKEN_TTL_S`). It
  travels only via the `Sec-WebSocket-Protocol` header, never a URL or query string, and is never
  written to logs. Replaying a consumed or expired token closes the connection (`1008`).
  See [Terminal WebSocket](../specs/010-web-session-interface/contracts/terminal-websocket.md).
  A token cannot be redirected to a different target after issuance — the server re-resolves the
  session target from the current registry/discovery cache at upgrade time (server-side
  reauthorization), so a fabricated or stale `session_target_id` is rejected with `404`.
- **No secrets reach the browser or logs.** SSH keys, AWS credentials, proxy commands, and WS tokens
  are redacted from application logs and from error text sent to the browser (`src/remo_cli/web/logging_config.py`).
- **Restrictive CSP + same-origin assets.** The terminal WASM asset is served same-origin, not from a
  public CDN, under a Content-Security-Policy that doesn't need to relax script/connect sources for a
  third party.

**Why this isn't multi-user/authenticated.** The spec explicitly scopes the MVP to a single trusted
operator on a trusted network (LAN/tailnet) to keep the terminal protocol and server boundary simple
while still being safe to run continuously. Built-in accounts, OIDC, and RBAC are out of scope for
this MVP but are called out as an explicit, addable post-MVP direction — the server boundary is shaped
so an auth middleware can be layered on later without changing `POST /terminals` or the WebSocket
framing.

**Bottom line:** run this only where you'd be comfortable handing out SSH access to every registered
instance — your own LAN, your own tailnet, or behind a reverse proxy you control and trust.

## Docker Compose deployment

See [`docker/compose.example.yml`](../docker/compose.example.yml) — copy and adapt it; it is not run
automatically. It builds from the multi-stage [`docker/Dockerfile`](../docker/Dockerfile) (stage 1
builds the `frontend/` SPA with Node; stage 2 builds a `remo-cli[web]` wheel; stage 3 is a slim Python
runtime with `openssh-client`, AWS CLI v2, and the Session Manager Plugin, arch-selected via
`$TARGETARCH` for amd64/arm64).

### What each mount is for

| Mount | Purpose | Why read-only |
|---|---|---|
| `${HOME}/.config/remo:/home/remo/.config/remo:ro` | The Remo **registry** (`known_hosts`) — provider type, instance name, address, SSH user, access mode, region. | This is metadata, **not authentication material** (see below). The service never needs to write it; FR-004 requires hot-reload without a container restart, not mutation. |
| `${HOME}/.ssh/id_ed25519:/home/remo/.ssh/id_ed25519:ro` (+ `config`, `known_hosts`) | The **SSH identity** that actually authenticates to every instance. | The service only ever needs to *use* this key, never modify it; read-only limits blast radius if the container is compromised. |
| `${HOME}/.aws:/home/remo/.aws:ro` (optional, commented out by default) | AWS credentials/profile for any registered instance using the SSM access mode. | Same reasoning — read-only, and only mounted at all if you actually have SSM-routed instances. |

**"Registry is metadata, not authentication material" (FR-026, US4 scenario 2)**: mounting only the
registry and nothing else is a common misconfiguration. The registry tells the service *which*
instances exist and how to address them, but contains no credentials — connecting to any of them still
requires the separate SSH identity mount. If you mount only the registry, `GET /api/v1/ready` returns
`503` with a message that says exactly this (see `src/remo_cli/web/health.py`), and `remo web check`
reports the same `ssh_identity` failure with a remediation pointing at the correct env var
(`REMO_WEB_SSH_IDENTITY_FILE`) or the conventional `~/.ssh/id_ed25519`/`id_ecdsa`/`id_rsa`/`id_dsa`
filenames.

### tmpfs requirement

```yaml
tmpfs:
  - "/run/remo-ssh"
```

SSH ControlMaster sockets (one per distinct SSH destination, multiplexing every terminal attached to
that instance) must live somewhere writable. Because the container root filesystem is read-only
(below), this tmpfs mount is not optional — without it, every SSH connection attempt fails at socket
creation. The path is configurable via `REMO_WEB_SSH_CONTROL_DIR` (default `/run/remo-ssh`) if you
need to point it elsewhere.

### Healthcheck

```yaml
healthcheck:
  test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/api/v1/ready"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 10s
```

This calls the same `GET /api/v1/ready` endpoint described in [Discovery states](#discovery-states)
and [Troubleshooting](#troubleshooting) below — `200` only when the registry is readable, an SSH
identity is mounted, the runtime dir is writable, and required executables (`ssh`, and `aws`/
`session-manager-plugin` when SSM instances are registered) are present. `curl` is installed in the
image specifically so this healthcheck can run without extra tooling.

### Hardening flags and why each matters

| Flag | Value | Why |
|---|---|---|
| `read_only` | `true` | The image ships no legitimate reason to write to its own filesystem at runtime — all mutable state is either ephemeral (PTYs, SSH ControlMaster sockets) or lives in the explicit `tmpfs` mount. A read-only rootfs means a compromised process can't persist a backdoor into the image layer. |
| `user: "1000:1000"` | non-root UID/GID | The image creates a dedicated `remo` user (UID 1000) at build time and drops root via `USER remo` in the Dockerfile before the entrypoint runs. Running as non-root limits what a container-escape or dependency-confusion bug can do to the host. |
| `security_opt: no-new-privileges:true` | — | Prevents any process in the container (including a compromised one) from gaining privileges via setuid/setgid binaries, closing off a common container-escape vector. |
| `cap_drop: ALL` | — | The service needs no Linux capabilities at all (it doesn't bind privileged ports, doesn't need `ptrace`, doesn't need raw sockets) — dropping everything removes capabilities an attacker could otherwise abuse. |
| `restart: unless-stopped` | — | Keeps the service available across host reboots/crashes without fighting an operator's deliberate `docker compose stop`. |
| `ports: "127.0.0.1:8080:8080"` (default) | loopback-only | Matches the LAN/tailnet security boundary above — the container's own network namespace binds `0.0.0.0` internally (Docker's DNAT requires this), but the **host-side** publish address stays loopback-only until you deliberately widen it (e.g. to a specific LAN IP or `0.0.0.0`) alongside setting `REMO_WEB_ALLOWED_HOSTS`/`REMO_WEB_ALLOWED_ORIGINS` to match. |

## Credentials and SSM

`remo web` reaches instances exactly the way `remo shell` does — it reuses the same
`build_ssh_base_cmd()` core logic (`src/remo_cli/core/ssh.py`), so per-instance behavior is identical:

- **Direct-SSH instances** (Proxmox, Incus, most Hetzner/AWS entries not using SSM): only the mounted
  SSH identity (`~/.ssh/id_ed25519` or equivalent) and a known-hosts/config file for strict host-key
  verification are needed. No AWS mounts required.
- **AWS SSM-access-mode instances**: SSH is tunneled through an SSM session (`ProxyCommand`), so the
  container additionally needs the AWS CLI v2 and Session Manager Plugin (both bundled in the image)
  plus a read-only AWS credentials/profile mount (`${HOME}/.aws:/home/remo/.aws:ro` in the Compose
  example, commented out by default — uncomment it if you have any SSM-routed instances registered).
  Discovery and terminal attachment both follow this same SSM route.

`remo web check` (see [Troubleshooting](#troubleshooting)) only requires and checks `aws_cli`/
`ssm_plugin` executables when at least one registered instance actually uses SSM access — it reads the
registry to make that determination, unlike the lighter `GET /api/v1/ready` liveness/readiness probe.

## Discovery states

Each instance's discovery result carries a typed `status` rather than an empty success, so a broken
instance never looks the same as "no projects" (FR-006). From
[`data-model.md`](../specs/010-web-session-interface/data-model.md) and
[`rest-api.md`](../specs/010-web-session-interface/contracts/rest-api.md):

| Status | Meaning | Remediation |
|---|---|---|
| `ok` | `remo-host capabilities` and `sessions list` both succeeded; `capability` and `targets` are populated (targets may be an empty list if the instance has no projects). | — |
| `unreachable` | SSH connection failed (network/timeout/host down). | Retryable — check the instance is running and reachable. |
| `auth_failed` | SSH connected but authentication was rejected. | Verify the mounted SSH identity is authorized on that instance. |
| `no_remo_host` | The instance answered but has no `remo-host` command installed. | Not retryable as-is — re-run the instance's configure/update flow (see [Upgrade compatibility](#upgrade-compatibility)) to install it. |
| `incompatible_protocol` | `remo-host` responded, but its `protocol_version` is outside the client's supported `[min,max]` range. | Update the instance's Remo host tools to a version whose `remo-host` reports a compatible protocol version. |
| `malformed` | `remo-host` produced output that isn't valid/parseable JSON for the expected schema. | Usually indicates a broken or partial `remo-host` install — re-run configure. |
| `timeout` | The remote command didn't respond within the configured discovery timeout. | Retryable — the instance may be slow or overloaded; increase `REMO_WEB_DISCOVERY_TIMEOUT_S` if this is chronic. |

One instance's failure never blocks or delays the others — discovery runs concurrently per instance
(`REMO_WEB_DISCOVERY_CONCURRENCY`), and each instance's snapshot is independent.

## Terminal limits

Two configurable caps bound how many concurrent server-side PTY/SSH attachments can exist at once:

- **Global cap**: 32 concurrent terminals by default, across all clients — `REMO_WEB_TERMINAL_CAP_GLOBAL`.
- **Per-client cap**: 16 concurrent terminals by default, for a single browser client — `REMO_WEB_TERMINAL_CAP_PER_CLIENT`.

Both are comfortably above the nine-terminal (3 instances × 3 projects) baseline the feature was tested
against. Exceeding either cap returns `429` from `POST /api/v1/terminals` with a clear message rather
than silently queuing or degrading existing terminals.

## Troubleshooting

Run `remo web check` (or `docker compose exec remo-web remo web check` in the container) for a
PASS/FAIL report with per-check remediation. It performs a strict superset of what `GET
/api/v1/ready` checks, plus per-instance reachability/protocol checks — and never opens an
interactive session (only `remo-host capabilities` is invoked, never `sessions attach`).

| Failure | What it means | Fix |
|---|---|---|
| `registry` FAIL — not found / not readable | `~/.config/remo` isn't mounted, or the mount is wrong. | Mount the Remo registry read-only at the configured `REMO_HOME`/`XDG_CONFIG_HOME` path (see [Docker Compose deployment](#docker-compose-deployment)). |
| `ssh_identity` FAIL — no SSH private key found | Only the registry is mounted, or the SSH key path doesn't match. | Mount a private key read-only (`REMO_WEB_SSH_IDENTITY_FILE` or the conventional `~/.ssh/id_ed25519` etc.). Remember: the registry is metadata, not authentication material. |
| `runtime_dir` FAIL — not writable | No tmpfs (or writable directory) exists at the SSH ControlMaster socket path. | Add the `tmpfs: ["/run/remo-ssh"]` mount (or point `REMO_WEB_SSH_CONTROL_DIR` at a writable location). |
| `ssh`/`aws_cli`/`ssm_plugin` FAIL — not found on PATH | A required executable is missing from the runtime environment. | Use the provided image (these are bundled); if running outside Docker, install the missing tool. |
| `instance <type>/<name>` FAIL — `no_remo_host` | That specific instance predates the `remo-host` rollout, or its install failed. | Re-run that instance's configure/update flow — see [Upgrade compatibility](#upgrade-compatibility). |
| `instance <type>/<name>` FAIL — unreachable / timeout | Network path or instance state issue, isolated to that one instance. | Confirm the instance is running and reachable from the Docker host; other instances are unaffected. |

## Upgrade compatibility

`remo-host` is versioned. The client (`src/remo_cli/core/remo_host_client.py`) declares a supported
inclusive major-version range — currently **`[1, 1]`** — and treats any host reporting a
`protocol_version` within that range as compatible, tolerating additive/minor fields within a major
version. A host reporting a version outside the range surfaces as the typed `incompatible_protocol`
discovery status with a per-instance update prompt, rather than silently failing or falling back to
scraping human-facing `project-menu` output.

**Mixed fleet during a rollout/upgrade** is expected and supported: if you update `remo-host` on one
instance but not another, discovery keeps working across the whole registry — the updated instance
reports its new (still-compatible, same major version) capabilities, older instances continue to work
as long as they're within `[1, 1]`, and only instances truly outside the supported range show
`incompatible_protocol`/`no_remo_host`.

**How to pick up a newer `remo-host` on an already-provisioned instance:** `remo-host` is installed by
the same `user_setup` Ansible role that installs `project-menu`/`project-launch`
(`ansible/roles/user_setup/templates/remo-host.sh.j2`, idempotent install task in
`ansible/roles/user_setup/tasks/main.yml`). That role runs as part of both the initial `create` flow
and the `update` flow for every provider, so re-running:

```bash
remo aws update        # or: remo hetzner update / remo incus update / remo proxmox update
```

against the affected instance re-templates and reinstalls `remo-host` in place — no full recreate is
needed, and the update is idempotent (safe to run repeatedly, on both fresh and already-configured
hosts).

## Configuration reference

Every setting is an environment variable prefixed `REMO_WEB_`, resolved by `WebSettings`
(`src/remo_cli/web/config.py`) at process start. All have safe defaults, so `remo web serve` works
locally with zero configuration; a container overrides everything via env alone.

| Variable | Default | Description |
|---|---|---|
| `REMO_WEB_BIND_HOST` | `127.0.0.1` | Address the Uvicorn server binds to. `--host` on `remo web serve` overrides this per-invocation. The Docker image sets this to `0.0.0.0` internally via the Dockerfile's `ENV` (Docker's port publishing can't reach a loopback-only bind); the host-side LAN exposure decision stays in Compose's `ports:` mapping. |
| `REMO_WEB_BIND_PORT` | `8080` | Port the server binds to. `--port` on `remo web serve` overrides this per-invocation. |
| `REMO_WEB_DISCOVERY_CONCURRENCY` | `8` | Maximum number of instances discovered concurrently. |
| `REMO_WEB_DISCOVERY_TIMEOUT_S` | `10.0` | Per-instance timeout (seconds) for a discovery round-trip before it's classified `timeout`. |
| `REMO_WEB_DISCOVERY_CACHE_TTL_S` | `30.0` | How long a discovery snapshot is served from cache before the next scheduled refresh; manual refresh (`POST /api/v1/discovery/refresh`) bypasses this. |
| `REMO_WEB_TERMINAL_CAP_GLOBAL` | `32` | Maximum concurrent terminal attachments across all clients. |
| `REMO_WEB_TERMINAL_CAP_PER_CLIENT` | `16` | Maximum concurrent terminal attachments for a single client. |
| `REMO_WEB_WS_TOKEN_TTL_S` | `30.0` | Seconds a single-use WebSocket terminal token remains valid between issuance and successful upgrade. |
| `REMO_WEB_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Comma-separated allowlist for the HTTP `Host` header on state-changing requests and the WS handshake. No wildcard is supported — set this explicitly for any real deployment. |
| `REMO_WEB_ALLOWED_ORIGINS` | `http://127.0.0.1:8080,http://localhost:8080` | Comma-separated allowlist for the `Origin` header on state-changing requests and the WS handshake. No wildcard CORS. |
| `REMO_WEB_SSH_CONTROL_DIR` | `/run/remo-ssh` | Writable directory for SSH ControlMaster sockets (must be tmpfs or otherwise writable under a read-only rootfs). |
| `REMO_WEB_FRONTEND_DIST_DIR` | `<repo_root>/frontend/dist` (resolved relative to the installed package) | Directory the built frontend SPA is served from. The Docker image overrides this to `/app/frontend-dist`, matching where the multi-stage build actually copies the built assets. |
| `REMO_WEB_SSH_IDENTITY_FILE` | *(unset — falls back to `~/.ssh/id_ed25519`/`id_ecdsa`/`id_rsa`/`id_dsa`)* | Explicit path to the SSH private key used for readiness/`remo web check`'s identity check, when it isn't one of the conventional filenames. |

`remo web serve --host`/`--port` are convenience overrides for local runs; every other setting is env-var-only.
