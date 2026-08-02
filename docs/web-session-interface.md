# Web Session Interface

`remo web` is a home-lab Docker service that gives you a browser-based terminal into any project on
any of your registered Remo instances, without a local CLI, SSH key, or SSH config on the client
device. It reads your existing Remo registry, connects to each instance over SSH (same transport and
provider behavior as `remo shell`), discovers projects and session state through a new `remo-host`
command installed on every instance, and streams interactive terminals to the browser over
WebSockets.

The service can get its configuration two ways: **bind-mount mode** (read-only mounts of your
workstation's registry and SSH key — the original deployment, unchanged) or **adopted mode**, where a
fresh container generates its own service-scoped SSH identity and a single `remo web push` command
from your workstation hands it your registry — the first push adopts, every later push re-syncs, and
your personal private key never leaves the workstation. See
[Deployment modes](#deployment-modes-mounts-vs-adoption) and
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
~/.config/remo/registry.json (read-only mount; legacy known_hosts also read in place)
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
  `GET /hosts` and `GET /sessions` only **read** the service's discovery cache; `POST
  /discovery/refresh` is the only call that repopulates it, which is why the console posts it on a
  background cadence (with `force: false`, so the cache TTL decides when a run is really due)
  rather than polling the GETs alone.
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
  row (or its `+` button) **adds** it to a responsive grid (1/2/3 columns by count). In a grid, **drag
  a tile's header onto another to swap their positions** — a window outline follows the cursor and the
  swap target shows a dashed outline (mouse or touch press-and-hold; keyboard-accessible via the
  handle); the arrangement persists until the grid is rebuilt. In a grid, **resting the pointer on a
  tile focuses it** (focus-follows-mouse, with a short dwell so passing through tiles doesn't steal
  focus) — keystrokes go where the pointer is, no click needed. The **◻** control solos a tile; **Esc**
  collapses the grid back to the focused terminal;
  number keys **1–9** jump to the numbered sessions (⌘ 1–9 add to the grid). Hidden terminals stay
  connected and keep their scrollback. Each terminal header shows `provider · instance · region`
  (doubling as the drag handle), connection state, and a
  window-control cluster of the display modes, ordered by how much space they take: **⊞ Grid** (smaller —
  a tile in the grid, when one is available), **◻ Normal** (fills the app's main pane — single view),
  **⤢ Fullscreen** (the terminal fills the whole window — shell chrome hidden, plus best-effort browser
  fullscreen), and **✕ Close** — with the current mode shown active. Press **f** to
  toggle fullscreen on the focused terminal; **Esc** exits it. Fullscreen is a presentation overlay: it
  never disturbs the single/grid layout underneath, so exiting returns to exactly where you were.

**Clipboard & links.** Select text and press **⌘C** (macOS) / **Ctrl+Shift+C** (Linux/Windows), or click
the **⧉ Copy** button that appears on selection, to copy to the system clipboard; bare **Ctrl+C** stays
SIGINT. **Paste** with ⌘V / Ctrl+V. **http(s) URLs are clickable** and open in a new tab. Remote apps that
emit **OSC 52** (e.g. Claude Code's copy-on-select) can write to the browser clipboard — best-effort: it
must traverse Zellij and the browser must permit a gesture-less clipboard write. OSC 52 *reads* are denied
(a remote app can never read your clipboard). Clipboard access needs a secure context (HTTPS or localhost).

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

**Settings** (⚙, top bar; stored in this browser only, FR-034): **appearance** (site light/dark mode),
accent color, terminal font, **terminal theme**, font size, program ligatures, grid display mode
(actual-size vs scale-to-fit), **focus dwell** (how long the pointer rests before focus-follows-mouse
fires), terminal engine, a **Nerd Font uploader**, and **Pair CLI to sync** (mint a re-sync pairing
code).

**Appearance** is tri-state — `system` (the default, following the OS `prefers-color-scheme`),
`light`, or `dark` — toggled from the ◐/☀/☾ button in the top bar as well as from Settings. The
console's palette is a set of `light-dark()` CSS custom properties in `theme/tokens.css`; "system"
therefore needs no JavaScript to render, and an explicit choice is applied as `data-theme` on `<html>`.
**Terminal theme** defaults to **Follow site theme**, which tracks the site mode: a light console gets
**Remo Light**, a dark one **Remo Dark**. Those two are derived from `theme/tokens.css` — terminal
background is `--bg-term`, foreground `--text`, ANSI 1–6 the `--danger`/`--ok`/`--warn`/`--info`/`--mag`/
`--cyan` ramp — so the terminal sits flush with the chrome around it. They are hand-derived *snapshots*
(a terminal palette must be literal `#rrggbb`; ghostty-web's parser accepts neither `oklch()` nor
`var()`), so editing a token does not update them. Six curated third-party schemes are also available —
Catppuccin Mocha/Latte, Dracula, Gruvbox Dark/Light, Solarized Light — and any theme can be used under
either site mode; picking one explicitly stops the terminal following the site. The Settings choice is
the default for every terminal; an individual terminal can override it from the color swatch in its
header, and clearing that override back to "Default" makes it follow the global choice again. Theme
changes apply live — the terminal is recolored in place, keeping the connection and scrollback. Because a browser can't read fonts installed on the
instance, uploading a patched Nerd Font once registers it via the `FontFace` API (persisted in
IndexedDB) and offers it as a terminal font — that's how Powerline/Git/devicon glyphs in a prompt or
Zellij status bar render. Font changes apply live to every open terminal. The top bar shows the
**WebSocket round-trip latency** (median across open terminals, with the dot green/yellow/red by
latency), falling back to the service health status when no terminal is connected; an offline overlay
appears if the service becomes unreachable (terminals reattach automatically when it returns). Press
**?** for the keyboard-shortcut reference.

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

> **Breaking change (012-web-adopt-pairing):** the static `REMO_WEB_API_TOKEN` gate is **removed**. A
> value set for that variable is now ignored. Setup access is authorized by an **ephemeral pairing
> code** minted from the awaiting-adoption page, not a long-lived secret.

The setup API (`/api/v1/setup/*`) used by `remo web push` (and the deprecated `remo web adopt` alias)
is **dormant** — every
route returns `404`, byte-identical to an unknown route — unless a **pairing session** is live. A
session exists only while an operator is on the awaiting-adoption page (or the dashboard's re-sync
affordance): opening the page mints a short-lived, single-use pairing code (sliding idle TTL, default
15 min), the operator copies it to their workstation and pastes it into the CLI, and the code
authenticates that one adoption/push. See [CLI-to-web adoption](#cli-to-web-adoption).

Two properties make this safe:

- **Minting a code is gated by operator authentication.** The browser-facing
  `POST /api/v1/pairing/mint` endpoint only mints for an authenticated operator. v1 implements this
  with **forward auth**: put a proxy (Traefik ForwardAuth / oauth2-proxy / Authelia / a hola app's
  SSO) in front that terminates sign-on and injects a trusted identity header, and set
  `REMO_WEB_OPERATOR_AUTH=forward` + `REMO_WEB_FORWARD_AUTH_HEADER=<that header>` (e.g.
  `X-Forwarded-User`). Enabling forward auth without naming a header is a **fail-fast** startup error.
  A loopback/dev deployment may instead set `REMO_WEB_OPERATOR_AUTH=none` (network-restricted — mints
  without operator auth; a loud, weaker posture surfaced in readiness). The check sits behind a
  pluggable provider seam so an in-app OIDC verifier can be added later.
- **The proxy must split the two paths.** Forward auth applies **only** to `POST /api/v1/pairing/mint`
  — the CLI cannot complete an SSO challenge, so the proxy MUST **pass `/api/v1/setup/*` through**
  unauthenticated at the proxy layer; those routes are authenticated by the pairing code alone. In
  short: gate `/api/v1/pairing/mint` with SSO, pass `/api/v1/setup/*` through.

**Forward-auth trust boundary.** The service trusts the identity header only because the deployment
guarantees the proxy sits in front and **sets/strips** that header — so a client cannot reach the app
directly and spoof it. This is the standard forward-auth boundary; a deployment that exposes the app
directly (no proxy) MUST use `REMO_WEB_OPERATOR_AUTH=none` and accept the weaker posture.

**Origin-less requests to the setup surface bypass the Origin allowlist — deliberately and safely.**
The Origin allowlist is a browser-CSRF defense, and the setup API carries no ambient credentials: a
cross-origin browser request cannot attach an `Authorization` header, and a genuine browser CSRF
attempt always carries an `Origin` (still enforced). This scoped exemption lets the Origin-less CLI
reach the setup API, including `--via` tunnels whose `127.0.0.1:<random-port>` origin could never be
allowlisted. The browser-only `POST /api/v1/pairing/mint` is **not** exempt — it is held to the Origin
check. See [`setup-api.md`](../specs/012-web-adopt-pairing/contracts/setup-api.md),
[`pairing-api.md`](../specs/012-web-adopt-pairing/contracts/pairing-api.md), and the middleware in
`src/remo_cli/web/app.py`.

## Deployment modes: mounts vs adoption

The service runs in one of two deployment modes. The mode is never declared — it is **derived from
what is actually on disk** (`src/remo_cli/web/state.py`, pure filesystem probes, no mode flag or env
var that can drift out of sync with reality):

| | **Bind-mount mode** (original) | **Adopted mode** (011-web-adopt) |
|---|---|---|
| Registry | Your workstation's `~/.config/remo` bind-mounted **read-only** | Pushed by `remo web push`, stored in the writable state volume |
| SSH identity | **Your personal private key** bind-mounted read-only | A **service-scoped keypair** the container generates itself on first boot (`web-identity/id_ed25519`, comment `remo-web@<deployment-id>`) |
| Instance host keys | Your `~/.ssh/known_hosts` bind-mounted read-only | Verified host keys pushed by the CLI (`web-identity/known_hosts`) |
| Volumes | Several read-only bind mounts | **One** writable named volume at `REMO_HOME` (`/home/remo/.config/remo`) — no registry mount, no `~/.ssh` mounts |
| Required env | — | `REMO_WEB_OPERATOR_AUTH` (`forward` + `REMO_WEB_FORWARD_AUTH_HEADER`, or `none` for loopback/dev) — gates pairing-code minting; without a provider, minting is disabled and adoption is impossible |
| Runs where? | Effectively the same machine as your CLI config | Any host — nothing from the workstation is mounted |
| Registry updates | Edit/sync locally; the mount hot-reloads | `remo web push` after local changes |

Both modes use the identical image, identical hardening flags, and identical runtime behavior once
configured — adopted mode only moves *where configuration comes from*. Upgrading an existing
bind-mount deployment to this release changes nothing (FR-005/SC-005).

### How the service decides its mode

Everything the adopted service owns lives in one place: `<REMO_HOME>/web-identity/` — `id_ed25519` +
`id_ed25519.pub` (the service keypair, generated once via `ssh-keygen` and **never** silently
regenerated while the files exist), `known_hosts` (service-managed instance host keys), and
`state.json` (the deployment id). The registry stays at its usual path (`~/.config/remo/registry.json`,
format v2 — a legacy `known_hosts` file is still read in place if present, e.g. right after an
in-place service upgrade, before the next adoption push replaces it).
From these artifacts the service derives one of four states:

| State | Derivation | `remo web check` | `GET /api/v1/ready` | Browser |
|---|---|---|---|---|
| `unconfigured` | `REMO_HOME` writable, no registry (service keypair may already exist — generated, awaiting first push) | PASS: `unconfigured — awaiting adoption; run 'remo web adopt <service-url>' from a workstation` | **`200`** `{"status": "unconfigured", ...}` — healthy-and-waiting must not fail the compose healthcheck or crash-loop `restart: unless-stopped` | "Awaiting adoption" page: explains the state, shows the `remo web adopt <origin>` command, and a **Copy pairing code** button (the code itself is never displayed). Flips to the dashboard automatically once adoption completes. No instance data, no terminals, no public-key display. |
| `adopted` | `REMO_HOME` writable + service keypair + registry present | PASS: `adopted — configured via 'remo web adopt' (service identity in web-identity/)` | `200` `{"status": "ready", ...}` | Normal dashboard |
| `mount_configured` | Registry present **and** `REMO_HOME` **not writable** (the `:ro` bind mount). A non-writable `REMO_HOME` is now the *only* heuristic mount signal — a readable personal `~/.ssh/id_*` no longer forces this mode (017-web-adopt-simplify, US6), so bare-metal `remo web serve` on a writable home classifies as `adopted`, not `mount_configured`. | PASS: `mount_configured — configured via read-only mounts` | `200` `{"status": "ready", ...}` | Normal dashboard |
| `broken` | Any required artifact present but unreadable, a half-pair service keypair, a registry on a writable volume with nothing able to authenticate, or a missing runtime prerequisite | FAIL with per-check remediation | `503` `{"status": "not_ready", ...}` with actionable detail — unchanged from today | Offline/error indicator |

The distinction that matters operationally: a fresh configless container is **`unconfigured`
(expected, actionable — run `remo web push`)**, never confused with **`broken` (something present
but unusable)**. The container entrypoint's startup gate (`remo web check --skip-instance-checks` in
[`docker/entrypoint.sh`](../docker/entrypoint.sh)) treats `unconfigured` as PASS for the same reason.

**Explicit override (`REMO_WEB_MODE`).** When the heuristic above needs to be forced — most often to
pin bare-metal `remo web serve` to `adopted`, or to be unambiguous in an unusual layout — set
`REMO_WEB_MODE` to `adopted` or `mount_configured`. It is honored deterministically **after** the
`broken` guards (a present-but-unreadable artifact or a half-pair service keypair still classifies as
`broken`, override or not), and before the writable/keypair heuristic. An invalid value is a fail-fast
startup error. Leaving it unset keeps the derivation above. Because the authoritative
`mount_configured` signal is now a non-writable `REMO_HOME` (not the mere presence of a personal key),
the override is rarely needed — bare-metal serve already lands on `adopted` — but it remains the
deterministic escape hatch. See [Configuration reference](#configuration-reference).

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
  the service identity keypair — plus a `REMO_WEB_OPERATOR_AUTH` setting (`forward` behind an SSO
  proxy, or `none` for loopback/dev) that gates pairing-code minting. Hardening flags are identical.
  See [CLI-to-web adoption](#cli-to-web-adoption) for what happens after `up`.

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
| `${HOME}/.config/remo:/home/remo/.config/remo:ro` | The Remo **registry** (`registry.json`, format v2 — or legacy `known_hosts`, read in place) — provider type, instance name, address, SSH user, access, and per-type fields (e.g. AWS instance id/region). | This is metadata, **not authentication material** (see below). The service never needs to write it; FR-004 requires hot-reload without a container restart, not mutation. |
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

**One command: `remo web push`.** The *first* push to a not-yet-adopted deployment adopts it; every
push after that re-syncs. You never choose between the two — the CLI auto-detects which case applies
(from whether its non-secret push cache for that deployment is empty or populated) and there is a
single code path behind both (`core/web_adopt.run_push`). `remo web adopt` still exists as a
**deprecated alias**: it prints a one-line deprecation notice and then behaves exactly like
`remo web push`. It is scheduled for removal one release later — use `remo web push`.

Both commands live in the base CLI (`src/remo_cli/cli/web.py` → `src/remo_cli/core/web_adopt.py`,
stdlib HTTP only) — the `web` extra is **not** required on the workstation:

```text
remo web push  [URL] [--token TEXT] [--via HOST] [--allow-empty] [--yes] [--force]
remo web adopt [URL] [--token TEXT] [--via HOST] [--allow-empty] [--yes] [--force]   # DEPRECATED alias
remo web status [--deployment ID]
```

`--token` carries the **pairing code** (the option name is kept for
compatibility). Nothing is saved between runs — each push obtains a fresh
code from the page. `--force` re-scans and re-authorizes every direct-access instance (see
[Forcing a full re-authorization](#forcing-a-full-re-authorization---force)); `remo web status`
reports offline drift with no network access (see [`remo web status`](#remo-web-status-offline-drift)).

### The push flow (adopt on first use, re-sync afterwards)

**1. Deploy the container.** Via Compose (`docker compose --profile adopted up -d`, see
[Docker Compose deployment](#docker-compose-deployment)) or as an **hola app** — set
`REMO_WEB_OPERATOR_AUTH` (`forward` behind the hola app's SSO, plus `REMO_WEB_FORWARD_AUTH_HEADER`;
or `none` for a loopback/dev deployment) so the page can mint pairing codes. Within ~30 seconds the
container is up in the `unconfigured` state, has minted its service-scoped keypair, and the browser
shows the "awaiting adoption" page. (Bare-metal `remo web serve` on a writable home is also adoptable
now — see [How the service decides its mode](#how-the-service-decides-its-mode).)

**2. Copy the pairing code and run `remo web push`.** On the awaiting-adoption page (or, after the
first push, the dashboard's **Pair CLI to sync** affordance), click **Copy pairing code** — the code
lands on your clipboard and is never displayed. On the workstation, inputs resolve in this order:

| Input | Resolution order |
|---|---|
| Service URL | argument → `REMO_API_URL` env var → interactive prompt |
| Pairing code | `--token` → `REMO_API_TOKEN` env var → interactive prompt (hidden input) |

```bash
remo web push http://docker-host.lan:8080    # paste the code at the prompt
```

The flow is identical whether this is the first push (adoption) or the hundredth (re-sync): it
checks the service's state (aborting clearly if the target is mount-configured or the code is no
longer valid), verifies the payload version, fetches the service's public key and deployment id, and
— per direct-access instance, with a bounded per-instance time budget so one slow instance delays
only itself — `ssh-keyscan`s the host, verifies the scanned key against your own trusted
`~/.ssh/known_hosts` record (`ssh-keygen -F`, so hashed known_hosts files work; the service itself
**never** makes a trust-on-first-use decision), and installs the service's key into that instance's
`~/.ssh/authorized_keys` idempotently. Instances that are **unchanged since the last successful push**
(their registry entry matches the non-secret push cache for this `deployment_id`) skip that
keyscan/authorize work and reuse their cached host-key lines — reported `unchanged`. New or changed
instances get the full trust treatment. The flow finishes by pushing the full registry mirror,
best-effort revoking the service key on any removed instances (see
[Removing an instance revokes its access](#removing-an-instance-revokes-its-access)), triggering a
server-side verification pass, and rendering the report.

The `unchanged` fast path is an optimization, and **verification overrules it**. The push cache
records what this workstation last *sent*; only the service can observe whether its key is still
installed on an instance. So when the verification pass comes back `auth_failed` for an instance the
push skipped as `unchanged` — the service key was removed host-side, e.g. by a provisioning pass that
rewrote `authorized_keys` — that instance is re-keyscanned and re-authorized within the same run, the
mirror is re-pushed, and verification runs again, so the report you read reflects the repaired state.
Those instances are reported `repaired`. **A plain `remo web push` therefore fixes a host-side key
loss on its own**; you do not need `--force` for that case.

**3. Read the summary.** Every registry entry gets exactly one outcome line, each with a one-line
remediation where applicable:

| Outcome | Meaning |
|---|---|
| `adopted` | Host key verified and pushed; service key authorized on the instance. |
| `unchanged` | The instance matches the push cache from the last successful push — keyscan/authorization skipped, cached host-key lines reused. (Never appears on the very first push; `--force` bypasses it.) |
| `repaired` | The instance was skipped as `unchanged`, service-side verification then reported `auth_failed` for it, and the push re-keyscanned and re-authorized it. The service key had gone missing host-side; no action needed. |
| `skipped_unreachable` | Keyscan failed or timed out — instance down or unreachable from the workstation. Not fatal; re-run push when it's back. |
| `skipped_by_design` | SSM-routed instance (AWS-managed transport). No action needed — SSM instances are excluded from host-key and service-key push by design; see [Credentials and SSM](#credentials-and-ssm). |
| `skipped_no_trust` | Your workstation has no trusted host-key record and the run was non-interactive (`--yes`), so nothing was pushed. Interactively, you're prompted to confirm the SHA256 fingerprint instead. |
| `security_flagged` | **The scanned host key does not match your workstation's trusted record.** Rendered prominently as a potential MITM warning; nothing is pushed for that instance and the rest of the run continues. Investigate before trusting; if the instance was legitimately rebuilt, `ssh-keygen -R <host>`, reconnect once to re-trust it, then re-run the push. |

Removed instances (in the last push but no longer in the registry) get their own **Revocation**
block below the summary — see
[Removing an instance revokes its access](#removing-an-instance-revokes-its-access).

**4. Read the verification report.** The service then re-checks itself and every pushed instance
(`remo-host capabilities` round-trips over its *own* identity) and the CLI renders the per-instance
PASS/FAIL lines. One outcome deserves a special mention: an instance the CLI just reached but the
service cannot is annotated **"reachable from workstation but not from the service"** — an
asymmetric-network case (e.g. the instance is only reachable via workstation-specific SSH client
config such as ProxyJump, or a firewall between the container host and the instance), not a
push failure.

**5. No saved credentials.** Nothing durable is saved (there is no long-lived secret to save). Every
push obtains a fresh pairing code the same way. The workstation keeps only a **non-secret** push
cache at `~/.config/remo/web-service.json` (mode `0600`, `cache_version: 3`): per service
`deployment_id`, the mirror generation it last observed (for flap detection) plus, per instance, a
host-key fingerprint, the verified host-key lines, and a non-secret connection tuple
(host/user/access/type/port). The fingerprint drives the `unchanged` fast path and the offline
`remo web status` diff; the connection tuple lets a later push reach a *removed* instance for
revocation. **No URL and no pairing code are ever stored.** A pre-017 cache (any version other than
`3`) is treated as empty, forcing one full re-verification push after upgrade.

The command exits `0` when the flow completes — per-instance skips/flags and revocation failures are
reported in the summary, not fatal — and `1` only on hard failure (dormant setup surface / expired
code, mount-configured target, empty registry without `--allow-empty`, tunnel failure, payload
rejected, or a flap declined interactively). Re-running the push (with a fresh code) is idempotent:
same summary, zero changes, still exactly one `remo-web@` line per instance.

### `remo web status` (offline drift)

Between pushes your local registry drifts from what the deployment last mirrored — you create,
destroy, or change instances. `remo web status` shows that drift **entirely offline**: it compares
the current registry against the non-secret push cache and reports each instance as `new`, `changed`,
`removed`, or `in sync`, making **zero** network or SSH connections (typically < 2s). It never
contacts the service, so it works with the deployment offline or unreachable.

```bash
remo web status
```

- **Deployment selection.** When the cache records exactly one deployment, status reports against it
  implicitly. When it records more than one, pass `--deployment <id>` to choose (the reported-against
  deployment id is always shown in the output).
- **Exit codes.** Exits `0` in all normal cases — it is informational, so it exits `0` even when
  drift exists. It exits `1` only when more than one deployment is cached and no `--deployment`
  selector was given (the error lists the known deployment ids).
- **Friendly edge cases.** "No prior push recorded from this workstation" (the cache is empty) and
  "In sync — nothing to push" (the registry matches the last push) are explicit, non-error outcomes.

### The out-of-date nudge

So drift doesn't stay invisible until a terminal fails to open, any registry-mutating CLI command
prints a single-line reminder **when — and only when — a push cache exists**:

```text
Your web deployment may now be out of date — run 'remo web status' to see what changed, or
'remo web push' to re-sync.
```

It fires after a successful `remo <provider> create`/`destroy`, an **applied** `remo <provider> sync`
(the shared reconcile/`run_sync` path), `remo add`, and `remo remove`. It never fires when this
workstation has never pushed (no cache to be out of date against), on a dry-run or no-op `sync`, or
on `remo aws stop`/`start`/`reboot` (which don't mutate the registry). The nudge is gated on cache
existence only — a rare false positive after a no-op mutation is acceptable because `remo web status`
is the cheap, authoritative follow-up.

### Removing an instance revokes its access

The push is an exact mirror: the workstation registry is the source of truth, so an instance you
removed locally disappears from the service's registry, the dashboard, and discovery, and no new
sessions can target it. Beyond that, the push now makes a **best-effort** attempt to strip the
service's `remo-web@` line from that instance's `~/.ssh/authorized_keys`, using **your own ambient SSH
access** (never the service identity) — the marker-scoped, atomic, idempotent edit that adoption uses
to install the key, run in reverse. Only the `remo-web@` line is removed; all your other authorized
keys are untouched, and re-running is a no-op.

Each removed instance is reported in the push's **Revocation** block as one of:

| Result | Meaning |
|---|---|
| `revoked` | The `remo-web@` line was removed from that instance's `authorized_keys`. |
| `could_not_revoke` | Revocation couldn't be performed — the instance is unreachable, you no longer have SSH access, it is SSM-routed (AWS-managed transport, no ambient SSH path), or the cache holds no connection details for it (an older cache). The line is reported with manual-removal remediation. |

Revocation is **never fatal**: the push still exits `0` even when every removal reports
`could_not_revoke`. For those, remove the service's line manually — on the instance (or via
`remo shell`), delete its single marker line, leaving your own access untouched:

```bash
sed -i '/ remo-web@/d' ~/.ssh/authorized_keys
```

Every entry the flow installs carries the `remo-web@<deployment-id>` comment marker, so it is always
exactly one identifiable line (`grep remo-web@ ~/.ssh/authorized_keys` to audit).

### Forcing a full re-authorization (`--force`)

`remo web push --force` (also on the deprecated `adopt` alias) bypasses the fingerprint `unchanged`
fast path and re-scans host keys and re-authorizes the service key on **every** direct-access
instance, exactly as a first push would. `--force` changes only *which* instances are processed, not
how failures are classified: per-instance skips/flags stay non-fatal.

You should rarely need it. An instance rebuilt **out of band** — same registry entry, brand-new host
keys and a wiped `authorized_keys` — is caught by the verification-driven repair described above and
reported `repaired`, because the service fails to authenticate to it and the push acts on that.
Reach for `--force` when the repair itself didn't take (the verification report names the exact
command in that case), or when you want every instance re-scanned regardless of what verification
saw — for example after rotating host keys across a fleet.

### Multi-workstation flap detection

If you push to the same deployment from more than one workstation, the deployment reports a
**mirror-identity marker** on `GET /api/v1/setup/status` (and updates it on each `PUT /setup/registry`
apply): a monotonic generation counter plus a best-effort last-push descriptor (a timestamp and a
"hostname/user" label). The marker exposes no secret and no instance contents, and is served only
over the already-pairing-gated setup surface.

Each push records the generation it last wrote in its push cache. When you push from workstation B and
the deployment's generation is **greater** than what B last recorded — i.e. the mirror was advanced by
another workstation since B's last push — the push **warns**, naming when/where the last push came
from (to the extent that information is available), before overwriting:

- **Interactive** run: you're prompted to confirm or abort before the overwrite (aborting exits `1`).
- **`--yes`** run: the warning is printed and the push proceeds (so automation isn't deadlocked).

A first-ever push to a fresh deployment, and consecutive pushes from the same workstation with no
intervening external push, never warn.

### The setup API and pairing codes

The CLI talks to four endpoints under `/api/v1/setup/*`
([`setup-api.md`](../specs/012-web-adopt-pairing/contracts/setup-api.md)): `GET /status`,
`GET /identity`, `PUT /registry`, `POST /verify`. The surface is **dormant** unless a pairing session
is live; each route requires `Authorization: Bearer <pairing-code>`, compared in constant time:

- **No live session → the setup surface does not exist.** Every `/api/v1/setup/*` request gets a plain
  `404`, indistinguishable from an absent feature — fail closed. A session is live only while an
  operator is on the awaiting-adoption page (or the dashboard re-sync affordance).
- **Wrong/missing/expired code → the same dormant `404`** — never a distinguishable `401` that would
  reveal a session exists. The attempt is logged without the presented code; codes and `Authorization`
  headers are covered by the service's log redaction (`src/remo_cli/web/logging_config.py`).
- **The session ends when the flow completes** (on the terminal `POST /verify`), and a code is
  single-use per handoff — reopening the page mints a fresh one and invalidates the prior. There is no
  rotation to manage: codes are ephemeral by construction.

### Service key rotation

There is no dedicated rotation command in v1; rotation is a documented state-reset procedure:

1. **Reset the state volume** — e.g. `docker compose --profile adopted down` then
   `docker volume rm <project>_remo-web-state` (or delete the hola app's volume).
2. **Restart the container.** It boots `unconfigured` and mints a **new** identity (new keypair, new
   `deployment_id`).
3. **Re-run `remo web push`.** The new `deployment_id` starts with an empty push cache, so this push
   adopts from scratch. Because the `authorized_keys` management filters on the ` remo-web@` marker
   rather than the key material, the stale entry from the old identity is *replaced*, not accumulated
   — each instance in the current registry again ends up with exactly one service line.

One caveat: instances **removed from your registry before the rotation** never get visited by the
re-adopting push, so the *old* identity's entry lingers there — clean those up with the manual
de-authorization procedure above. The old private key is gone with the volume, so the stale entries
are inert, but hygiene says remove them.

### Tunnel fallback: `--via <host>`

When the service URL isn't directly reachable from the workstation (loopback-only port publish,
firewalled segment, a reverse proxy in the way for the setup calls), tunnel the push over your
existing SSH access to the deployment host:

```bash
remo web push --via docker-host.lan
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
| `no_remo_host` | The instance answered but has no `remo-host` command installed. | Not retryable as-is — re-run the instance's configure/upgrade flow (see [Upgrade compatibility](#upgrade-compatibility)) to install it. |
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
| `instance <type>/<name>` FAIL — `no_remo_host` | That specific instance predates the `remo-host` rollout, or its install failed. | Re-run that instance's configure/upgrade flow — see [Upgrade compatibility](#upgrade-compatibility). |
| `instance <type>/<name>` FAIL — unreachable / timeout | Network path or instance state issue, isolated to that one instance. | Confirm the instance is running and reachable from the Docker host; other instances are unaffected. |

### Adoption issues

| Failure | What it means | Fix |
|---|---|---|
| `/api/v1/setup/*` returns `404` for everything | No pairing session is live — the surface is dormant (fail closed). | Open the awaiting-adoption page (through your SSO proxy) to mint a code; if the page can't mint, set `REMO_WEB_OPERATOR_AUTH` (`forward` + header, or `none` for loopback). |
| `remo web push` (or the deprecated `adopt`) fails: "pairing code is no longer valid … dormant" | The code expired (idle TTL), was rotated by reopening the page, or was already used. | Reopen the awaiting-adoption page (or the dashboard's "Pair CLI to sync" affordance) for a fresh code and retry. |
| Mint page shows "you are not signed in" / `POST /pairing/mint` returns `403` | Forward auth is required but the request reached the service without the trusted identity header. | Ensure the request goes through the SSO proxy that injects `REMO_WEB_FORWARD_AUTH_HEADER`; verify the proxy sets and strips it. |
| adopt fails: deployment "configured via read-only mounts" | The target is a bind-mount deployment (`mount_configured`) — its configuration is operator-provided and read-only, so adoption does not apply. | Update the mounted files instead, or deploy the adopted-mode service (writable state volume, no mounts) if you want adoption. |
| adopt refuses: empty registry | Your local registry has no instances — pushing would wipe a previously adopted service (a classic wrong-workstation accident). | Register/sync instances first, or pass `--allow-empty` if wiping is intentional. |
| `--via` fails naming `REMO_WEB_ALLOWED_HOSTS` | Tunneled requests arrive with a `127.0.0.1` Host header, which the service's Host allowlist rejects. | Add `127.0.0.1` to `REMO_WEB_ALLOWED_HOSTS` (the default includes it). |
| After a service state-volume reset, instances keep a stale `remo-web@` line | The reset service minted a new identity; a fresh `remo web push` authorizes the new key but does not remove the old-identity line from an instance no longer in the registry. | Re-run `remo web push` (revokes/replaces the marker on every current instance), then delete any stale `remo-web@<old-id>` line from instances you had already removed from the registry. |
| Summary line `security_flagged` (potential MITM warning) | The instance's scanned host key doesn't match your workstation's trusted record; nothing was pushed for it. | Investigate before trusting. If the instance was legitimately rebuilt: `ssh-keygen -R <host>`, reconnect once to re-trust, re-run the push. |
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
and the `upgrade` flow for every provider, so re-running:

```bash
remo aws upgrade <name>        # or: remo hetzner upgrade / remo incus upgrade / remo proxmox upgrade
```

against the affected instance re-templates and reinstalls `remo-host` in place — no full recreate is
needed, and the upgrade is idempotent (safe to run repeatedly, on both fresh and already-configured
hosts).

**Git status glyphs require this re-provision.** Per-project git status (`git_tracked`/`git_dirty`/
`git_ahead`/`git_behind`) was added to `remo-host` as additive, backward-compatible protocol-1 fields.
An instance still running the older `remo-host` simply omits them and the console shows no git glyphs
for its projects — nothing breaks. Run the `upgrade` command above for each instance (e.g.
`remo proxmox upgrade dev1`) to start reporting git status.

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
| `REMO_WEB_DISCOVERY_CACHE_TTL_S` | `30.0` | How long a discovery snapshot is served from cache before the console's background poll is allowed to re-run discovery. This — not the console's poll interval, and not how many browser tabs are open — is what sets how often instances are actually contacted. An explicit refresh (`POST /api/v1/discovery/refresh` with the default `force: true`, which is what the Refresh button and the post-terminal-exit refresh send) bypasses it. |
| `REMO_WEB_TERMINAL_CAP_GLOBAL` | `32` | Maximum concurrent terminal attachments across all clients. |
| `REMO_WEB_TERMINAL_CAP_PER_CLIENT` | `16` | Maximum concurrent terminal attachments for a single client. |
| `REMO_WEB_WS_TOKEN_TTL_S` | `30.0` | Seconds a single-use WebSocket terminal token remains valid between issuance and successful upgrade. |
| `REMO_WEB_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Comma-separated allowlist for the HTTP `Host` header on state-changing requests and the WS handshake. No wildcard is supported — set this explicitly for any real deployment. |
| `REMO_WEB_ALLOWED_ORIGINS` | `http://127.0.0.1:8080,http://localhost:8080` | Comma-separated allowlist for the `Origin` header on state-changing requests and the WS handshake. No wildcard CORS. |
| `REMO_WEB_SSH_CONTROL_DIR` | `/run/remo-ssh` | Writable directory for SSH ControlMaster sockets (must be tmpfs or otherwise writable under a read-only rootfs). |
| `REMO_WEB_FRONTEND_DIST_DIR` | `<repo_root>/frontend/dist` (resolved relative to the installed package) | Directory the built frontend SPA is served from. The Docker image overrides this to `/app/frontend-dist`, matching where the multi-stage build actually copies the built assets. |
| `REMO_WEB_SSH_IDENTITY_FILE` | *(unset — falls back to the service keypair under `web-identity/`, then `~/.ssh/id_ed25519`/`id_ecdsa`/`id_rsa`/`id_dsa`)* | Explicit path to the SSH private key used for readiness/`remo web check`'s identity check, when it isn't one of the conventional filenames. |
| `REMO_WEB_MODE` | *(unset — derived from disk)* | Deterministic override for the service's configuration mode: `adopted` or `mount_configured`. Honored **after** the `broken` guards (an unreadable artifact or half-pair keypair still wins as `broken`) and before the writable/keypair heuristic; an invalid value is a fail-fast startup error. Left unset, the mode is derived from disk (a non-writable `REMO_HOME` = `mount_configured`, a service keypair on a writable `REMO_HOME` = `adopted`). Most useful to pin a bare-metal `remo web serve` to `adopted`. See [How the service decides its mode](#how-the-service-decides-its-mode). |
| `REMO_WEB_OPERATOR_AUTH` | *(unset — minting disabled)* | Operator-authentication posture gating pairing-code minting (`POST /api/v1/pairing/mint`). `forward` requires a trusted proxy-injected identity header (`REMO_WEB_FORWARD_AUTH_HEADER`); `none` mints without operator auth (network-restricted — a loud, weaker posture for loopback/dev). While unset, minting is disabled and adoption is impossible (fail closed). |
| `REMO_WEB_FORWARD_AUTH_HEADER` | *(unset)* | Name of the trusted identity header your forward-auth proxy injects (e.g. `X-Forwarded-User`, `Remote-User`). **Required** when `REMO_WEB_OPERATOR_AUTH=forward`; enabling forward auth without it is a fail-fast startup error. The proxy MUST set and strip this header. |
| `REMO_WEB_PAIRING_TTL_S` | `900.0` | Sliding idle TTL (seconds) for a pairing session — it expires this long after the last successful setup call (default 15 min). |
| `REMO_WEB_API_TOKEN` | *(removed — ignored)* | **Removed in 012.** The static setup-API token is gone; a value set here is ignored (a one-line "now ignored" note is logged at startup). Setup access is authorized by ephemeral pairing codes. |

`remo web serve --host`/`--port` are convenience overrides for local runs; every other setting is env-var-only.

### Workstation-side environment variables

Two variables configure the **CLI** (not the service — hence no `REMO_WEB_` prefix), read by
`remo web push` (and the deprecated `remo web adopt` alias):

| Variable | Used as |
|---|---|
| `REMO_API_URL` | Service URL fallback when no URL argument is given (before falling back to an interactive prompt). |
| `REMO_API_TOKEN` | Pairing-code fallback when `--token` is not given (before falling back to a hidden interactive prompt). Set it to a code freshly minted from the awaiting-adoption (or dashboard re-sync) page. |
