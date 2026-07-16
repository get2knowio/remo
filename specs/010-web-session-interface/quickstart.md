# Quickstart & Validation: Remo Web Session Interface

Runnable validation of the feature end-to-end. Assumes the branch is built. Details of payloads/frames
live in [contracts/](./contracts/) and [data-model.md](./data-model.md) — not repeated here.

> ⚠️ **Security boundary**: the web service grants shell access to **every** configured instance. Bind
> it only to a trusted LAN / Tailscale interface or a loopback reverse proxy. Never expose it publicly
> (FR-047/FR-052).

## Prerequisites

- Instances configured with the updated `user_setup` role so `~/.local/bin/remo-host` exists
  (`ansible-playbook … && ssh <host> remo-host capabilities --json` returns JSON).
- A Remo registry at `~/.config/remo/known_hosts` with ≥1 reachable instance.
- A dedicated SSH identity that authenticates non-interactively to those instances.
- For AWS/SSM entries: AWS CLI v2 + Session Manager Plugin available (bundled in the image) and a
  read-only credentials/profile mount.
- Docker with Compose (amd64 or arm64) for the container path.

## A. Local dev run (no container)

```bash
uv sync --extra web            # install web extra (FastAPI/Uvicorn/…) — NOT part of the normal CLI
uv run remo web check          # validates registry, SSH identity, runtime dir, executables, reachability, protocol
uv run remo web serve --host 127.0.0.1 --port 8080
```
Expected: `remo web check` prints per-check PASS/FAIL with actionable remediation; `serve` logs a
ready readiness state. Open `http://127.0.0.1:8080`.

**Negative check (NFR-008 / FR-041)** — the ordinary CLI must not need web deps:
```bash
uv run remo --help             # works without the web extra installed
uv run remo web serve          # WITHOUT web extra → concise "pip install remo-cli[web]" message, no traceback
```

## B. Container run (home lab)

```bash
cd docker
docker compose -f compose.example.yml up -d
docker compose exec remo-web remo web check
curl -fsS http://<lan-ip>:8080/api/v1/ready   # 200 ready
```
`compose.example.yml` mounts (all read-only): registry (`~/.config/remo`), SSH material
(key/config/known_hosts), optional AWS creds; declares tmpfs for `/run/remo-ssh`; sets non-root
UID/GID, read-only rootfs, `no-new-privileges`, dropped caps, healthcheck, restart policy.

## Validation scenarios (map to spec Success Criteria)

### V1 — Discover nine targets (SC-001, US1)
Use the 3-instance × 3-project fixture. Load the dashboard.
- Expect all nine targets grouped by provider/instance within ~10 s, each showing Zellij + devcontainer
  state, results rendering incrementally (SC-010). No terminal opened yet.

### V2 — Host isolation & typed status (SC-004, US1.2/US1.4)
Take one instance offline; give another the version marker but no `remo-host`.
- Offline instance shows a **retryable** error; the `no_remo_host` instance shows a non-retryable
  **update** remediation. The other six targets remain usable.

### V3 — Open one terminal, parity (SC-002, US2)
Open a stopped devcontainer project.
- Startup output streams; final shell is inside the devcontainer. First warm-session output < 5 s
  (SC-011). Then run `remo shell <host> -p <project>` from a CLI → attaches to the **same** Zellij
  session.

### V4 — Open all nine, no cross-routing (SC-003, US3)
"Open all". Type a unique marker in each of the nine terminals; switch through grid/tab/focused modes.
- Input goes only to the focused terminal; output is never cross-routed even with repeated project
  names; provider/instance/project labels always visible.

### V5 — Reconnect leaves remote session intact (SC-005, US2.3)
Kill one browser WS (or the local ssh attachment).
- Local PTY/SSH is reaped; the remote Zellij session survives; bounded auto-reconnect (then a manual
  "Reconnect") reaches the same session; siblings unaffected.

### V6 — Security rejections (SC-007)
- `POST /terminals` with a fabricated/undiscovered `session_target_id` → 404.
- WS handshake from a wrong `Origin` → rejected (1008).
- Reuse a consumed or expired `ws_token` → rejected (1008). Token never appears in server logs or URLs.

### V7 — Caps & backpressure (FR-021/FR-022, NFR-004)
- Exceed the per-client (16) or global (32) cap → `429` with a clear message.
- `yes` in one terminal for a while → memory stays bounded; nine terminals run 1 h with no leaks or
  cross-routing (SC-013).

### V8 — SSM path (SC-006)
With an AWS/SSM entry and creds mounted, discovery and terminal attach follow the SSM route (same as
CLI). On amd64 and arm64 images.

### V9 — Graceful shutdown (SC-014)
`docker compose stop` → service stops accepting new terminals, reaps attachments within a bounded
interval, remote Zellij sessions remain (verify via `remo shell -p`).

## Test entry points (see plan.md → tests/ tree)

