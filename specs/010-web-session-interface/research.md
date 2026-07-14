# Phase 0 Research: Remo Web Session Interface

All Technical Context unknowns are resolved below. Decisions are grounded in the existing
codebase (`src/remo_cli/core/ssh.py`, `ansible/roles/user_setup/`) so the web surface reuses
Remo behavior rather than reimplementing it.

## R1. remo-host discovery/attach mechanics (map onto existing host behavior)

- **Decision**: `remo-host` is a Bash command installed at `~/.local/bin/remo-host` that reuses the
  exact primitives the existing scripts already use:
  - Project enumeration = `find $PROJECTS_DIR -maxdepth 1 -mindepth 1 -type d` (as in `project-menu` `get_project_dirs`), where `$PROJECTS_DIR = dev_workspace_dir` (`/home/<user>/projects`).
  - Devcontainer presence = `-d $dir/.devcontainer || -f $dir/.devcontainer.json` (as in `project-launch`).
  - Zellij state = parse `zellij list-sessions` with ANSI stripped (`sed 's/\x1b\[[0-9;]*m//g'`); classify each project name as `active` / `exited` / `absent` (mirrors `get_active_sessions` + EXITED handling).
  - Devcontainer running = `docker ps -q --filter "label=devcontainer.local_folder=$dir"` (as in `project-menu` `handle_delete`); `unknown` when docker is unavailable.
  - `sessions attach --project NAME` = `exec ~/.local/bin/project-launch --project "$NAME"` after validating NAME. This is the single source of truth for the Zellij+devcontainer entry, guaranteeing web/CLI parity (SC-002).
