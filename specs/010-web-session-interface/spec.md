# Feature Specification: Remo Web Session Interface

**Feature Branch**: `010-web-session-interface`

**Created**: 2026-07-13

**Status**: Draft

**Input**: User description: "Remo Web Session Interface — a browser-based terminal broker and session dashboard that discovers projects/session state across all Remo-managed instances over SSH and lets one trusted user open and rapidly switch among many interactive terminals, running as a home-lab Docker service."

## Overview

Remo currently assumes the user has a local terminal from which to run `remo shell`. That is not always true: the user may be on a tablet, a locked-down workstation, or any browser-capable device without the Remo CLI and its SSH configuration. Remo's remote hosts already run persistent Zellij project sessions and know how to start or enter devcontainers, so the missing piece is a central, browser-accessible terminal broker and session dashboard.

This feature adds a home-lab Docker web service (packaged inside the existing Remo repository and CLI family) that reads a read-only mount of Remo's `~/.config/remo/known_hosts` registry, connects **server-to-instance** over SSH using the same provider-aware behavior as the CLI, discovers each instance's projects and session state through a new versioned `remo-host` command, and streams interactive terminals to the browser over WebSockets backed by one server-side PTY and SSH attachment each.

The motivating example: a user with three registered instances (one Proxmox, one AWS, one Hetzner), three devcontainer projects on each, discovers all nine session targets, opens all nine at once, views them in a grid or as tabs, focuses any one, and switches among them without returning to a local terminal.

**A project opened in the browser and the same project opened with `remo shell -p <project>` MUST enter the same remote Zellij session and the same devcontainer environment.** The web application preserves existing Remo behavior rather than creating a second, subtly different devcontainer launcher.

The MVP is single-user, for a trusted LAN/tailnet (normally reached over Tailscale). It focuses on **discovery** and **browser terminal access**. Project cloning, project lifecycle actions, instance lifecycle actions, and port forwarding are out of scope, but contracts are designed so those can be added to both CLI and web without replacing the discovery or connectivity architecture.

### Terms

| Term | Definition |
|---|---|
| **Remo registry** | The existing colon-delimited `~/.config/remo/known_hosts` file (provider type, Remo name, address, SSH user, provider identity/access mode, region). Not the OpenSSH `known_hosts` file; contains no credentials. |
| **Instance** | One SSH-reachable Remo environment represented by a `KnownHost`, regardless of provider (AWS instance, Hetzner server, Incus/Proxmox container). |
| **Project** | A directory beneath the Remo user's `~/projects` on an instance, normally containing a devcontainer definition, already selectable through `project-menu`. |
| **Session target** | The stable pair `(instance, project)` that can be opened in an interactive terminal. Its opaque public ID must not expose a command or permit an arbitrary destination. |
| **Remote session** | The Zellij session named for the project on the selected instance; for a devcontainer project its shell runs inside the container per existing Remo behavior. |
| **Browser terminal** | One browser terminal component connected by WebSocket to one server-side PTY and SSH attach process. |
| **Terminal attachment** | The ephemeral server-side PTY/SSH process attaching a browser terminal to a remote session; may end while the remote Zellij session continues. |
| **`remo-host`** | A new, versioned, non-daemon command installed on every Remo instance. Exposes structured discovery and explicit session/project operations over SSH. Listens on no port; runs only when invoked. |
| **Web service** | The home-lab Docker service that serves the UI and API, owns browser WebSockets and PTYs, reads the registry, and initiates SSH connections. |

## Clarifications

### Session 2026-07-13

