# Speckit Specify Input: Remo Web Session Interface

Use the contents of this document as the feature description for Speckit's
`specify` prompt. Preserve the decisions marked **Required decision** when
generating the specification and the later implementation plan.

## Feature Summary

Build a web interface that accompanies the Remo CLI and provides browser-based
terminal access to project sessions spread across all Remo-managed instances.
The application runs as a home-lab Docker service in the same network
environment in which the Remo CLI would run. It reads a read-only mount of
Remo's `~/.config/remo/known_hosts` registry, connects from the server to the
registered instances over SSH using the same provider-aware connection behavior
as the CLI, discovers each instance's projects and session state, and lets the
user open and rapidly switch among many interactive terminal sessions.

The motivating example is a user with three registered Remo instances—one
Proxmox, one AWS, and one Hetzner—with three devcontainer projects on each. The
user must be able to discover all nine session targets, open all nine at once,
view them in a grid or as tabs, focus any one of them, and switch among them
without returning to a local terminal or running `remo shell`.

The MVP is for one trusted user on a trusted LAN or VPN, normally reached over
Tailscale. It focuses on discovery and browser terminal access. Project cloning,
project lifecycle actions, instance lifecycle actions, port forwarding, and
other management capabilities are not part of this MVP, but the contracts must
be designed so those actions can be added to both the CLI and web interface
without replacing the discovery or connectivity architecture.

## Motivation

Remo currently assumes that the user has a local terminal from which they can
run `remo shell`. That is not always true: the user may be on a tablet, a locked
down workstation, or any browser-capable device without the Remo CLI and its SSH
configuration installed. Remo's remote hosts already contain persistent Zellij
project sessions and know how to start or enter devcontainers, so the missing
piece is a central, browser-accessible terminal broker and session dashboard.

The web application must preserve Remo's existing behavior rather than create a
second, subtly different devcontainer launcher. A project opened in the browser
and the same project opened with `remo shell -p <project>` must enter the same
remote Zellij session and the same devcontainer environment.

## Terms and Definitions

| Term | Definition |
|---|---|
| **Remo registry** | The existing colon-delimited `~/.config/remo/known_hosts` file containing provider type, Remo name, address, SSH user, provider identity/access mode, and region. It is not the OpenSSH `known_hosts` file and does not contain credentials. |
| **Instance** | One SSH-reachable Remo environment represented by a `KnownHost`, regardless of whether the provider calls it an AWS instance, Hetzner server, Incus container, or Proxmox container. |
| **Project** | A directory beneath the Remo user's `~/projects` directory on an instance. It normally contains a devcontainer definition and is already selectable through `project-menu`. |
| **Session target** | The stable pair `(instance, project)` that can be opened in an interactive terminal. Its opaque public identifier must not expose a command or permit an arbitrary destination. |
| **Remote session** | The Zellij session named for the project on the selected instance. For a devcontainer project, its shell runs inside the project's devcontainer according to existing Remo behavior. |
| **Browser terminal** | One Ghostty Web terminal component connected by WebSocket to one server-side PTY and SSH attach process. |
| **Terminal attachment** | The ephemeral web-server-side PTY/SSH process that attaches a browser terminal to a remote session. The attachment may end while the remote Zellij session continues. |
| **`remo-host`** | A new, versioned, non-daemon command installed on every Remo instance. It exposes structured discovery and explicit session/project operations over SSH. It listens on no port and runs only when invoked. |
| **Web service** | The home-lab Docker service that serves the UI and API, owns browser WebSockets and PTYs, reads the registry, and initiates SSH connections. |

## Required Architectural Decisions

### 1. Keep the feature in the Remo repository and CLI family

**Required decision:** Implement this in the existing Remo repository as a
separately packaged `remo-web` OCI image and a `remo web` Click command group.
The normal CLI installation must not acquire the web server's runtime
dependencies. Web dependencies belong in an optional package extra, and the
web service code must be imported lazily in the same spirit as the existing
notifier service.

The expected command surface for the MVP is:

```text
remo web serve       # run the web service locally or as the container entry point
remo web check       # validate registry, SSH material, remote capabilities, and reachability
```

A future `remo web deploy` command may automate home-lab deployment, but a
Docker Compose example is sufficient for the MVP.

### 2. Use SSH as the host transport; do not add a resident agent

**Required decision:** Do not add a daemon, HTTP listener, or new open port to
each Remo instance. The web service must use OpenSSH for both structured remote
commands and interactive terminal attachments. This preserves direct SSH and
AWS SSM access behavior and uses the connectivity Remo already provisions.