- **Rationale**: Reuses battle-tested logic; discovery stays strictly read-only (FR-010) because it only lists/greps and never calls `project-launch`, `zellij attach`, `git fetch`, or `devcontainer up`. Note `project-menu` runs `git fetch` during its build — `remo-host sessions list` deliberately does **not**.
- **Alternatives considered**: A Python helper on the host (rejected: adds a runtime dependency the instances don't guarantee; Bash + coreutils already present). Scraping `project-menu` output (rejected by spec FR-059 — it is interactive/fzf, ANSI-laden, and mixes git-fetch side effects).

## R2. remo-host JSON protocol & version negotiation

- **Decision**: Machine commands (`capabilities --json`, `sessions list --json`) print a single JSON
  object to **stdout only**, diagnostics to **stderr**, with documented exit codes (0 ok, 2 usage,
  3 invalid project, 4 unsupported subcommand, 5 internal). Top-level `protocol_version` is an
  integer major version; additive fields are allowed within a major. The client (shared
  `core/remo_host_client.py`) declares `SUPPORTED = [1, 1]` (`[min,max]`, inclusive) and treats a
  host `protocol_version` inside the range as compatible (Clarifications Q1). Outside range → typed
  `IncompatibleProtocol` surfaced as a per-instance update prompt (FR-059). Client enforces a
  payload-size cap (default 256 KiB) and rejects malformed JSON with an actionable error (FR-013).
- **Rationale**: Integer-major range negotiation gives the mixed-fleet tolerance FR-059 anticipates
  during staggered host upgrades, without a full semver parser in Bash.
- **Alternatives considered**: Exact-match (rejected — lockstep upgrades); semver strings (rejected —
  heavier to emit/validate in Bash for no MVP benefit).

## R3. SSH transport reuse & ControlPath refactor

- **Decision**: Reuse `build_ssh_opts()` verbatim for direct vs SSM targeting, region/profile, and
  timezone `SendEnv`. Refactor the hard-coded `ControlPath=~/.ssh/remo-%r@%h-%p` into a resolved
  base dir: default keeps today's `~/.ssh/remo-…` for the CLI; the web service sets
  `$REMO_SSH_CONTROL_DIR=/run/remo-ssh` (writable tmpfs) so keys/config stay read-only (FR-024).
  Extract a `build_ssh_base_cmd(host, *, tty, multiplex, control_dir)` used by both `shell_connect`
  (CLI) and the web terminal builder, so argument construction/quoting is shared and unit-tested for
  direct+SSM parity (FR-055). Web always adds `-o BatchMode=yes` and, for direct SSH, keeps
  `StrictHostKeyChecking` on with a mounted read-only `known_hosts` (FR-025).
- **Rationale**: One code path for both surfaces prevents drift and satisfies the "reuse core/ssh.py,
  do not shell out to `remo shell`" decision. Existing SSM `ProxyCommand` already yields SSM parity.
- **Alternatives considered**: A separate web SSH builder (rejected — divergence risk, FR-055 parity
  tests would have nothing shared to prove). Paramiko/asyncssh (rejected — loses SSM ProxyCommand +
  ControlMaster reuse Remo depends on; OpenSSH is the required transport).

## R4. Per-terminal PTY + SSH attachment lifecycle

- **Decision**: Each terminal = one `pty.openpty()` + `asyncio` subprocess running
  `ssh <opts> -tt <target> "~/.local/bin/remo-host sessions attach --project <quoted>"`. `-tt` forces
  remote TTY for the Zellij/devcontainer flow (matches CLI's `-t`). `TERM=xterm-256color` is forced
  (host `.bashrc`/`.bash_profile` already fall back to it). Reader task pumps PTY→WebSocket binary
  frames; writer task pumps WS binary→PTY; a JSON control frame carries `resize` (→ `TIOCSWINSZ` via
  `fcntl`/`termios`), `ready`, `exit`, `error`. Output uses a bounded queue with a per-terminal byte
  cap (FR-021); when the browser stalls, backpressure pauses the PTY reader rather than buffering
  unboundedly. Close/disconnect reaps the process group (SIGTERM→SIGKILL escalation) without killing
  the remote Zellij session (killing the local ssh only detaches; FR-019). Resize dims are clamped
  (e.g. 1–1000 cols, 1–1000 rows; FR-060).
- **Rationale**: Standard, well-understood PTY brokering; using `remo-host sessions attach` (not a raw
  remote command from the browser) keeps FR-015 (no browser-supplied targets/commands) intact.
- **Alternatives considered**: `ssh -W`/direct socket to browser (rejected — exposes SSH to browser,
  violates decision #4). Shipping a remote multiplexer like `ttyd`/`gotty` on hosts (rejected — new
  listener/daemon, violates decision #2).

## R5. SSH multiplexing per instance

- **Decision**: A per-instance `SshMaster` keyed by effective `(user, host, port, access_mode)` opens
  a ControlMaster (`ControlMaster=auto`, `ControlPersist`, ControlPath in `/run/remo-ssh`) once; the
  9-terminal example yields 3 masters, 9 attachments. Stale sockets are cleaned on startup and when a
  master's health check (`ssh -O check`) fails; a dead master surfaces as a clear per-terminal
  reconnect rather than corrupting siblings (FR-024, edge cases).
- **Rationale**: Directly implements decision #5; ControlPersist already proven in `shell_connect`.
- **Alternatives considered**: One master per attachment (rejected — nine full handshakes, the exact
  anti-goal). Global single master (rejected — cannot key by destination/access mode safely).

## R6. Frontend framework & terminal renderer

- **Decision**: TypeScript + **Vite + React** SPA. Terminal emulator = **`ghostty-web` pinned 0.4.0**
  (verified latest tag as of 2026-07-13, released 2025-12-09 — unchanged since spec authoring) loaded
  behind a Remo-owned `RendererAdapter` interface (create/open, write, onInput, fit/resize, focus,
  title, selection/copy, dispose — FR-037). An `XtermRenderer` implements the same interface as a
  drop-in fallback (SC-009) so a release-blocking Ghostty gap needs no backend change. The WASM asset
  is copied into `frontend/public` and served same-origin (no CDN, FR-038) with a restrictive CSP that
  permits `wasm-unsafe-eval` for the module and the same-origin WS endpoint (FR-051).
- **Rationale**: React's mature state/ecosystem suits grid/tab/focus + per-terminal lifecycle; the
  adapter satisfies the spec's explicit decoupling and fallback requirement. Pinning + local WASM meets
  supply-chain and CSP constraints.
- **Alternatives considered**: Svelte/Preact (smaller bundle; viable, but React chosen for contributor
  familiarity and richer testing tooling — bundle size is non-critical for a LAN tool). xterm.js as
  default (rejected by decision #6 — Ghostty is the chosen default, xterm is the fallback).

## R7. Web service packaging & optional extra

- **Decision**: Add `[project.optional-dependencies].web = ["fastapi", "uvicorn[standard]", ...]` in
  `pyproject.toml`. `cli/web.py` lazy-imports `remo_cli.web.*` inside command bodies; if the import
  fails, it raises `SystemExit` with `Install web support: pip install "remo-cli[web]"` (FR-041) —
  never a traceback. Nothing under the ordinary CLI import graph references FastAPI/Uvicorn (NFR-008),
  enforced by a unit test that imports `remo_cli.cli.main` with those modules blocked. Mirrors the
  established lazy-service convention (notifier, specs 007–009) but stays a **separate** process/
  image/config/lifecycle (FR-040).
- **Rationale**: Matches the "in-repo, separate packaging" decision and the existing lazy-import spirit
  for optional deps (boto3/hcloud/ansible today).
- **Alternatives considered**: Separate PyPI package/repo (rejected by decision #1 — must stay in the
  Remo repo/CLI family and share `core`). Merging with notifier (rejected — different trust boundary).

## R8. Container: multi-arch, non-root, read-only, SSM

- **Decision**: Multi-stage Dockerfile: stage 1 `node` builds the frontend; stage 2 a slim Python base
  with `openssh-client`, AWS CLI v2, and the Session Manager Plugin (arch-specific package selected via
  `TARGETARCH`, addressing the arm64 packaging edge case, FR-027/FR-042). Runtime: non-root UID/GID,
  read-only root FS, `no-new-privileges`, dropped caps, tmpfs for `/run/remo-ssh` and other runtime
  dirs (FR-044). `entrypoint.sh` runs `remo web check` as a readiness gate before `remo web serve`.
  `compose.example.yml` documents RO mounts distinctly: registry (`~/.config/remo` RO), SSH material
  (keys/config/known_hosts RO), optional AWS creds/profile RO — with prose separating "registry ≠ SSH
  auth material" (FR-026, US4 scenario 2).
- **Rationale**: Meets every hardening/packaging FR and the amd64+arm64 requirement (SC-006).
- **Alternatives considered**: Single-stage image with build tools (rejected — bloat + supply-area).
  Baking credentials into the image (rejected — FR-025/FR-028, US4 scenario 4).

## R9. Browser/network security model

- **Decision**: `POST /api/v1/terminals` returns a **single-use** token (opaque, ≥128-bit, default TTL
  **30 s** to WS upgrade, from Clarifications Q4) bound server-side to `(terminal_id, session_target)`;
  the browser presents it via a **WebSocket subprotocol** value (never URL/query, never logged;
  FR-049). Token is consumed atomically on successful upgrade (replay-resistant). Middleware validates
  `Host` against an allowlist and WS `Origin` against configured origins; no wildcard CORS (FR-048).
  Target authorization is re-checked at WS time against the current registry + discovery cache so a
  token can't retarget (FR-050). Reconnect mints a **fresh** token per attempt (Clarifications Q2:
  bounded auto-reconnect then manual). A later auth middleware can wrap the app without touching the
  terminal protocol (FR-053).
- **Rationale**: Implements decision #7 browser protections while keeping the trusted-LAN boundary
  explicit in docs (FR-052).
- **Alternatives considered**: Token in query string (rejected — leaks via proxy/request logs).
  Long-lived session cookie for terminals (rejected — replay window, not single-purpose).

## R10. Read-only registry mount & hot reload

- **Decision**: Add a config accessor that resolves the registry path **without** the `mkdir` side
  effect in today's `get_remo_home()` (which would fail/violate a read-only mount). Discovery reads the
  registry fresh on each refresh cycle (manual `POST /discovery/refresh` or the configurable interval),
  tolerating absent/empty/malformed/atomically-replaced files as typed states (FR-004/FR-006, edge
  cases); the service never writes the registry.
- **Rationale**: The existing `get_remo_home()` creates directories — unsafe for a RO mount; a
  read-path-only accessor is required. Re-reading per refresh gives hot reload without a file watcher.
- **Alternatives considered**: inotify watcher (rejected — extra complexity/caps; polling on the
  existing refresh cadence suffices for a home-lab registry that changes rarely).

## Resolved unknowns summary

| Unknown | Resolution |
|---|---|
| Discovery source of truth | `find`/`zellij list-sessions`/`docker ps` in `remo-host` (R1) |
| Protocol version policy | integer-major `[min,max]` range = `[1,1]` (R2) |
| SSH reuse vs rewrite | reuse `build_ssh_opts`, refactor ControlPath, share base-cmd builder (R3) |
| Terminal transport | asyncio PTY + `ssh -tt … remo-host sessions attach` (R4) |
| Multiplexing | per-instance ControlMaster in `/run/remo-ssh` (R5) |
| Frontend stack | Vite+React+TS, ghostty-web 0.4.0 behind adapter, xterm fallback (R6) |
| Packaging | `web` extra, lazy import, separate image/lifecycle (R7) |
| Image | multi-stage, multi-arch, non-root/RO, SSM plugin by `TARGETARCH` (R8) |
| Security | single-use 30 s WS subprotocol token, Host/Origin, re-auth at WS (R9) |
| RO registry | read-path-only accessor + per-refresh re-read (R10) |