- Q: How should `remo-host` protocol version compatibility be decided? → A: Range negotiation — the client declares an inclusive `[min_supported, max_supported]` range of supported major protocol versions; any host reporting a version within that range is compatible, and additive/minor fields are backward-compatible within a major version. Versions outside the range yield a typed incompatibility with a per-instance update prompt.
- Q: Is terminal reconnection automatic or user-initiated? → A: Bounded automatic reconnect — on an unexpected WebSocket close the client transparently re-requests a fresh single-use terminal token and reattaches with backoff up to a small capped number of attempts; after the cap (or on token expiry) it stops and shows a manual "Reconnect" control.
- Q: What are the default terminal concurrency limits? → A: Default global limit 32 concurrent terminals, default per-client limit 16 — both operator-configurable. (Comfortably above the nine-terminal tested baseline.)
- Q: What is the WebSocket terminal token lifetime? → A: 30 seconds from issuance to WebSocket upgrade, single-use (consumed on successful upgrade), operator-configurable.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Discover every available session target (Priority: P1)

The user opens the web application and sees projects from every registered and reachable instance, grouped by provider and instance. Each project shows whether a Zellij session is active and whether its devcontainer is running. Unreachable or outdated instances remain visible with an actionable status.

**Why this priority**: A central view across providers is the primary value that does not exist in `remo shell` today.

**Independent Test**: Mount a registry containing three instances with three projects each, make all three reachable, load the UI, and verify all nine targets appear with correct grouping and state without opening a terminal.

**Acceptance Scenarios**:

1. **Given** a mounted registry with reachable Proxmox, AWS, and Hetzner instances, **When** the dashboard loads, **Then** it discovers projects on all three concurrently and displays the combined results.
2. **Given** one unreachable instance, **When** discovery runs, **Then** projects from reachable instances appear without waiting for the unreachable one's full failure lifecycle, and the unreachable instance displays a retryable error.
3. **Given** the mounted registry changes, **When** the user refreshes discovery or the configured refresh interval elapses, **Then** added and removed instances are reflected without restarting the container.
4. **Given** an instance lacks the required remote protocol version, **When** it is queried, **Then** the UI identifies the incompatibility and names the Remo update action required to fix it.

---

### User Story 2 - Open a browser terminal into a project (Priority: P1)

The user selects a project and receives an interactive browser terminal attached to that project's normal Remo session. If the Zellij session or devcontainer is not running, existing Remo launch behavior creates/starts it while streaming progress into the same terminal. If it is already running, the browser attaches to it.

**Why this priority**: Browser terminal access is the feature's core outcome.

**Independent Test**: From a browser with no local CLI, open one stopped devcontainer project, observe startup, obtain a shell inside the container, run a command, disconnect, reconnect, and observe the same Zellij session.

**Acceptance Scenarios**:

1. **Given** an existing active project session, **When** the user opens it, **Then** the browser attaches to that same Zellij session and input/output is interactive.
2. **Given** a project whose devcontainer is stopped, **When** the user opens it, **Then** startup output is streamed and the final shell is inside the project's devcontainer, matching `remo shell -p <project>`.
3. **Given** the browser connection drops, **When** the user reconnects, **Then** a fresh terminal attachment reaches the still-running remote Zellij session.
4. **Given** the SSH authentication or remote launch fails, **When** the terminal is opened, **Then** the UI shows the specific failure and provides retry without leaving an orphaned PTY or SSH process.

---

### User Story 3 - Open and switch among many sessions (Priority: P1)

The user can open several or all discovered targets. The workspace offers a terminal grid, tabbed/focused view, and keyboard-friendly switching. Each open terminal stays independently connected while the user views another one.

**Why this priority**: The intended advantage over repeatedly invoking `remo shell` is rapid multi-instance context switching.

**Independent Test**: Open all nine targets in the three-instance example, interact with each, switch repeatedly through grid and focused modes, and verify that output and input stay routed to the correct terminal.

**Acceptance Scenarios**:

1. **Given** nine discovered targets, **When** the user chooses "Open all", **Then** nine terminal cards are created and connect independently with per-terminal progress and error state.
2. **Given** multiple open terminals, **When** one is focused or selected by a keyboard shortcut, **Then** keyboard input is sent only to that terminal.
3. **Given** one terminal disconnects, **When** the others remain healthy, **Then** they continue without interruption and only the failed terminal shows reconnect controls.
4. **Given** terminals from different instances have similar project names, **When** the user switches among them, **Then** provider, instance, and project identity are always visible and output is never cross-routed.