The web service must reuse or refactor shared logic from `core/ssh.py`, including
`KnownHost` resolution, direct versus SSM targeting, region/profile behavior,
safe remote command construction, and SSH multiplexing. It must not shell out to
the top-level interactive `remo shell` command or parse its human-facing output.

### 3. Add a versioned `remo-host` command on every instance

**Required decision:** Add `~/.local/bin/remo-host`, installed and updated by the
existing Ansible host-configuration flow. It is a command, not a service.

The MVP command contract is:

```text
remo-host capabilities --json
remo-host sessions list --json
remo-host sessions attach --project <name>
```

The helper must:

- emit schema-versioned JSON with no ANSI sequences or explanatory text on
  stdout for JSON commands;
- report its protocol version and supported operations;
- list projects under the configured projects directory;
- report whether each project has a devcontainer configuration;
- report whether its Zellij session is active, absent, or exited;
- report whether its devcontainer is running when that state can be determined;
- validate project names and refuse traversal or arbitrary paths;
- return stable, documented exit codes and machine-readable errors;
- make `sessions attach` preserve existing `project-launch` behavior, initially
  by delegating to that script if appropriate;
- be extensible with explicit future verbs such as `projects clone`,
  `projects delete`, and `sessions stop` without accepting an arbitrary shell
  command as an API operation.

For parity with the current project menu, every project directory remains an
eligible session target. Projects without a devcontainer configuration must be
clearly marked and follow the existing plain project/Zellij behavior; discovery
must not pretend that they are devcontainer environments.

The CLI-side/client-side representation of this protocol must live in shared
Remo code usable by both `remo shell` and the web service. The protocol schema
must be documented and compatibility-tested. If an instance is too old or does
not have `remo-host`, the UI must show a clear, per-instance update instruction
instead of silently omitting it or falling back to scraping `project-menu`.

### 4. Use one server-side PTY and SSH attachment per browser terminal

**Required decision:** The browser never connects directly to SSH. For each
opened terminal, the web service creates a Unix PTY, runs an SSH client attached
to that PTY, forces remote TTY allocation, and invokes the explicit remote
attach operation for the selected instance/project. PTY bytes are streamed to
and from the browser over a WebSocket.

The PTY path must support:

- arbitrary byte-safe terminal output;
- browser input and bracketed paste;
- terminal resize propagation using the PTY window-size operation;
- orderly SSH process termination when the browser terminal closes;
- cancellation and cleanup if connection setup fails;
- output backpressure so a noisy terminal cannot grow memory without bound;
- separate failure reporting for authentication, network, remote capability,
  missing project, and remote launch failures;
- `TERM=xterm-256color` or another deliberately supported terminfo value rather
  than advertising a terminal type unavailable on the remote instance.

Losing the browser WebSocket or killing the local SSH attachment must not kill
the remote Zellij session. Reconnection creates a new attachment to the same
Zellij session. A browser tab kept open but hidden remains connected and retains
its browser scrollback; browser scrollback persistence across a page reload is
not required in the MVP.

### 5. Multiplex SSH connections by instance

**Required decision:** Concurrent terminal attachments to the same instance
must reuse an OpenSSH ControlMaster connection. Nine sessions across three
instances should ordinarily result in three SSH masters and nine logical
attachments, not nine complete authentication handshakes.

Refactor the existing hard-coded ControlPath so the service can place sockets
in a writable tmpfs such as `/run/remo-ssh`, while SSH keys and configuration
remain read-only. Multiplexing must be scoped safely by user, host, and port,
clean up stale sockets, and degrade to a clear reconnect if a master dies.

### 6. Use Ghostty Web behind a small adapter

**Required decision:** Use `ghostty-web` as the default browser terminal
emulator for the MVP. It provides a Ghostty-derived WASM VT parser, an
xterm.js-like API, fit/resize behavior, selection, clipboard handling, links,
IME support, and mobile support. Its demo provides a useful reference for the
WebSocket/PTY browser contract.

Do not couple application state directly to Ghostty Web classes. Define a small
frontend terminal-renderer adapter covering create/open, write, input events,
resize, focus, dispose, title changes, and selection/copy. Pin the dependency
and its WASM asset. This keeps an xterm.js fallback possible because Ghostty Web
is still pre-1.0 and currently builds against a patched Ghostty/libghostty
surface.

References to verify during planning:

- <https://github.com/coder/ghostty-web>
- <https://github.com/coder/ghostty-web/tree/main/demo>
- <https://github.com/coder/ghostty-web/blob/main/CHANGELOG.md>

As of the original feature discussion on 2026-07-13, the latest tagged release
was 0.4.0 from 2025-12-09. The plan must reverify the current compatible version
before locking dependencies.

### 7. Treat the trusted network as the MVP access boundary