- Unit: `uv run pytest tests/unit/core/test_remo_host_client.py tests/unit/core/test_ssh_controlpath.py tests/unit/web/`
- Ansible idempotency: fresh-host + already-configured-host runs of the `remo-host` install task
  (both branches), plus `tests/unit/test_ansible_templates.py` assertions on the template.
- Integration: `tests/integration/test_remo_host_e2e.py` (healthy/unreachable/malformed/incompatible/
  slow disposable SSH targets); `tests/integration/test_nine_terminals.py` (3×3 fixture).
- Browser: Playwright suite (grid/tab/focus, keyboard routing, reconnect, Origin, WASM load).
- Image: `docker buildx` amd64+arm64, non-root/read-only run, readiness, required-mount validation.

## Validation results (T065)

Run 2026-07-13 against this implementation, in a sandboxed dev container with Docker (network-
capable) and Node/npm available but no browser/display. Each scenario below is marked against real
automated evidence gathered this session; scenarios needing a real browser or a production multi-host
deployment are marked accordingly rather than claimed as manually clicked-through.

| # | Scenario | Result | Evidence |
|---|---|---|---|
| V1 | Discover nine targets | **PASS** (automated) | `tests/integration/test_nine_terminals.py` discovers 3×3 real targets via `DiscoveryService` against disposable SSH containers; `tests/perf/test_latency.py` measured discovery of 9 targets in 0.40s (budget 10s, SC-010). |
| V2 | Host isolation & typed status | **PASS** (automated) | `tests/integration/test_remo_host_e2e.py` (healthy/unreachable/malformed/incompatible/slow/no-remo-host all produce isolated per-instance typed status); `tests/unit/web/test_discovery.py` (one host's failure never blocks others). |
| V3 | Open one terminal, parity | **PASS** (automated) | `tests/integration/test_terminal_attach.py` (POST→WS→bytes→resize→disconnect against a disposable target); `tests/integration/test_web_cli_parity.py` (web attach and the CLI's `build_project_launch_remote_cmd`/`build_ssh_base_cmd` path reach the same `project-launch` invocation); `tests/perf/test_latency.py` measured first warm-session output in 0.19s (budget 5s, SC-011). |
| V4 | Open all nine, no cross-routing | **PASS** (automated) | `tests/integration/test_nine_terminals.py` opens all 9 real PTY/WS terminals concurrently (incl. a project name repeated across all 3 instances) and asserts each terminal only ever receives its own marker. |
| V5 | Reconnect leaves remote session intact | **PARTIAL** — backend automated, browser UI not exercised | Backend: `tests/unit/web/test_backpressure.py`/`test_terminal_resize.py` prove local PTY/SSH reap on close without touching the remote session; `frontend/src/terminal/TerminalConnection.ts`'s bounded-auto-reconnect→manual logic and `tests/e2e/reconnect.spec.ts` are written and ready but require a real browser + backend fixture (`REMO_E2E_BACKEND_URL`), not available in this sandbox (no display/Playwright runtime installed). |
| V6 | Security rejections | **PASS** (automated) | `tests/integration/test_security_rejections.py`: fabricated target → 404, bad Origin → 1008, replayed/expired token → 1008, and a `caplog`-based assertion that issued token values never appear in logs. |
| V7 | Caps & backpressure | **PASS** (automated, smoke-tier soak) | `tests/unit/web/test_terminals_api.py` (429 on global/per-client cap); `tests/integration/test_nine_terminals_soak.py` (9 terminals under sustained load; full ≥1h tier is opt-in via `REMO_RUN_SOAK_TEST=1`, see that file for the extended-run numbers actually captured this session). |
| V8 | SSM path | **PARTIAL** — argv-level automated, no live AWS | `tests/integration/test_web_cli_parity.py::TestSsmArgvParity` proves the web and CLI paths build byte-identical SSM `ProxyCommand` argv from the same `KnownHost`; `tests/image/test_docker_image.py` (opt-in `REMO_RUN_IMAGE_TESTS=1`) verified AWS CLI v2 + session-manager-plugin are present and architecture-correct in the built image for both amd64 and arm64. No live AWS SSM target was available to drive an actual session. |
| V9 | Graceful shutdown | **PARTIAL** — manually verified, not automated | `remo web serve`'s lifespan sets `app.state.shutting_down` before reaping (`POST /terminals` returns 503 once shutdown begins); manually verified via `curl`/SIGTERM during T052 that the process logs "Application shutdown complete" and exits cleanly. No automated test drives a live SIGTERM against an open terminal and asserts the remote Zellij session survives — would need a real disposable target kept alive across a service restart, which is a heavier fixture than this session built. |

**Summary**: 6/9 scenarios fully automated and passing; 3/9 (V5, V8, V9) are backend-proven with the
browser-, live-AWS-, or full-restart-dependent portion documented as not executable in this sandbox
rather than claimed. This mirrors how T044/T049/T060 handled genuinely infrastructure-gated coverage
elsewhere in this feature.