---

### User Story 4 - Install as a home-lab Docker service (Priority: P1)

The user installs the web application using a documented Docker Compose example, mounts their Remo registry and dedicated SSH material read-only, supplies a network route to the registered instances, and accesses the service through their LAN or Tailscale environment.

**Why this priority**: The browser cannot provide value unless the service is easy and safe to operate continuously in a home lab.

**Independent Test**: On a clean amd64 or arm64 Linux Docker host, follow only the documented Compose instructions, run the readiness check, and open a remote project terminal from another tailnet device.

**Acceptance Scenarios**:

1. **Given** a valid registry and SSH identity, **When** the container starts, **Then** its health endpoint becomes ready and `remo web check` validates the configuration.
2. **Given** only the Remo registry is mounted, **When** the service starts, **Then** it fails readiness with a clear explanation that registry metadata is not SSH authentication material.
3. **Given** an AWS entry using SSM access mode, **When** its required AWS credentials/profile are present, **Then** discovery and terminal attachment follow the same SSM route as the CLI.
4. **Given** the container uses a read-only root filesystem, **When** terminals are opened, **Then** runtime state is confined to declared tmpfs/writable mounts and no credentials are copied into the image or written to logs.

---

### User Story 5 - Preserve CLI behavior and compatibility (Priority: P2)

The user continues to use `remo shell` normally after upgrading hosts for web support. The CLI and web service share session identities, connection builders, remote protocol parsing, and safe validation instead of drifting into separate implementations.

**Why this priority**: Divergence between web and CLI would create two subtly different launchers and break the "same session, either surface" guarantee; it is a correctness constraint rather than a user-facing capability.

**Independent Test**: Open the same project from the web interface and from `remo shell -p`, confirm both attach to the same Zellij session, and run existing CLI tests unchanged except for deliberate shared-core refactors.

**Acceptance Scenarios**:

1. **Given** an upgraded instance, **When** the user runs existing `remo shell` commands, **Then** all existing options and behavior continue to work.
2. **Given** a project opened in the web UI, **When** the user later invokes `remo shell <host> -p <project>`, **Then** it attaches to the same remote session.
3. **Given** a project name containing supported spaces or punctuation, **When** it is opened through either surface, **Then** shared validation and quoting prevent command injection and identify the same project.

---

### Edge Cases

- The registry file is absent, empty, malformed, replaced atomically, or changes while discovery is running.
- Two entries share a display name but differ in provider, or two projects share a name on different instances.
- A host resolves but rejects SSH authentication; an SSM session starts but the instance is stopped or lacks plugin prerequisites.
- An AWS instance is stopped — the MVP reports it as unavailable rather than auto-starting it.
- A remote instance has the Remo version marker but lacks `remo-host`, or returns an older/newer unsupported protocol version.
- A project is deleted or renamed between discovery and terminal creation.
- Project names contain spaces, quotes, Unicode, leading dashes, shell metacharacters, control characters, or traversal attempts.
- A Zellij session is in `EXITED` or corrupted state — existing Remo cleanup/retry behavior remains the source of truth.
- Devcontainer startup is slow, rebuilds, requests input, or fails after producing substantial output.
- The user opens the same target twice — both attachments may reach the same Zellij session, but the UI makes the duplication visible and routing stays independent.
- A browser disconnects unexpectedly, sleeps, changes network, or resumes after the token expired.
- The service restarts while remote sessions remain active — browser attachments are lost, but rediscovery and reattachment recover the remote sessions.
- An SSH ControlMaster dies while several child attachments use it — each child fails/reconnects cleanly without corrupting another target's state.
- A terminal emits data faster than the browser can receive, or the browser is background-throttled for a long period.
- Browser resize produces zero or extremely large dimensions — sizes are clamped to safe documented bounds.
- A malicious webpage attempts a cross-origin WebSocket connection, steals/replays a token, or guesses terminal/session IDs.
- The service runs behind a reverse proxy with HTTPS/WSS and forwarded host headers.
- The image runs on arm64 where AWS/SSM binary packaging differs from amd64.