**Required decision:** The MVP is single-user and intended only for a trusted
LAN/tailnet. It does not include local user accounts, OIDC, or multi-user
authorization. Deployment documentation must prominently state that the
service grants shell access to every configured instance and must not be
exposed to an untrusted network.

Trusted-network deployment does not remove browser security requirements. The
service must still validate allowed `Host` values and WebSocket `Origin`, use a
short-lived single-purpose token to authorize each terminal WebSocket, prevent
token replay after use or expiry, avoid permissive CORS, and support operation
behind a same-origin reverse proxy. A later authentication layer must be
addable without changing the terminal protocol.

## User Scenarios and Acceptance Testing

### User Story 1: Discover every available session target (Priority P1)

The user opens the Remo web application and sees projects from every registered
and reachable instance, grouped by provider and instance. Each project shows
whether a Zellij session is active and whether its devcontainer is running.
Unreachable or outdated instances remain visible with an actionable status.

**Why this priority:** A central view across providers is the primary value that
does not exist in `remo shell` today.

**Independent test:** Mount a registry containing three instances with three
projects each, make all three reachable, load the UI, and verify that all nine
targets appear with the correct grouping and state without opening a terminal.

**Acceptance scenarios:**

1. **Given** a mounted registry with reachable Proxmox, AWS, and Hetzner
   instances, **when** the dashboard loads, **then** it discovers projects on all
   three concurrently and displays the combined results.
2. **Given** one unreachable instance, **when** discovery runs, **then** projects
   from reachable instances appear without waiting for the unreachable one's
   full failure lifecycle, and the unreachable instance displays a retryable
   error.
3. **Given** the mounted registry changes, **when** the user refreshes discovery
   or the configured refresh interval elapses, **then** added and removed
   instances are reflected without restarting the container.
4. **Given** an instance lacks the required remote protocol version, **when** it
   is queried, **then** the UI identifies the incompatibility and names the
   Remo update action required to fix it.

### User Story 2: Open a browser terminal into a project (Priority P1)

The user selects a project and receives an interactive browser terminal attached
to that project's normal Remo session. If the Zellij session or devcontainer is
not running, existing Remo launch behavior creates/starts it while streaming
progress into the same terminal. If it is already running, the browser attaches
to it.

**Why this priority:** Browser terminal access is the feature's core outcome.

**Independent test:** From a browser with no local CLI, open one stopped
devcontainer project, observe startup, obtain a shell inside the container, run
a command, disconnect, reconnect, and observe the same Zellij session.

**Acceptance scenarios:**

1. **Given** an existing active project session, **when** the user opens it,
   **then** the browser attaches to that same Zellij session and input/output is
   interactive.
2. **Given** a project whose devcontainer is stopped, **when** the user opens it,
   **then** startup output is streamed and the final shell is inside the
   project's devcontainer, matching `remo shell -p <project>`.
3. **Given** the browser connection drops, **when** the user reconnects, **then**
   a fresh terminal attachment reaches the still-running remote Zellij session.
4. **Given** the SSH authentication or remote launch fails, **when** the terminal
   is opened, **then** the UI shows the specific failure and provides retry
   without leaving an orphaned PTY or SSH process.

### User Story 3: Open and switch among many sessions (Priority P1)

The user can open several or all discovered targets. The workspace offers a
terminal grid, tabbed/focused view, and keyboard-friendly switching. Each open
terminal stays independently connected while the user views another one.

**Why this priority:** The intended advantage over repeatedly invoking
`remo shell` is rapid multi-instance context switching.

**Independent test:** Open all nine targets in the three-instance example,
interact with each, switch repeatedly through grid and focused modes, and verify
that output and input stay routed to the correct terminal.

**Acceptance scenarios:**

1. **Given** nine discovered targets, **when** the user chooses "Open all",
   **then** nine terminal cards are created and connect independently with
   per-terminal progress and error state.
2. **Given** multiple open terminals, **when** one is focused or selected by a
   keyboard shortcut, **then** keyboard input is sent only to that terminal.
3. **Given** one terminal disconnects, **when** the others remain healthy,
   **then** they continue without interruption and only the failed terminal
   shows reconnect controls.
4. **Given** terminals from different instances have similar project names,
   **when** the user switches among them, **then** provider, instance, and
   project identity are always visible and output is never cross-routed.

### User Story 4: Install as a home-lab Docker service (Priority P1)

The user installs the web application using a documented Docker Compose example,
mounts their Remo registry and dedicated SSH material read-only, supplies an
appropriate network route to the registered instances, and accesses the service
through their LAN or Tailscale environment.

**Why this priority:** The browser cannot provide value unless the service is
easy and safe to operate continuously in a home lab.

