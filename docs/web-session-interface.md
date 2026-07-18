# Web Session Interface

`remo web` is a home-lab Docker service that gives you a browser-based terminal into any project on
any of your registered Remo instances, without a local CLI, SSH key, or SSH config on the client
device. It reads your existing Remo registry, connects to each instance over SSH (same transport and
provider behavior as `remo shell`), discovers projects and session state through a new `remo-host`
command installed on every instance, and streams interactive terminals to the browser over
WebSockets.

The service can get its configuration two ways: **bind-mount mode** (read-only mounts of your
workstation's registry and SSH key — the original deployment, unchanged) or **adopted mode**, where a
fresh container generates its own service-scoped SSH identity and a single `remo web adopt` command
from your workstation hands it your registry — your personal private key never leaves the
workstation. See [Deployment modes](#deployment-modes-mounts-vs-adoption) and
[CLI-to-web adoption](#cli-to-web-adoption).

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
- [Browser console UI](#browser-console-ui)
- [Security boundary](#security-boundary)
- [Deployment modes: mounts vs adoption](#deployment-modes-mounts-vs-adoption)
- [Docker Compose deployment](#docker-compose-deployment)
- [CLI-to-web adoption](#cli-to-web-adoption)
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
`api/hosts.py` and `api/terminals.py`). Frontend: `frontend/` (Vite + React + TypeScript). Terminals
render behind a Remo-owned adapter (`frontend/src/terminal/RendererAdapter.ts`) with two
interchangeable engines: **xterm.js** (`XtermRenderer.ts`) is the default — stable and
battle-tested — and **[ghostty-web](https://github.com/coder/ghostty-web)** (`GhosttyRenderer.ts`,
its WASM VT engine) is opt-in. The user switches between them at runtime via **Settings → Terminal
engine** (`settings.renderer`, persisted browser-side); ghostty falls back to xterm.js if its WASM
engine can't load. Either engine satisfies the same adapter, so the choice has no backend impact.

## Browser console UI

The SPA is a two-pane **web console**:

- **Session rail** (left, resizable/collapsible; auto-hidden on narrow viewports). Groups every
  registered instance with a provider-colored dot, name, region, and typed status. Each project is a
  row showing its name, git glyphs, and a Zellij-active bolt. A search box, provider-color filter
  chips, and an "⚡ Active only" toggle narrow the list; "⊞ Open all · N" opens every available
  target as a grid.
- **Terminal pane** (right). Clicking a row opens that target **solo** (single view); ⌘/Ctrl-click a
  row (or its `+` button) **adds** it to a responsive grid (1/2/3 columns by count). Clicking a grid
  tile solos it; **Esc** collapses the grid back to the focused terminal; number keys **1–9** jump to
  the numbered sessions (⌘ 1–9 add to the grid). Hidden terminals stay connected and keep their
  scrollback. Each terminal header shows `provider · instance · region`, connection state, and
  reconnect/close controls.

**Session-row glyphs** (also shown in the rail legend):

| Glyph | Meaning |
|---|---|
| ● | Uncommitted changes in the project's git work tree |
| ⇡ | Local commits ahead of upstream (to push) |
| ⇣ | Upstream commits behind (to pull) |
| ⚡ | Active Zellij session |

Git ahead/behind reflect the **last-known** upstream — discovery never runs `git fetch` (FR-010), so
they can be stale until something else fetches. Git glyphs only appear on instances running a
`remo-host` new enough to report git status; see [Upgrade compatibility](#upgrade-compatibility).

**Settings** (⚙, top bar; stored in this browser only, FR-034): accent color, terminal font, font
size, program ligatures, grid display mode (actual-size vs scale-to-fit), and a **Nerd Font uploader**.
Because a browser can't read fonts installed on the instance, uploading a patched Nerd Font once
registers it via the `FontFace` API (persisted in IndexedDB) and offers it as a terminal font — that's
how Powerline/Git/devicon glyphs in a prompt or Zellij status bar render. Font changes apply live to
every open terminal. The top bar also shows a health indicator (from `GET /api/v1/ready`) and an
offline overlay if the service becomes unreachable (terminals reattach automatically when it returns).
Press **?** for the keyboard-shortcut reference.

All fonts are self-hosted (bundled `@fontsource` assets), never fetched from a CDN, so the restrictive
same-origin CSP (`default-src 'self'`) is satisfied.

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

### Reverse proxies, SSO, and the setup surface

There is exactly one authenticated surface in the service: the **setup API** (`/api/v1/setup/*`) used
by `remo web adopt`/`remo web push`, gated by a bearer token (`REMO_WEB_API_TOKEN` — see
[CLI-to-web adoption](#cli-to-web-adoption)). Everything above about the *browser* surface remains
true regardless of deployment mode:

- **A reverse proxy without SSO does not change the trust model.** Putting the service behind a
  TLS-terminating proxy (e.g. an hola app behind Traefik) encrypts the transport, but the dashboard
  and terminals still have no login — the browser surface still needs proxy-level protection
  (network boundary, allowlisted client IPs, or proxy auth) exactly as described above.
- **A future forward-auth deployment needs a bypass rule scoped to `/api/v1/setup/*`.** The
  workstation CLI authenticates with the `Authorization: Bearer` token, not with a browser SSO
  session, so an SSO/forward-auth layer in front of the service would block adoption unless setup
  routes are exempted from it. That bypass is defensible because the application itself enforces the
  bearer token on every setup route — the surface is never unauthenticated. (Documentation-only
  guidance; no proxy/SSO integration ships with the service.)
- **Origin-less requests to the setup surface bypass the Origin allowlist — deliberately and
  safely.** The Origin allowlist is a browser-CSRF defense, and the setup API is bearer-token-only
  with no ambient credentials: a cross-origin browser request cannot attach an `Authorization`
  header, and a genuine browser CSRF attempt always carries an `Origin` header — which is still
  enforced (a present-but-disallowed `Origin` is rejected). This scoped exemption is what lets the
  Origin-less CLI client reach the setup API, including `--via` tunnels whose
  `127.0.0.1:<random-port>` origin could never be allowlisted. See the auth section of
  [`setup-api.md`](../specs/011-web-adopt/contracts/setup-api.md) and the middleware in
  `src/remo_cli/web/app.py`.

## Deployment modes: mounts vs adoption

The service runs in one of two deployment modes. The mode is never declared — it is **derived from
what is actually on disk** (`src/remo_cli/web/state.py`, pure filesystem probes, no mode flag or env
var that can drift out of sync with reality):

| | **Bind-mount mode** (original) | **Adopted mode** (011-web-adopt) |
|---|---|---|
| Registry | Your workstation's `~/.config/remo` bind-mounted **read-only** | Pushed by `remo web adopt`, stored in the writable state volume |
| SSH identity | **Your personal private key** bind-mounted read-only | A **service-scoped keypair** the container generates itself on first boot (`web-identity/id_ed25519`, comment `remo-web@<deployment-id>`) |
| Instance host keys | Your `~/.ssh/known_hosts` bind-mounted read-only | Verified host keys pushed by the CLI (`web-identity/known_hosts`) |
| Volumes | Several read-only bind mounts | **One** writable named volume at `REMO_HOME` (`/home/remo/.config/remo`) — no registry mount, no `~/.ssh` mounts |
| Required env | — | `REMO_WEB_API_TOKEN` (enables the token-gated setup API; without it adoption is impossible) |
| Runs where? | Effectively the same machine as your CLI config | Any host — nothing from the workstation is mounted |
| Registry updates | Edit/sync locally; the mount hot-reloads | `remo web push` after local changes |

Both modes use the identical image, identical hardening flags, and identical runtime behavior once
configured — adopted mode only moves *where configuration comes from*. Upgrading an existing
bind-mount deployment to this release changes nothing (FR-005/SC-005).

### How the service decides its mode

Everything the adopted service owns lives in one place: `<REMO_HOME>/web-identity/` — `id_ed25519` +
`id_ed25519.pub` (the service keypair, generated once via `ssh-keygen` and **never** silently
regenerated while the files exist), `known_hosts` (service-managed instance host keys), and
`state.json` (the deployment id). The registry stays at its usual path (`~/.config/remo/known_hosts`).
From these artifacts the service derives one of four states:

| State | Derivation | `remo web check` | `GET /api/v1/ready` | Browser |
|---|---|---|---|---|
| `unconfigured` | `REMO_HOME` writable, no registry (service keypair may already exist — generated, awaiting first push) | PASS: `unconfigured — awaiting adoption; run 'remo web adopt <service-url>' from a workstation` | **`200`** `{"status": "unconfigured", ...}` — healthy-and-waiting must not fail the compose healthcheck or crash-loop `restart: unless-stopped` | "Awaiting adoption" page: explains the state, shows a copy-pastable `remo web adopt <origin>` command, and flips to the dashboard automatically once adoption completes. No instance data, no terminals, no public-key display. |
| `adopted` | `REMO_HOME` writable + service keypair + registry present | PASS: `adopted — configured via 'remo web adopt' (service identity in web-identity/)` | `200` `{"status": "ready", ...}` | Normal dashboard |
| `mount_configured` | Registry present **and** (`REMO_HOME` not writable — the `:ro` bind mount — or a user SSH identity resolves via `REMO_WEB_SSH_IDENTITY_FILE`/`~/.ssh/id_*`). Explicit mounts are the operator's stated intent, so this wins even if a service keypair also exists. | PASS: `mount_configured — configured via read-only mounts` | `200` `{"status": "ready", ...}` | Normal dashboard |
| `broken` | Any required artifact present but unreadable, a half-pair service keypair, a registry on a writable volume with nothing able to authenticate, or a missing runtime prerequisite | FAIL with per-check remediation | `503` `{"status": "not_ready", ...}` with actionable detail — unchanged from today | Offline/error indicator |

The distinction that matters operationally: a fresh configless container is **`unconfigured`
(expected, actionable — run `remo web adopt`)**, never confused with **`broken` (something present
but unusable)**. The container entrypoint's startup gate (`remo web check --skip-instance-checks` in
[`docker/entrypoint.sh`](../docker/entrypoint.sh)) treats `unconfigured` as PASS for the same reason.

## Docker Compose deployment

See [`docker/compose.example.yml`](../docker/compose.example.yml) — copy and adapt it; it is not run
automatically. It pulls the published image and needs no source checkout, so the Compose file is the
only file you need:

```bash
curl -O https://raw.githubusercontent.com/get2knowio/remo/main/docker/compose.example.yml
# adapt the mounts to your host, then:
docker compose -f compose.example.yml up -d
```

The file defines **both deployment modes as alternative services** — run one or the other, not both
(they publish the same host port):

- `remo-web` — bind-mount mode. Started by a plain `docker compose up -d`, exactly as before.
- `remo-web-adopted` — adopted mode. Carries `profiles: ["adopted"]` so it only starts when you ask
  for it:

  ```bash
  docker compose -f compose.example.yml --profile adopted up -d
  ```

  Its differences from `remo-web`: **no bind mounts at all** — a single writable named volume
  (`remo-web-state:/home/remo/.config/remo`) holds the pushed registry, per-instance host keys, and
  the service identity keypair — plus a required `REMO_WEB_API_TOKEN` environment variable (generate
  one with `openssl rand -hex 24`). Hardening flags are identical. See
  [CLI-to-web adoption](#cli-to-web-adoption) for what happens after `up`.

### The published image

`ghcr.io/get2knowio/remo-web` is published on every stable release by
[`.github/workflows/release.yml`](../.github/workflows/release.yml) as a multi-arch manifest
(`linux/amd64` + `linux/arm64`), so the same `image:` line works on an x86 box and a Raspberry Pi.

| Tag | Moves | Use when |
|---|---|---|
| `latest` | Each stable release. Pre-release tags (`rc`/`beta`/`alpha`/`dev`) are **never** published as `latest`. | You want `docker compose pull` to track stable releases. |
| `2.1.0` (exact version) | Never — immutable. | You'd rather upgrade deliberately. Recommended if you care about reproducibility. |
| `2.1` (major.minor) | Each stable patch within that minor. | You want patch fixes but not minor bumps. |

### Building from source instead

Comment out `image:` and uncomment the `build:` block in the Compose file to build the same
multi-stage [`docker/Dockerfile`](../docker/Dockerfile) the published image comes from (stage 1
builds the `frontend/` SPA with Node; stage 2 builds a `remo-cli[web]` wheel; stage 3 is a slim Python
runtime with `openssh-client`, AWS CLI v2, and the Session Manager Plugin, arch-selected via
`$TARGETARCH` for amd64/arm64). That path needs a full repo checkout, and the Compose file must stay
in `docker/` for its relative build context to resolve.

Both paths are exercised on every PR by the `docker-image` job in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), which really builds the image for amd64 and
arm64 and runs it under the hardening flags below (see `tests/image/`).

### What each mount is for

These bind mounts are what define **bind-mount mode** — the adopted-mode service uses none of them,
only its named state volume.

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
and [Troubleshooting](#troubleshooting) below — `200` when the registry is readable, an SSH identity
resolves (a mounted key, or the adopted service's own keypair), the runtime dir is writable, and
required executables (`ssh`, and `aws`/`session-manager-plugin` when SSM instances are registered)
are present — **and also `200`** (`{"status": "unconfigured"}`) for a healthy adopted-mode container
still awaiting adoption, so an unconfigured deployment never flaps the healthcheck or crash-loops
(see [Deployment modes](#deployment-modes-mounts-vs-adoption)). Broken configuration keeps the `503`
semantics. `curl` is installed in the image specifically so this healthcheck can run without extra
tooling.

### Hardening flags and why each matters

| Flag | Value | Why |
|---|---|---|
| `read_only` | `true` | The image ships no legitimate reason to write to its own filesystem at runtime — all mutable state is either ephemeral (PTYs, SSH ControlMaster sockets) or lives in the explicit `tmpfs` mount. A read-only rootfs means a compromised process can't persist a backdoor into the image layer. |
| non-root `remo` (UID 1000) | non-root process | The app always ends up running as the dedicated `remo` user (UID 1000). In the read-only bind-mount service the container is pinned to `user: "1000:1000"`; the adopted-mode service instead starts as **root** and its entrypoint drops to `remo` via `gosu` after self-healing filesystem permissions (chowning a root-owned bind-mounted/named-volume config dir and re-healing the `/run/remo-ssh` tmpfs a restart remounts root-owned). Either way the serving process is non-root, which limits what a container-escape or dependency-confusion bug can do to the host. The adopted service therefore grants back only the minimal capabilities the heal-then-drop needs (`CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID`, `SETGID`) on top of `cap_drop: ALL`; the dropped-to `remo` process holds none of them effectively. |
| `security_opt: no-new-privileges:true` | — | Prevents any process in the container (including a compromised one) from gaining privileges via setuid/setgid binaries, closing off a common container-escape vector. |
| `cap_drop: ALL` | — | The service needs no Linux capabilities at all (it doesn't bind privileged ports, doesn't need `ptrace`, doesn't need raw sockets) — dropping everything removes capabilities an attacker could otherwise abuse. |
| `restart: unless-stopped` | — | Keeps the service available across host reboots/crashes without fighting an operator's deliberate `docker compose stop`. |
| `ports: "127.0.0.1:8080:8080"` (default) | loopback-only | Matches the LAN/tailnet security boundary above — the container's own network namespace binds `0.0.0.0` internally (Docker's DNAT requires this), but the **host-side** publish address stays loopback-only until you deliberately widen it (e.g. to a specific LAN IP or `0.0.0.0`) alongside setting `REMO_WEB_ALLOWED_HOSTS`/`REMO_WEB_ALLOWED_ORIGINS` to match. |

## CLI-to-web adoption

Adoption is the single-command handoff from a working workstation CLI to a freshly deployed
adopted-mode container: the workstation pushes its registry and the verified SSH host key of each
direct-access instance, and authorizes the **service's own public key** on every one of those
instances using your existing SSH access. Your personal private key, and your provider credentials,
never leave the workstation — see
[What is never transmitted or stored](#what-is-never-transmitted-or-stored).

Both commands live in the base CLI (`src/remo_cli/cli/web.py` → `src/remo_cli/core/web_adopt.py`,
stdlib HTTP only) — the `web` extra is **not** required on the workstation:

```text
remo web adopt [URL] [--token TEXT] [--via HOST] [--allow-empty] [--yes] [--save]
remo web push [--allow-empty] [--yes]
```

### First-time adoption walkthrough

**1. Deploy the container.** Via Compose (`docker compose --profile adopted up -d`, see
[Docker Compose deployment](#docker-compose-deployment)) or as an **hola app** — the hola app
manifest prompts for or generates the admin token at install time and supplies it to the container
as `REMO_WEB_API_TOKEN`; either way the operator's job is the same: a writable state volume plus the
token. Within ~30 seconds the container is up in the `unconfigured` state, has minted its
service-scoped keypair, and the browser shows the "awaiting adoption" page with the exact command to
run.

**2. Run `remo web adopt` on the workstation.** Inputs resolve in this order:

| Input | Resolution order |
|---|---|
| Service URL | argument → `REMO_API_URL` env var → interactive prompt |
| API token | `--token` → `REMO_API_TOKEN` env var → interactive prompt (hidden input) |

`REMO_API_URL`/`REMO_API_TOKEN` are the workstation-side counterparts of the service's
`REMO_WEB_API_TOKEN` — set them (e.g. exported by your shell profile, or handed out by the hola app
install output) and the command runs prompt-free:

```bash
export REMO_API_URL=http://docker-host.lan:8080
export REMO_API_TOKEN=<the value you set as REMO_WEB_API_TOKEN>
remo web adopt
```

The flow then: checks the service's state (aborting clearly if the target is mount-configured or the
token is rejected), fetches the service's public key and deployment id, and — per direct-access
instance, with a bounded per-instance time budget so one slow instance delays only itself —
`ssh-keyscan`s the host, verifies the scanned key against your own trusted `~/.ssh/known_hosts`
record (`ssh-keygen -F`, so hashed known_hosts files work; the service itself **never** makes a
trust-on-first-use decision), and installs the service's key into that instance's
`~/.ssh/authorized_keys` idempotently. It finishes by pushing the registry mirror, triggering a
server-side verification pass, and rendering the report.

**3. Read the summary.** Every registry entry gets exactly one outcome line, each with a one-line
remediation where applicable:

| Outcome | Meaning |
|---|---|
| `adopted` | Host key verified and pushed; service key authorized on the instance. |
| `skipped_unreachable` | Keyscan failed or timed out — instance down or unreachable from the workstation. Not fatal; re-run adopt when it's back. |
| `skipped_by_design` | SSM-routed instance (AWS-managed transport). No action needed — SSM instances are excluded from host-key and service-key push by design; see [Credentials and SSM](#credentials-and-ssm). |
| `skipped_no_trust` | Your workstation has no trusted host-key record and the run was non-interactive (`--yes`), so nothing was pushed. Interactively, you're prompted to confirm the SHA256 fingerprint instead. |
| `security_flagged` | **The scanned host key does not match your workstation's trusted record.** Rendered prominently as a potential MITM warning; nothing is pushed for that instance and the rest of the run continues. Investigate before trusting; if the instance was legitimately rebuilt, `ssh-keygen -R <host>`, reconnect once to re-trust it, then re-run adopt. |
| `unchanged` | (`remo web push` only) The instance matches the delta cache from the last successful push — keyscan/authorization skipped. |

**4. Read the verification report.** The service then re-checks itself and every pushed instance
(`remo-host capabilities` round-trips over its *own* identity) and the CLI renders the per-instance
PASS/FAIL lines. One outcome deserves a special mention: an instance the CLI just reached but the
service cannot is annotated **"reachable from workstation but not from the service"** — an
asymmetric-network case (e.g. the instance is only reachable via workstation-specific SSH client
config such as ProxyJump, or a firewall between the container host and the instance), not an
adoption failure.

**5. Saved credentials (explicit consent).** On success the CLI offers to save the service URL and
token to `~/.config/remo/web-service.json` (written atomically, mode `0600`) so that later
`remo web push` runs are zero-argument. Saving requires explicit consent — an interactive yes, or
the `--save` flag; `--yes` alone never saves. v1 stores a single default deployment. The file also
records the service's `deployment_id` and a per-instance push cache (see below).

The command exits `0` when the flow completes — per-instance skips/flags are reported in the
summary, not fatal — and `1` only on hard failure (auth rejection, mount-configured target, empty
registry without `--allow-empty`, tunnel failure, payload rejected). Re-running adopt is idempotent:
same summary, zero changes, still exactly one `remo-web@` line per instance.

### The setup API and `REMO_WEB_API_TOKEN`

The CLI talks to four token-gated endpoints under `/api/v1/setup/*`
([`setup-api.md`](../specs/011-web-adopt/contracts/setup-api.md)): `GET /status`, `GET /identity`,
`PUT /registry`, `POST /verify`. Every route requires `Authorization: Bearer <REMO_WEB_API_TOKEN>`,
compared in constant time:

- **Unset/empty token → the setup surface does not exist.** Every `/api/v1/setup/*` request gets a
  plain `404`, indistinguishable from an absent feature — fail closed, never open. All other
  functionality (dashboard, terminals, health) is unaffected. A bind-mount deployment that never
  sets the token therefore exposes no setup surface at all.
- **Wrong/missing header → `401`** with no further detail; the attempt is logged without the
  presented credential. The token value and `Authorization` headers are covered by the service's
  existing log redaction (`src/remo_cli/web/logging_config.py`).
- **Rotation = redeploy with a new value.** The token is deploy-time configuration; change it in the
  Compose file or hola app settings and restart, then re-run `remo web adopt` with the new token
  (saved workstation credentials with the old token fail with a clear re-auth message).

### Ongoing pushes: `remo web push`

After the initial adoption, local registry changes (a `remo <provider> sync`, a new `create`, a
removal) are re-synced with a zero-argument push:

```bash
remo incus sync my-incus-host    # e.g. registers a new instance locally
remo web push                    # re-sync it to the adopted service
```

`remo web push` loads the saved credentials, verifies the service still has the same
`deployment_id` (a mismatch means the state volume was reset — it aborts with re-adopt guidance),
then runs the adopt flow with **delta behavior**: only new or changed instances are re-keyscanned
and re-authorized; instances unchanged since the last successful push skip that work entirely and
are reported as `unchanged` (their previously verified host keys are reused, since every push
replaces the service's host-keys file wholesale). If no credentials were saved, `push` behaves
exactly like first-time `adopt`, prompting for URL and token.

**Mirror semantics — removals propagate, authorization does not.** The push is an exact mirror: the
workstation registry is the source of truth, so an instance you removed locally disappears from the
service's registry, the dashboard, and discovery, and no new sessions can target it. But the push
does **not** de-authorize the service on that instance — the service's key line REMAINS in the
instance's `~/.ssh/authorized_keys` until you remove it manually:

```bash
# On the instance (or via `remo shell`): revoke the service's access by
# deleting its single marker line — your own access is untouched.
sed -i '/ remo-web@/d' ~/.ssh/authorized_keys
```

Every entry the flow installs carries the `remo-web@<deployment-id>` comment marker, so it is always
exactly one identifiable line (`grep remo-web@ ~/.ssh/authorized_keys` to audit).

### Service key rotation

There is no dedicated rotation command in v1; rotation is a documented state-reset procedure:

1. **Reset the state volume** — e.g. `docker compose --profile adopted down` then
   `docker volume rm <project>_remo-web-state` (or delete the hola app's volume).
2. **Restart the container.** It boots `unconfigured` and mints a **new** identity (new keypair, new
   `deployment_id`).
3. **Re-run `remo web adopt`.** Because the `authorized_keys` management filters on the
   ` remo-web@` marker rather than the key material, the stale entry from the old identity is
   *replaced*, not accumulated — each instance in the current registry again ends up with exactly
   one service line. (`remo web push` alone won't do this: it detects the changed `deployment_id`
   and directs you to re-adopt.)

One caveat: instances **removed from your registry before the rotation** never get visited by the
re-adopt, so the *old* identity's entry lingers there — clean those up with the manual
de-authorization procedure above. The old private key is gone with the volume, so the stale entries
are inert, but hygiene says remove them.

### Tunnel fallback: `--via <host>`

When the service URL isn't directly reachable from the workstation (loopback-only port publish,
firewalled segment, a reverse proxy in the way for the setup calls), tunnel the adoption over your
existing SSH access to the deployment host:

```bash
remo web adopt --via docker-host.lan
```

The CLI binds a free local port, opens `ssh -N -L <free-port>:127.0.0.1:<service-port> <host>`
(with `ExitOnForwardFailure=yes`), and runs the identical flow against
`http://127.0.0.1:<free-port>`. Requirement: the service's `REMO_WEB_ALLOWED_HOSTS` must include
`127.0.0.1` (the default does), because the tunneled requests arrive with a loopback `Host` header —
the CLI's error message names this setting if the check fails. The tunneled requests are Origin-less
CLI traffic, covered by the setup surface's scoped Origin exemption (see
[Security boundary](#reverse-proxies-sso-and-the-setup-surface)).

### What is never transmitted or stored

- **Your personal SSH private key.** It is used *locally* to reach instances during
  adoption (the same `remo shell` transport), but no private key material crosses the wire in either
  direction at any point — the service authenticates with its own generated keypair, whose private
  half never leaves the container's state volume.
- **Provider credentials.** Hetzner/AWS API tokens, AWS CLI credentials/profiles — nothing
  provider-side is pushed to or stored by the service. The adoption payload is exactly the registry
  mirror (instance metadata) plus per-instance verified public host keys.

## Credentials and SSM

`remo web` reaches instances exactly the way `remo shell` does — it reuses the same
`build_ssh_base_cmd()` core logic (`src/remo_cli/core/ssh.py`), so per-instance behavior is identical:

- **Direct-SSH instances** (Proxmox, Incus, most Hetzner/AWS entries not using SSM): only an SSH
  identity and a known-hosts file for strict host-key verification are needed — in bind-mount mode
  the mounted `~/.ssh/id_ed25519` (or equivalent) and `~/.ssh/known_hosts`; in adopted mode the
  service's own generated identity and the pushed host keys under `web-identity/` (no mounts). No
  AWS mounts required either way.
- **AWS SSM-access-mode instances**: SSH is tunneled through an SSM session (`ProxyCommand`), so the
  container additionally needs the AWS CLI v2 and Session Manager Plugin (both bundled in the image)
  plus a read-only AWS credentials/profile mount (`${HOME}/.aws:/home/remo/.aws:ro` in the Compose
  example, commented out by default — uncomment it if you have any SSM-routed instances registered).
  Discovery and terminal attachment both follow this same SSM route.

**SSM and adoption**: SSM-routed instances are excluded from adoption's host-key push and service-key
authorization by design (`skipped_by_design` in the adopt summary) — their transport is AWS-managed,
not SSH-key-trust-managed. They still appear in the pushed registry mirror, but reaching them from
the service keeps requiring this same AWS credential-mount path, which is unchanged by the adoption
feature.

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

### Adoption issues

| Failure | What it means | Fix |
|---|---|---|
| `/api/v1/setup/*` returns `404` for everything | `REMO_WEB_API_TOKEN` is unset or empty — the setup surface is disabled entirely (fail closed). | Set `REMO_WEB_API_TOKEN` in the deployment (Compose `environment:` / hola app setting) and restart. |
| `remo web adopt` fails with an auth error (`401`) | The token you supplied doesn't match the service's `REMO_WEB_API_TOKEN`. | Re-check `REMO_API_TOKEN`/`--token` against the deployed value; after a token rotation, re-run adopt with the new value. |
| adopt fails: deployment "configured via read-only mounts" | The target is a bind-mount deployment (`mount_configured`) — its configuration is operator-provided and read-only, so adoption does not apply. | Update the mounted files instead, or deploy the adopted-mode service (writable state volume, no mounts) if you want adoption. |
| adopt refuses: empty registry | Your local registry has no instances — pushing would wipe a previously adopted service (a classic wrong-workstation accident). | Register/sync instances first, or pass `--allow-empty` if wiping is intentional. |
| `--via` fails naming `REMO_WEB_ALLOWED_HOSTS` | Tunneled requests arrive with a `127.0.0.1` Host header, which the service's Host allowlist rejects. | Add `127.0.0.1` to `REMO_WEB_ALLOWED_HOSTS` (the default includes it). |
| `remo web push` aborts: service identity changed | The saved `deployment_id` no longer matches the service — its state volume was reset and it minted a new identity. | Re-run `remo web adopt` (this replaces the stale `remo-web@` entries on your instances with the new key). |
| Summary line `security_flagged` (potential MITM warning) | The instance's scanned host key doesn't match your workstation's trusted record; nothing was pushed for it. | Investigate before trusting. If the instance was legitimately rebuilt: `ssh-keygen -R <host>`, reconnect once to re-trust, re-run adopt. |
| Verify report: "reachable from workstation but not from the service" | Asymmetric reachability — the CLI reached the instance but the container cannot (DNS, routing, firewall, or workstation-only SSH config like ProxyJump). | Fix the network path from the container host to the instance; the adoption itself succeeded. |

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

**Git status glyphs require this re-provision.** Per-project git status (`git_tracked`/`git_dirty`/
`git_ahead`/`git_behind`) was added to `remo-host` as additive, backward-compatible protocol-1 fields.
An instance still running the older `remo-host` simply omits them and the console shows no git glyphs
for its projects — nothing breaks. Run the `update` command above for each instance (e.g.
`remo proxmox update --name dev1`) to start reporting git status.

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
| `REMO_WEB_SSH_IDENTITY_FILE` | *(unset — falls back to the service keypair under `web-identity/`, then `~/.ssh/id_ed25519`/`id_ecdsa`/`id_rsa`/`id_dsa`)* | Explicit path to the SSH private key used for readiness/`remo web check`'s identity check, when it isn't one of the conventional filenames. |
| `REMO_WEB_API_TOKEN` | *(unset)* | Admin bearer token for the setup API (`/api/v1/setup/*`) used by `remo web adopt`/`remo web push`. **Fail-closed**: while unset or empty, the setup surface is disabled entirely — every setup route returns a plain `404` — and adoption is impossible; all other functionality is unaffected. Verified with a constant-time comparison and covered by log redaction. Rotation = redeploy with a new value (then re-run `remo web adopt` with it). Generate with e.g. `openssl rand -hex 24`. |

`remo web serve --host`/`--port` are convenience overrides for local runs; every other setting is env-var-only.

### Workstation-side environment variables

Two variables configure the **CLI** (not the service — hence no `REMO_WEB_` prefix), read by
`remo web adopt`/`remo web push`:

| Variable | Used as |
|---|---|
| `REMO_API_URL` | Service URL fallback when no URL argument is given (before falling back to an interactive prompt). |
| `REMO_API_TOKEN` | API token fallback when `--token` is not given (before falling back to a hidden interactive prompt). Set it to the same value as the service's `REMO_WEB_API_TOKEN`. |