## Required Architectural Decisions *(preserved — these constrain planning and MUST NOT be traded away)*

1. **In-repo, separate packaging.** Implement in the existing Remo repository as a separately packaged `remo-web` OCI image and a `remo web` command group (`remo web serve`, `remo web check`). The normal CLI installation MUST NOT acquire the web server's runtime dependencies; web deps live in an optional package extra and web code is lazily imported, in the same spirit as the notifier service. (A future `remo web deploy` may automate deployment; a Docker Compose example suffices for the MVP.)
2. **SSH as host transport; no resident agent.** No daemon, HTTP listener, or new open port on any instance. Use OpenSSH for both structured remote commands and interactive attachments, preserving direct-SSH and AWS-SSM access behavior. Reuse/refactor shared logic from `core/ssh.py` (`KnownHost` resolution, direct vs SSM targeting, region/profile behavior, safe remote command construction, SSH multiplexing). MUST NOT shell out to interactive `remo shell` or parse its human-facing output.
3. **Versioned `remo-host` command on every instance.** Install `~/.local/bin/remo-host` via the existing Ansible host-configuration flow (a command, not a service). MVP contract: `remo-host capabilities --json`, `remo-host sessions list --json`, `remo-host sessions attach --project <name>`. The client representation of this protocol lives in shared Remo code usable by both `remo shell` and the web service. Extensible with future verbs (`projects clone`, `projects delete`, `sessions stop`) without accepting an arbitrary shell command as an API operation.
4. **One server-side PTY and SSH attachment per browser terminal.** The browser never connects directly to SSH. For each terminal the service creates a Unix PTY, runs an SSH client attached to it, forces remote TTY allocation, and invokes the explicit remote attach operation. Losing the WebSocket or killing the SSH attachment MUST NOT kill the remote Zellij session; reconnection creates a new attachment to the same session. Use `TERM=xterm-256color` (or another deliberately supported terminfo value).
5. **Multiplex SSH by instance.** Concurrent attachments to the same instance reuse an OpenSSH ControlMaster (nine sessions across three instances → three masters, nine attachments). Refactor the hard-coded ControlPath so sockets live in a writable tmpfs (e.g. `/run/remo-ssh`) while keys/config stay read-only. Scope multiplexing by user/host/port, clean up stale sockets, degrade to a clear reconnect if a master dies.
6. **Ghostty Web behind a small adapter.** Use `ghostty-web` as the default browser terminal emulator behind a Remo-owned renderer adapter (create/open, write, input, resize, focus, dispose, title, selection/copy). Pin the dependency and its WASM asset. Keep an xterm.js fallback possible. Reverify the current compatible version during planning (latest tagged release as of 2026-07-13 was 0.4.0, 2025-12-09).
7. **Trusted network is the MVP access boundary.** Single-user; no local accounts, OIDC, or multi-user authorization. Deployment docs MUST prominently state the service grants shell access to every configured instance and must not be exposed to an untrusted network. Trusted-network deployment still requires browser protections (Host/Origin validation, short-lived single-use terminal tokens, no permissive CORS, same-origin reverse-proxy support). A later authentication layer MUST be addable without changing the terminal protocol.

## Requirements *(mandatory)*

### Functional Requirements

#### Registry and host access

- **FR-001**: The service MUST read the existing Remo registry through `REMO_HOME`/the established config-path rules and MUST support mounting that registry read-only.
- **FR-002**: The service MUST treat `(provider type, Remo name)` as the stable instance identity and use opaque public IDs in browser/API operations.
- **FR-003**: The service MUST support every access mode supported by `build_ssh_opts`, including direct SSH and AWS SSM proxying.
- **FR-004**: The service MUST load registry changes without process restart and MUST NOT mutate the mounted registry in the MVP.
- **FR-005**: Discovery MUST run concurrently across instances with configurable concurrency, connection timeout, command timeout, cache duration, and manual refresh.
- **FR-006**: A host's failure MUST be isolated from every other host and MUST be represented as typed status rather than an empty successful result.