**Independent test:** On a clean amd64 or arm64 Linux Docker host, follow only
the documented Compose instructions, run the readiness check, and open a remote
project terminal from another tailnet device.

**Acceptance scenarios:**

1. **Given** a valid registry and SSH identity, **when** the container starts,
   **then** its health endpoint becomes ready and `remo web check` validates the
   configuration.
2. **Given** only the Remo registry is mounted, **when** the service starts,
   **then** it fails readiness with a clear explanation that registry metadata
   is not SSH authentication material.
3. **Given** an AWS entry using SSM access mode, **when** its required AWS
   credentials/profile are present, **then** discovery and terminal attachment
   follow the same SSM route as the CLI.
4. **Given** the container uses a read-only root filesystem, **when** terminals
   are opened, **then** runtime state is confined to declared tmpfs/writable
   mounts and no credentials are copied into the image or written to logs.

### User Story 5: Preserve CLI behavior and compatibility (Priority P2)

The user continues to use `remo shell` normally after upgrading hosts for web
support. The CLI and web service share session identities, connection builders,
remote protocol parsing, and safe validation instead of drifting into separate
implementations.

**Independent test:** Open the same project from the web interface and from
`remo shell -p`, confirm both attach to the same Zellij session, and run existing
CLI tests unchanged except for deliberate shared-core refactors.

**Acceptance scenarios:**

1. **Given** an upgraded instance, **when** the user runs existing `remo shell`
   commands, **then** all existing options and behavior continue to work.
2. **Given** a project opened in the web UI, **when** the user later invokes
   `remo shell <host> -p <project>`, **then** it attaches to the same remote
   session.
3. **Given** a project name containing supported spaces or punctuation, **when**
   it is opened through either surface, **then** shared validation and quoting
   prevent command injection and identify the same project.

## Functional Requirements

### Registry and host access

- **FR-001:** The service MUST read the existing Remo registry through
  `REMO_HOME`/the established config-path rules and MUST support mounting that
  registry read-only.
- **FR-002:** The service MUST treat `(provider type, Remo name)` as the stable
  instance identity and use opaque public IDs in browser/API operations.
- **FR-003:** The service MUST support every access mode supported by
  `build_ssh_opts`, including direct SSH and AWS SSM proxying.
- **FR-004:** The service MUST load registry changes without process restart and
  MUST never mutate the mounted registry in the MVP.
- **FR-005:** Discovery MUST run concurrently across instances with configurable
  concurrency, connection timeout, command timeout, cache duration, and manual
  refresh.
- **FR-006:** A host's failure MUST be isolated from every other host and MUST be
  represented as typed status rather than an empty successful result.

### Remote command protocol

- **FR-007:** The standard host configuration flow MUST install `remo-host`
  idempotently and MUST update it when Remo host tools are updated.
- **FR-008:** `remo-host` MUST implement a documented, versioned JSON protocol
  for capabilities and session discovery.
- **FR-009:** A discovery result MUST include instance protocol version and, for
  each project, name, devcontainer presence, Zellij session state, and
  devcontainer running state when available.
- **FR-010:** Discovery MUST be read-only: it MUST NOT start a devcontainer,
  create a Zellij session, perform `git fetch`, or modify project state.
- **FR-011:** Attach MUST validate a project against the configured projects
  directory and MUST refuse absolute paths, parent traversal, control
  characters, or projects that no longer exist.
- **FR-012:** Machine-readable commands MUST reserve stdout for their defined
  payload, use stderr for diagnostics, and return documented exit codes.
- **FR-013:** The client MUST reject unsupported protocol versions and malformed
  payloads with actionable errors, and MUST limit payload size.
- **FR-014:** Future explicit project-management verbs MUST be addable without
  expanding the protocol into arbitrary command execution.

### Terminal lifecycle and routing

- **FR-015:** Creating a terminal attachment MUST require a currently discovered
  instance/project target; browser requests MUST NOT supply raw SSH targets,
  users, remote paths, SSH options, or commands.
- **FR-016:** Each attachment MUST use a PTY-backed SSH process and force a
  remote TTY for the existing Zellij/devcontainer flow.
- **FR-017:** The WebSocket protocol MUST distinguish byte-oriented PTY data from
  structured control messages. Binary frames SHOULD carry PTY data and text
  frames SHOULD carry versioned JSON control messages such as resize, ready,
  exit, and error.
- **FR-018:** Browser-to-PTY input, PTY-to-browser output, and resize events MUST
  be streamed bidirectionally and independently per terminal.
- **FR-019:** Closing a browser terminal MUST terminate and reap its PTY/SSH
  attachment without intentionally terminating the remote Zellij session.