#### Remote command protocol

- **FR-007**: The standard host configuration flow MUST install `remo-host` idempotently and MUST update it when Remo host tools are updated.
- **FR-008**: `remo-host` MUST implement a documented, versioned JSON protocol for capabilities and session discovery. The client MUST declare an inclusive `[min_supported, max_supported]` range of supported major protocol versions and MUST treat any host reporting a version within that range as compatible, tolerating additive/minor fields as backward-compatible within a major version.
- **FR-009**: A discovery result MUST include instance protocol version and, for each project, name, devcontainer presence, Zellij session state, and devcontainer running state when available.
- **FR-010**: Discovery MUST be read-only: it MUST NOT start a devcontainer, create a Zellij session, perform `git fetch`, or modify project state.
- **FR-011**: Attach MUST validate a project against the configured projects directory and MUST refuse absolute paths, parent traversal, control characters, or projects that no longer exist.
- **FR-012**: Machine-readable commands MUST reserve stdout for their defined payload, use stderr for diagnostics, and return documented exit codes.
- **FR-013**: The client MUST reject host protocol versions outside its supported `[min_supported, max_supported]` range and malformed payloads with actionable errors (a version outside range surfaces as a typed incompatibility with a per-instance update prompt per FR-059), and MUST limit payload size.
- **FR-014**: Future explicit project-management verbs MUST be addable without expanding the protocol into arbitrary command execution.
- **FR-058**: Every project directory MUST remain an eligible session target; projects without a devcontainer configuration MUST be clearly marked and follow existing plain project/Zellij behavior, and discovery MUST NOT present them as devcontainer environments.
- **FR-059**: If an instance is too old or lacks `remo-host`, the UI MUST show a clear per-instance update instruction rather than silently omitting it or falling back to scraping `project-menu` output.

#### Terminal lifecycle and routing

- **FR-015**: Creating a terminal attachment MUST require a currently discovered instance/project target; browser requests MUST NOT supply raw SSH targets, users, remote paths, SSH options, or commands.
- **FR-016**: Each attachment MUST use a PTY-backed SSH process and force a remote TTY for the existing Zellij/devcontainer flow.
- **FR-017**: The WebSocket protocol MUST distinguish byte-oriented PTY data from structured control messages. Binary frames SHOULD carry PTY data and text frames SHOULD carry versioned JSON control messages (resize, ready, exit, error).
- **FR-018**: Browser-to-PTY input, PTY-to-browser output, and resize events MUST be streamed bidirectionally and independently per terminal.
- **FR-019**: Closing a browser terminal MUST terminate and reap its PTY/SSH attachment without intentionally terminating the remote Zellij session.
- **FR-020**: A WebSocket disconnect MUST trigger bounded cleanup. On an unexpected disconnect the client MUST attempt bounded automatic reconnection — re-requesting a fresh single-use terminal token and reattaching to the same session target with backoff up to a small configurable cap — and MUST fall back to a manual "Reconnect" control after the cap is reached or the token has expired. Each reconnect creates a new attachment to the same session target.
- **FR-021**: The service MUST apply bounded queues/backpressure and a configurable per-terminal output limit so stalled browser clients cannot consume unbounded memory.
- **FR-022**: The service MUST support configurable global and per-client terminal limits, defaulting to 32 global and 16 per-client, and MUST reject excess attachments clearly.
- **FR-023**: Terminal exit and setup errors MUST be classified and surfaced to the correct terminal only; a failure MUST NEVER be rendered in another terminal's stream.
- **FR-060**: Terminal resize requests MUST be clamped to safe documented row/column bounds, rejecting zero or extreme dimensions before propagation to the PTY.

#### SSH multiplexing and credentials

- **FR-024**: Attachments to the same SSH destination MUST support a shared ControlMaster, with its ControlPath configurable to a writable runtime directory.
- **FR-025**: The service MUST use a dedicated non-root runtime user, read-only SSH identity/config/host-trust mounts, strict host-key verification for direct SSH, and non-interactive authentication (`BatchMode` or equivalent). It MUST NOT require the Docker socket.
- **FR-026**: The Compose documentation MUST distinguish the Remo registry from OpenSSH host trust and identity material and document all required mounts.
- **FR-027**: AWS SSM support MUST include the required AWS CLI and Session Manager Plugin runtime and a documented read-only credential/profile or workload-identity configuration.
- **FR-028**: SSH secrets, AWS secrets, proxy commands, and WebSocket tokens MUST be redacted from application logs and browser-visible error details.

#### Web UI

- **FR-029**: The dashboard MUST group session targets by provider and instance and show reachability, compatibility, Zellij state, and devcontainer state.
- **FR-030**: Users MUST be able to open one target, all targets on an instance, a selected set, or all discovered targets.
- **FR-031**: The terminal workspace MUST offer grid, tab/focused, and rapid keyboard switching modes without disconnecting hidden terminals.
- **FR-032**: Every terminal surface MUST display provider, instance, project, connection state, and controls for focus, reconnect, and close.
- **FR-033**: The UI MUST remain functional on current desktop and tablet browsers and provide basic mobile keyboard/input operation.
- **FR-034**: Workspace layout and display preferences MAY be stored in browser local storage; no server-side database is required for the MVP.
- **FR-035**: Discovery and terminal state MUST update incrementally so one slow instance or terminal does not block the rest of the interface.

#### Terminal renderer integration

- **FR-036**: The browser terminal emulator MUST be loaded through a Remo-owned renderer adapter, not referenced throughout general application state.
- **FR-037**: The adapter MUST cover initialization, write, input events, fit and resize, focus, title, selection/copy, and disposal.
- **FR-038**: The build MUST serve the terminal WASM asset with the correct content type and same-origin/CSP behavior and MUST NOT depend on a public CDN at runtime.
- **FR-039**: Terminal compatibility MUST be verified with bash, zsh, Zellij, the project menu/launch path, devcontainer startup, common full-screen TUIs, bracketed paste, mouse input, Unicode, and terminal resize.

#### Web service and packaging

- **FR-040**: The service SHOULD reuse existing FastAPI/Uvicorn service conventions but MUST remain a separate process, image, configuration, and lifecycle from the notifier.
- **FR-041**: Web service dependencies MUST live in an optional `web` package extra and MUST NOT become imports or runtime requirements of ordinary Remo commands. Invoking a web-only command without that extra MUST fail with a concise installation instruction rather than an import traceback.
- **FR-042**: The project MUST provide a multi-stage Docker build that compiles frontend assets and ships a minimal Linux runtime with the required SSH/SSM clients.
- **FR-043**: The project MUST provide a home-lab Docker Compose example for amd64 and arm64 hosts, including read-only mounts, tmpfs runtime directories, network/bind configuration, health check, restart behavior, and safe defaults.
- **FR-044**: The runtime SHOULD support a read-only root filesystem, dropped capabilities, no-new-privileges, a non-root UID/GID, and bounded tmpfs.
- **FR-045**: The service MUST expose liveness and readiness status that distinguishes process health from invalid/missing operator configuration.
- **FR-046**: `remo web check` MUST validate registry readability, SSH identity availability, runtime directory writability, required executables, host reachability, and remote protocol compatibility without opening an interactive session.

#### Browser and network security

- **FR-047**: The service MUST bind only to an operator-configured address and MUST default/document deployment for a trusted LAN, loopback reverse proxy, or tailnet rather than public exposure.
- **FR-048**: The HTTP and WebSocket surfaces MUST validate allowed `Host` and `Origin` values on state-changing HTTP requests and WebSocket handshakes and MUST NOT enable wildcard CORS.
- **FR-049**: Terminal creation MUST return a short-lived, single-purpose WebSocket token bound to the terminal attachment. It MUST expire (default 30 seconds from issuance to WebSocket upgrade, configurable), be single-use — consumed on successful upgrade so it cannot be replayed — never appear in a URL/query string, and never be logged. The browser SHOULD present it through a WebSocket subprotocol or an equivalently non-URL mechanism.
- **FR-050**: The service MUST enforce target authorization by server-side lookup against the current registry and discovery cache; a token cannot change its target after issuance.
- **FR-051**: The frontend MUST use a restrictive Content Security Policy compatible with the locally served terminal WASM and WebSocket endpoint.
- **FR-052**: The service MUST document that LAN/Tailscale reachability is the MVP authorization boundary and that anyone who can reach the application can obtain shell access to configured instances.
- **FR-053**: Built-in accounts, OIDC, role-based authorization, and multi-user isolation are not required, but the server boundary MUST allow an authentication middleware to be added later.