- **FR-020:** A WebSocket disconnect MUST trigger bounded cleanup, while a user
  reconnect MUST be able to create a new attachment to the same session target.
- **FR-021:** The web service MUST apply bounded queues/backpressure and a configurable
  per-terminal output limit so stalled browser clients cannot consume unbounded
  memory.
- **FR-022:** The web service MUST support configurable global and per-client terminal
  limits and reject excess attachments clearly.
- **FR-023:** Terminal exit and setup errors MUST be classified and surfaced to
  the correct terminal only; a failure MUST never be rendered in another
  terminal's stream.

### SSH multiplexing and credentials

- **FR-024:** Attachments to the same SSH destination MUST support a shared
  ControlMaster, with its ControlPath configurable to a writable runtime
  directory.
- **FR-025:** The service MUST use a dedicated non-root runtime user, read-only
  SSH identity/config/host-trust mounts, strict host-key verification for direct
  SSH, and non-interactive authentication (`BatchMode` or equivalent). It MUST
  NOT require the Docker socket.
- **FR-026:** The Compose documentation MUST distinguish the Remo registry from
  OpenSSH host trust and identity material and document all required mounts.
- **FR-027:** AWS SSM support MUST include the required AWS CLI and Session
  Manager Plugin runtime and a documented read-only credential/profile or
  workload-identity configuration.
- **FR-028:** SSH secrets, AWS secrets, proxy commands, and WebSocket tokens MUST
  be redacted from application logs and browser-visible error details.

### Web UI

- **FR-029:** The dashboard MUST group session targets by provider and instance
  and show reachability, compatibility, Zellij state, and devcontainer state.
- **FR-030:** Users MUST be able to open one target, all targets on an instance,
  a selected set, or all discovered targets.
- **FR-031:** The terminal workspace MUST offer grid, tab/focused, and rapid
  keyboard switching modes without disconnecting hidden terminals.
- **FR-032:** Every terminal surface MUST display provider, instance, project,
  connection state, and controls for focus, reconnect, and close.
- **FR-033:** The UI MUST remain functional on current desktop and tablet
  browsers and provide basic mobile keyboard/input operation.
- **FR-034:** Workspace layout and display preferences MAY be stored in browser
  local storage; no server-side database is required for the MVP.
- **FR-035:** Discovery and terminal state MUST update incrementally so one slow
  instance or terminal does not block the rest of the interface.

### Ghostty Web integration

- **FR-036:** Ghostty Web MUST be loaded through a Remo-owned renderer adapter,
  not referenced throughout general application state.
- **FR-037:** The adapter MUST cover initialization, write, input events, fit and
  resize, focus, title, selection/copy, and disposal.
- **FR-038:** The build MUST serve the WASM asset with the correct content type
  and same-origin/CSP behavior and MUST not depend on a public CDN at runtime.
- **FR-039:** Terminal compatibility MUST be verified with bash, zsh, Zellij,
  the project menu/launch path, devcontainer startup, common full-screen TUIs,
  bracketed paste, mouse input, Unicode, and terminal resize.

### Web service and packaging

- **FR-040:** The service SHOULD reuse the existing FastAPI/Uvicorn service
  conventions but MUST remain a separate process, image, configuration, and
  lifecycle from the notifier.
- **FR-041:** Web service dependencies MUST live in an optional `web` package
  extra and MUST not become imports or runtime requirements of ordinary Remo
  commands. Invoking a web-only command without that extra MUST fail with a
  concise installation instruction rather than an import traceback.
- **FR-042:** The project MUST provide a multi-stage Docker build that compiles
  frontend assets and ships a minimal Linux runtime with the required SSH/SSM
  clients.
- **FR-043:** The project MUST provide a home-lab Docker Compose example for
  amd64 and arm64 hosts, including read-only mounts, tmpfs runtime directories,
  network/bind configuration, health check, restart behavior, and safe defaults.
- **FR-044:** The runtime SHOULD support a read-only root filesystem, dropped
  capabilities, no-new-privileges, a non-root UID/GID, and bounded tmpfs.
- **FR-045:** The service MUST expose liveness and readiness status that
  distinguishes process health from invalid/missing operator configuration.
- **FR-046:** `remo web check` MUST validate registry readability, SSH identity
  availability, runtime directory writability, required executables, host
  reachability, and remote protocol compatibility without opening an
  interactive session.

### Browser and network security

- **FR-047:** The service MUST bind only to an operator-configured address and
  MUST default/document deployment for a trusted LAN, loopback reverse proxy,
  or tailnet rather than public exposure.
- **FR-048:** The HTTP and WebSocket surfaces MUST validate allowed `Host` and
  `Origin` values on state-changing HTTP requests and WebSocket handshakes and
  MUST not enable wildcard CORS.
- **FR-049:** Terminal creation MUST return a short-lived, single-purpose
  WebSocket token bound to the terminal attachment. It MUST expire, be
  single-use or replay-resistant, never appear in a URL/query string, and never
  be logged. The browser SHOULD present it through a WebSocket subprotocol or an
  equivalently non-URL mechanism.
- **FR-050:** The service MUST enforce target authorization by server-side lookup
  against the current registry and discovery cache; a token cannot change its
  target after issuance.
- **FR-051:** The frontend MUST use a restrictive Content Security Policy
  compatible with the locally served Ghostty WASM and WebSocket endpoint.
- **FR-052:** The service MUST document that LAN/Tailscale reachability is the
  MVP authorization boundary and that anyone who can reach the application can
  obtain shell access to configured instances.
- **FR-053:** Built-in accounts, OIDC, role-based authorization, and multi-user
  isolation are not required, but the server boundary MUST allow an
  authentication middleware to be added later.

### Compatibility and documentation

- **FR-054:** Existing `remo shell`, tunneling, detached execution, remote update
  checks, and provider commands MUST retain their documented behavior.
- **FR-055:** Shared SSH/session refactors MUST have unit tests proving direct and
  SSM command parity and safe argument construction.
- **FR-056:** Host updates MUST remain idempotent on both fresh and already
  configured instances.
- **FR-057:** README and operator documentation MUST cover architecture, security
  boundary, Docker deployment, credentials, SSM, discovery states, terminal
  limits, troubleshooting, and upgrade compatibility.

## Proposed Service Contracts for Planning

The exact spelling may be refined during planning, but the plan must preserve
these responsibilities and version the public contracts.

```text
GET    /api/v1/health
GET    /api/v1/ready
GET    /api/v1/hosts
GET    /api/v1/sessions
POST   /api/v1/discovery/refresh
POST   /api/v1/terminals
GET    /api/v1/terminals
DELETE /api/v1/terminals/{terminal_id}
WS     /api/v1/terminals/{terminal_id}
```

`POST /api/v1/terminals` accepts only an opaque session-target ID and initial
terminal dimensions. It returns an opaque terminal ID, the short-lived
WebSocket token, and connection status. The browser cannot provide a hostname,
username, SSH option, remote command, or arbitrary project path.

The browser presents the token through a WebSocket subprotocol or another
explicitly non-URL mechanism so it does not leak through request-line/proxy
logs.

Within a terminal WebSocket:

- binary browser frames represent terminal input bytes;
- binary server frames represent PTY output bytes;
- versioned JSON text frames represent resize and lifecycle/control events;
- maximum frame and message sizes are bounded;
- error responses never contain secrets or full proxy commands.

## Key Entities

- **Known Host:** Existing `KnownHost` record loaded from the Remo registry.
  No schema change is required for the MVP.
- **Remote Capability:** Protocol version, host-tools version, projects root,
  and supported explicit operations reported by `remo-host`.
- **Session Target:** Opaque ID plus instance identity, project name,
  devcontainer presence/state, Zellij state, discovery timestamp, and status.
- **Discovery Snapshot:** Time-bounded per-instance result or typed error. It is
  replaceable on refresh and is not authoritative for provider lifecycle.
- **Terminal Attachment:** Ephemeral ID, bound session target, PTY/SSH process,
  dimensions, lifecycle state, token expiry, creation time, last activity, and
  exit/error information.
- **SSH Master:** Runtime-only multiplexed connection keyed by effective SSH
  destination and access configuration. Its socket contains no durable state.
- **Browser Workspace:** Client-side set/order/layout of open terminal IDs and
  UI preferences. Server persistence is not required.

## Edge Cases

- The registry file is absent, empty, malformed, replaced atomically, or changes
  while discovery is running.
- Two entries have the same display name but different providers, or two
  projects have the same name on different instances.
- A host resolves but rejects SSH authentication; an SSM session starts but the
  instance is stopped or lacks the plugin prerequisites.
- An AWS instance is stopped. The web MVP must report it as unavailable rather
  than automatically starting it unless auto-start is explicitly included in a
  later management feature.
- A remote instance has the Remo version marker but lacks `remo-host`, or returns
  an older/newer unsupported protocol version.
- A project is deleted or renamed between discovery and terminal creation.
- Project names contain spaces, quotes, Unicode, leading dashes, shell
  metacharacters, control characters, or traversal attempts.
- A Zellij session is in `EXITED` or corrupted state. Existing Remo cleanup and
  retry behavior should remain the source of truth.