#### Compatibility and documentation

- **FR-054**: Existing `remo shell`, tunneling, detached execution, remote update checks, and provider commands MUST retain their documented behavior.
- **FR-055**: Shared SSH/session refactors MUST have unit tests proving direct and SSM command parity and safe argument construction.
- **FR-056**: Host updates MUST remain idempotent on both fresh and already-configured instances.
- **FR-057**: README and operator documentation MUST cover architecture, security boundary, Docker deployment, credentials, SSM, discovery states, terminal limits, troubleshooting, and upgrade compatibility.

### Service Contracts *(exact spelling may be refined during planning; responsibilities and versioning MUST be preserved)*

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

- `POST /api/v1/terminals` accepts only an opaque session-target ID and initial terminal dimensions; it returns an opaque terminal ID, the short-lived WebSocket token, and connection status. The browser cannot provide a hostname, username, SSH option, remote command, or arbitrary project path.
- The browser presents the token through a WebSocket subprotocol or another explicitly non-URL mechanism.
- Within a terminal WebSocket: binary browser frames = input bytes; binary server frames = PTY output bytes; versioned JSON text frames = resize and lifecycle/control events; maximum frame/message sizes are bounded; error responses never contain secrets or full proxy commands.

### Key Entities

- **Known Host**: Existing `KnownHost` record loaded from the Remo registry. No schema change required for the MVP.
- **Remote Capability**: Protocol version, host-tools version, projects root, and supported explicit operations reported by `remo-host`.
- **Session Target**: Opaque ID plus instance identity, project name, devcontainer presence/state, Zellij state, discovery timestamp, and status.
- **Discovery Snapshot**: Time-bounded per-instance result or typed error; replaceable on refresh; not authoritative for provider lifecycle.
- **Terminal Attachment**: Ephemeral ID, bound session target, PTY/SSH process, dimensions, lifecycle state, token expiry, creation time, last activity, and exit/error information.
- **SSH Master**: Runtime-only multiplexed connection keyed by effective SSH destination and access configuration; socket contains no durable state.
- **Browser Workspace**: Client-side set/order/layout of open terminal IDs and UI preferences; server persistence not required.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user with the three-instance/nine-project example can load one page, see all nine targets, choose "Open all," and interact with every terminal without running a local CLI command.
- **SC-002**: Opening the same project from web and `remo shell -p` reaches the same Zellij/devcontainer session in every supported provider/access mode.
- **SC-003**: Input and output from nine concurrent terminals are never cross-routed, including when project names repeat across instances.
- **SC-004**: Taking one of three instances offline does not prevent the other six targets from being discovered or used, and the offline instance has a specific retryable status.
- **SC-005**: Closing or losing a browser terminal reaps its local PTY/SSH attachment while its remote Zellij session remains available for reattach.
- **SC-006**: A clean Docker Compose installation on both amd64 and arm64 can reach direct-SSH targets; an installation supplied with AWS credentials also reaches SSM targets.
- **SC-007**: Attempts to create terminals for arbitrary hosts, commands, or unreturned projects are rejected, as are wrong-origin and expired/replayed WebSocket connections.
- **SC-008**: Existing CLI unit and integration tests remain green, and new parity tests demonstrate that web and CLI share the same connection/session contract.
- **SC-009**: The terminal compatibility suite passes for Zellij, devcontainer startup, bash/zsh, common full-screen TUIs, resize, paste, mouse, Unicode, and mobile input; any release-blocking gap can be handled by swapping the renderer adapter to xterm.js without changing backend contracts.
- **SC-010**: Discovery of three healthy instances (nine projects) completes and renders incrementally within 10 seconds on a typical home LAN/tailnet, with each host's results shown as soon as it finishes.
- **SC-011**: For an already-running remote session, the first terminal output appears within 5 seconds of opening it under normal network conditions (cold devcontainer build time excluded, but progress begins streaming promptly).
- **SC-012**: Web-service-introduced keystroke-to-visible-echo latency stays below 100 ms at the 95th percentile on the same LAN, excluding SSH/network and remote workload latency.
- **SC-013**: The baseline sustains at least nine simultaneous active terminals for one hour without cross-routing, process leaks, unbounded memory growth, or unintended disconnects.
- **SC-014**: On graceful shutdown the service stops accepting new terminals, closes/reaps attachments within a bounded interval, and leaves remote Zellij sessions intact.

## Scope

### In scope

- Read-only loading of all registered Remo instances.
- Structured, concurrent project/session discovery through `remo-host`.
- Direct and AWS SSM SSH parity.
- Interactive PTY/WebSocket terminal attachments using existing Zellij/devcontainer behavior.
- Multiple simultaneous terminals; grid/tabs/focus; bulk open; rapid switching; reconnect; per-target error states.
- Browser terminal renderer behind an adapter.
- `remo web serve`, `remo web check`, a dedicated OCI image, and Docker Compose home-lab installation.
- Trusted-LAN/tailnet security boundary plus Host/Origin/token protections.
- Protocol, deployment, security, compatibility, and troubleshooting docs.

### Out of scope (this MVP)

- Cloning or deleting projects from the web UI.
- Starting/stopping/rebuilding devcontainers except as an inherent consequence of the existing attach path.
- AWS/Hetzner/Incus/Proxmox create, destroy, start, stop, reboot, resize, snapshot, sync, or update controls in the UI.
- Browser-managed SSH keys or uploading private keys through the UI.
- A resident Remo agent or new listener on each instance.
- Built-in user accounts, OIDC, passkeys, RBAC, multi-user tenancy, terminal sharing, or collaborative input.
- Persistent terminal recording, server-side scrollback, command auditing, or a database.
- File browser, file transfer, editor, desktop streaming, or port-forwarding UI.
- Direct browser-to-host SSH or exposing remote SSH credentials to the browser.
- Mounting or controlling the home-lab Docker socket.
- Integrating session discovery with notifier source registration.

### Post-MVP direction the architecture must enable

- Choose an instance, provide a Git repository reference, clone it under `~/projects`, launch its devcontainer, and immediately open the resulting browser terminal — implemented later as an explicit, validated `remo-host projects clone` operation plus a shared client method usable by both web and CLI, without a new host daemon or arbitrary remote-command endpoint.
- Later provider lifecycle controls may reuse existing provider business logic but introduce a materially larger security boundary; they must be separately specified rather than implicitly granting the MVP web container broader cloud/provider credentials.

## Assumptions

- The service is operated by one trusted user and reachable only through a trusted LAN, Tailscale network, or equivalently protected reverse proxy.
- Every supported instance already accepts non-interactive SSH authentication from a dedicated service identity, or the operator can provision one.
- The Docker host has outbound network access to every direct SSH target and to AWS APIs when SSM is used.
- Zellij remains Remo's authoritative persistent session mechanism.
- Existing `project-launch` behavior remains authoritative for starting and entering a project's devcontainer.
- The service may cache discovery briefly, but the remote host is authoritative; terminal creation must revalidate that the target is still allowed.
- No server database is necessary for the MVP; all runtime state except optional browser layout preferences is ephemeral.
- The current flat Remo registry remains the authoritative instance catalog, and no registry schema change is made unless planning proves one essential.
- The web and notifier services stay separate processes/images/configs/lifecycles with distinct trust boundaries, though they may share established service patterns and dependency versions.