- Devcontainer startup is slow, rebuilds, requests input, or fails after
  producing substantial output.
- The user opens the same target twice. Both attachments may attach to the same
  Zellij session, but the UI must make that duplication visible and routing must
  remain independent.
- One browser disconnects unexpectedly, sleeps, changes network, or resumes
  after the token expired.
- The service restarts while remote sessions remain active. Browser attachments
  are lost, but rediscovery and reattachment must recover the remote sessions.
- An SSH ControlMaster dies while several child attachments use it. Each child
  must fail/reconnect cleanly without corrupting another target's state.
- A terminal emits data faster than the browser can receive it, or the browser
  is background-throttled for a long period.
- Browser resize produces zero or extremely large dimensions; sizes must be
  clamped to safe documented bounds.
- A malicious webpage attempts a cross-origin WebSocket connection to the
  service, steals/replays a token, or guesses terminal/session IDs.
- The service is configured behind a reverse proxy with HTTPS/WSS and forwarded
  host headers.
- The image runs on arm64 where AWS/SSM binary packaging differs from amd64.

## Non-Functional Requirements

- **NFR-001:** Discovery of three healthy instances containing nine total
  projects SHOULD complete and render incrementally within 10 seconds on a
  typical home LAN/tailnet, with individual host results shown as soon as each
  finishes.
- **NFR-002:** For an already-running remote session, the first terminal output
  SHOULD appear within 5 seconds of the user opening it under normal network
  conditions. Cold devcontainer build time is excluded, but progress must begin
  streaming promptly.
- **NFR-003:** Interactive keystroke-to-visible-echo latency introduced by the
  web service SHOULD remain below 100 ms at the 95th percentile on the same LAN,
  excluding SSH/network and remote workload latency.
- **NFR-004:** The supported baseline MUST sustain at least nine simultaneous
  active terminals for one hour without cross-routing, process leaks, unbounded
  memory growth, or unintended disconnects.
- **NFR-005:** Discovery and terminal operations MUST be asynchronous so one
  slow host, slow terminal, or cold devcontainer does not block unrelated work.
- **NFR-006:** All runtime state except optional browser layout preferences MUST
  be ephemeral; no database or persistent server volume is required.
- **NFR-007:** The service MUST shut down gracefully, stop accepting new
  terminals, close/reap attachments within a bounded interval, and leave remote
  Zellij sessions intact.
- **NFR-008:** The normal Remo CLI path MUST not import or require FastAPI,
  Uvicorn, Ghostty Web, Node, or browser build tooling.

## Success Criteria

- **SC-001:** A user with the three-instance/nine-project example can load one
  page, see all nine targets, choose "Open all," and interact with every terminal
  without running a local CLI command.
- **SC-002:** Opening the same project from web and `remo shell -p` reaches the
  same Zellij/devcontainer session in every supported provider/access mode.
- **SC-003:** Input and output from nine concurrent terminals are never
  cross-routed, including when project names repeat across instances.
- **SC-004:** Taking one of three instances offline does not prevent the other
  six targets from being discovered or used, and the offline instance has a
  specific retryable status.
- **SC-005:** Closing or losing a browser terminal reaps its local PTY/SSH
  attachment while its remote Zellij session remains available for reattach.
- **SC-006:** A clean Docker Compose installation on both amd64 and arm64 can
  reach direct-SSH targets; an installation supplied with AWS credentials also
  reaches SSM targets.
- **SC-007:** Attempts to create terminals for arbitrary hosts, commands, or
  unreturned projects are rejected, as are wrong-origin and expired/replayed
  WebSocket connections.
- **SC-008:** Existing CLI unit and integration tests remain green, and new
  parity tests demonstrate that web and CLI share the same connection/session
  contract.
- **SC-009:** The Ghostty Web compatibility suite passes for Zellij,
  devcontainer startup, bash/zsh, common full-screen TUIs, resize, paste, mouse,
  Unicode, and mobile input; any release-blocking gap can be handled by swapping
  the renderer adapter to xterm.js without changing backend contracts.

## Required Verification Strategy

The implementation plan must include:

1. Unit tests for registry reloads, host/session IDs, protocol models and version
   negotiation, project validation, safe SSH command construction, SSM options,
   token expiry/replay, PTY resize, backpressure, and process cleanup.
2. Integration tests against disposable SSH targets implementing `remo-host`,
   including healthy, unreachable, malformed, incompatible, and slow hosts.
3. An end-to-end fixture with three independently addressable SSH targets and
   three projects on each, capable of opening nine real PTY/WebSocket terminals.
4. Browser tests for grid/tab/focus behavior, keyboard routing, reconnect,
   mobile input, Origin enforcement, and Ghostty Web/WASM loading.
5. Compatibility tests showing that `remo shell -p` and the web attach path
   enter the same remote Zellij session.
6. Image tests for amd64 and arm64, non-root/read-only operation, health and
   readiness, required mount validation, direct SSH, and SSM packaging.
7. Ansible idempotency tests on both a fresh host and a host that already has
   `project-menu`/`project-launch`, including all conditional branches.
8. Resource tests that open and exercise nine terminals for at least one hour
   and verify bounded memory, child-process cleanup, and no cross-routing.

## Project Constitution and Repository Constraints

- Preserve the existing three-layer architecture: Click modules parse and
  present, shared core code has no provider knowledge, and provider modules own
  provider business logic. Cross-provider SSH/session behavior belongs in core.
- Provider SDK imports and web-service imports must remain lazy where they are
  optional or service-specific.
- Every Ansible registered-variable attribute access must use a safe
  `| default()` filter, including `.rc`, `.stdout`, `.stderr`, `.status`, and
  related attributes.
- Test every true/false conditional path and both fresh/existing-host states.
- Provisioning and updates must be idempotent.
- Fail early with errors that explain what failed, why it matters, and how to
  correct it.
- Update README/operator documentation alongside behavior changes.
- Do not combine the web process with the notifier process. They may share
  established service patterns and dependency versions but have separate trust
  boundaries, configuration, images, health, and lifecycle.
- Preserve unrelated user changes and avoid changes to the existing flat
  registry schema unless planning proves a schema change essential.

## MVP Scope Boundaries

### In scope

- Read-only loading of all registered Remo instances.
- Structured, concurrent project/session discovery through `remo-host`.
- Direct and AWS SSM SSH parity.
- Interactive PTY/WebSocket terminal attachments using existing
  Zellij/devcontainer behavior.
- Multiple simultaneous terminals, grid/tabs/focus, bulk open, rapid switching,
  reconnect, and per-target error states.
- Ghostty Web renderer behind an adapter.
- `remo web serve`, `remo web check`, a dedicated OCI image, and Docker Compose
  home-lab installation.
- Trusted-LAN/tailnet security boundary plus Host/Origin/token protections.
- Protocol, deployment, security, compatibility, and troubleshooting docs.

### Explicitly out of scope for this MVP

- Cloning or deleting projects from the web UI.
- Starting/stopping/rebuilding devcontainers except as an inherent consequence
  of the existing attach path.
- AWS/Hetzner/Incus/Proxmox create, destroy, start, stop, reboot, resize,
  snapshot, sync, or update controls in the UI.
- Browser-managed SSH keys or uploading private keys through the UI.
- A resident Remo agent or new listener on each instance.
- Built-in user accounts, OIDC, passkeys, RBAC, multi-user tenancy, terminal
  sharing, or collaborative input.
- Persistent terminal recording, server-side scrollback, command auditing, or a
  database.
- File browser, file transfer, editor, desktop streaming, or port-forwarding UI.
- Direct browser-to-host SSH or exposing remote SSH credentials to the browser.
- Mounting or controlling the home-lab Docker socket.
- Integrating session discovery with notifier source registration; notifier
  sources are opt-in agentsh endpoints and are not the authoritative list of
  Remo projects.

## Post-MVP Direction That the Architecture Must Enable

The next likely feature is: choose an existing instance, provide a Git repository
reference, clone it under `~/projects`, launch its devcontainer, and immediately
open the resulting browser terminal. This should be implemented later as an
explicit, validated `remo-host projects clone` operation plus a shared client
method usable by both web and CLI. It must not require a new host daemon or an
arbitrary remote-command endpoint.

Later provider lifecycle controls may reuse the existing provider business
logic, but they introduce provider credentials and a materially larger security
boundary. They should be separately specified rather than implicitly granting
the MVP web container access to cloud/provider credentials beyond what is
required for SSH/SSM connectivity.

## Assumptions

- The service is operated by one trusted user and is reachable only through a
  trusted LAN, Tailscale network, or equivalently protected reverse proxy.
- Every supported instance already accepts non-interactive SSH authentication
  from a dedicated service identity, or the operator can provision one.
- The Docker host has outbound network access to every direct SSH target and to
  AWS APIs when SSM is used.
- Zellij remains Remo's authoritative persistent session mechanism.
- Existing `project-launch` behavior remains authoritative for starting and
  entering a project's devcontainer.
- The service may cache discovery briefly, but the remote host is authoritative;
  terminal creation must revalidate that the target is still allowed.
- No server database is necessary for the MVP.
- The current flat Remo registry remains the authoritative instance catalog.
