# Phase 0 Research: Notifier Sidecar

This document resolves the open technical decisions implied by the spec and the repo's existing conventions. Each entry is a Decision / Rationale / Alternatives triple. No `NEEDS CLARIFICATION` markers remained in the spec after `/speckit.clarify`; the items below are technical-approach choices, not requirement gaps.

## R1. FastAPI + Telegram on one asyncio event loop

**Decision**: Run the FastAPI app under uvicorn programmatically (`uvicorn.Server(Config(...)).serve()`); start the `python-telegram-bot` `Application` (initialize → start → `updater.start_polling()`) inside the FastAPI **lifespan** startup and stop it (stop polling → stop → shutdown) in lifespan shutdown. Both share the single running asyncio loop.

**Rationale**: The spec mandates long-polling (no public URL) and a single shared loop. PTB v21's `Application` is asyncio-native and composes with an externally-owned loop when driven via its lower-level `initialize/start/updater.start_polling` methods rather than the blocking `run_polling()` (which calls `asyncio.run` and owns the loop — incompatible with uvicorn already owning it). The lifespan hook is the documented FastAPI seam for background services.

**Alternatives considered**:
- `Application.run_polling()` directly — rejected: it creates/owns its own loop and blocks, conflicting with uvicorn.
- Separate thread for the bot with its own loop — rejected: cross-loop `asyncio.Event` resolution is error-prone; the spec explicitly wants one loop.
- Webhook mode — rejected by constraint (needs a public URL).

## R2. Pending-approval registry and timeout race

**Decision**: `PendingApprovals` holds `approval_id -> PendingApproval`, where each entry owns an `asyncio.Future[ApprovalDecision]`. Intake flow: validate → reject if `approval_id` already pending (409) → reject if at capacity (503) → deliver notification via transport → only then register the entry → `await asyncio.wait_for(future, timeout=clamped_timeout)`. On `TimeoutError`, resolve to fail-secure deny (reason `timeout`), call `transport.cancel`/edit, and return 408. A callback resolves the Future via `loop.call_soon_threadsafe`-safe path (same loop, so direct `set_result` guarded by "still pending"). All mutations go through an `asyncio.Lock` to keep cap-check + insert atomic.

**Rationale**: Futures (not Events) carry the decision payload directly. Registering only *after* successful send (R5) keeps the cap meaningful and satisfies FR-010a. The lock makes the capacity gate (FR-034) and duplicate-id gate (FR-003a) race-free. "Still pending" guards make late/duplicate callbacks no-ops (edge cases).

**Alternatives considered**:
- `asyncio.Event` + side dict for the decision — rejected: two structures to keep consistent; Future is one.
- Register before send — rejected: would hold a cap slot for a request that failed to notify (violates FR-010a intent).
- Per-entry lock only — rejected: capacity is a global invariant, needs a registry-level lock.

## R3. Timeout clamping and defaults

**Decision**: Effective timeout = `min(max(request.timeout_seconds or default, 1), max_timeout_seconds)`, with `default_timeout_seconds` and `max_timeout_seconds` from config (defaults 300 / 1800). Clamp silently (no error) per FR-006.

**Rationale**: Matches FR-006 directly; a silent clamp is friendlier than rejecting and keeps the caller contract simple. A 1 s floor prevents zero/negative pathological waits.

**Alternatives considered**: 400 on over-max — rejected: FR-006 says clamp, not reject.

## R4. Fail-secure decision algebra

**Decision**: A single internal resolver yields exactly one terminal outcome per approval: `ALLOW` only from an authorized human tapping Approve; everything else (`Deny` tap, timeout, transport/send failure, shutdown, lost connection) is `DENY` or no response at all. HTTP mapping: human decision → 200; timeout → 408 with `{decision: deny, reason: "timeout"}`; validation → 400; duplicate-id → 409; transport down / no config / shutting down / at capacity / send failure → 503.

**Rationale**: Centralizes FR-008's invariant in one place that tests can target (SC-005). The 409 for duplicate-id (FR-003a) is the one non-spec-enumerated code; documented in the contract as a sub-case of "client error" from FR-003a/FR-001's 400 family — see R9.

**Alternatives considered**: Returning 200-with-deny on transport failure — rejected: 503 lets the caller distinguish "no human saw this" from "human denied".

## R5. Getting the image build context onto the host

**Decision**: Force-include the minimal Docker build context into the wheel next to the already-included `ansible/` tree, at `remo_cli/notifier_build/` (containing `Dockerfile`, `pyproject.toml`, `uv.lock`, `README.md`, and the `remo_cli` source needed to `pip install ".[notifier]"`). The `remo_notifier` role copies this directory to `remo_notifier_source_dir` on the host and runs `community.docker.docker_image` with `source: build`. A resolver in the role (or passed as an extra-var by the CLI) locates the installed build context via `importlib`/package path, mirroring how `core/config.get_ansible_dir()` resolves the bundled `ansible/`.

**Rationale**: A `pip install remo-cli` user has no repo checkout, yet the spec requires v1 to build on the host. Shipping the build context in the wheel (the same trick already used for `ansible/`) makes `remo notifier deploy` work from a bare install. The Dockerfile at repo root (`notifier/Dockerfile`, per spec) is the source of truth; the wheel-included copy is produced by a hatch force-include mapping.

**Alternatives considered**:
- Build in CI, pull from ghcr.io — explicitly deferred by the spec (out of scope v1).
- Copy from the user's repo checkout — rejected: not present for pip installs.
- Build an sdist on the host — rejected: heavier and still needs the source shipped.

**Layout contract (U1)**: the bundled context at `remo_cli/notifier_build/` MUST reproduce a repo-root-relative tree — `Dockerfile`, `pyproject.toml`, `README.md`, `uv.lock`, `src/remo_cli/…` — so the *same* `notifier/Dockerfile` builds identically from the repo root and from the on-host copy. The role (T025) copies this directory verbatim, preserving layout; the CLI (T028) resolves the installed path and passes it as an extra-var. `uv.lock` is shipped only for an optional `uv sync --frozen` locked build; a plain `uv pip install ".[notifier]"` does not consume it (I1).

**Risk/Note**: Force-including `src/` doubles some files in the wheel. Keep the included context minimal (only what `.[notifier]` install needs). Confirm image stays <250 MB (AC-2) — Python 3.13-slim + these deps is ~180–220 MB.

## R6. Secret handling and rotation

**Decision**: The bot token is read once from `bot_token_file` (default `/run/secrets/telegram_bot_token`) at startup and kept only in memory. The TOML config never contains the token. Rotation: operator rewrites the secret file and restarts the service; additionally the process installs a `SIGHUP` handler that re-reads the secret file and re-initializes the bot (supports the spec's "rotation via kill -HUP" note) — best-effort, not a v1 acceptance criterion. structlog is configured with a processor that drops/reduces known-sensitive keys; the token is never passed to a logger.

**Rationale**: Satisfies FR-019 (separate file, never config), FR-017 (no secrets in logs), and the out-of-scope note that HUP-rotation is supported without a CLI wrapper.

**Alternatives considered**: Env-var token — rejected: env is visible in `/proc` and process listings; file with `0400` is tighter and rotatable.

## R7. structlog configuration matching remo

**Decision**: Configure structlog at process start: ISO timestamp, level, logger name, JSON renderer in the container (machine-readable for journald), key-value renderer if a TTY. INFO+ emits only structural fields (`approval_id`, `decision`, `latency_ms`, `transport`, `pending_count`). A dedicated DEBUG path may log operation/workspace/raw-body. A redaction processor strips `bot_token`, `authorization`, and full request bodies from any event not explicitly at DEBUG.

**Rationale**: FR-017 + the "do not log secrets" constraint. JSON to stdout is the right shape for `docker logs` → journald → `remo notifier logs`.

**Alternatives considered**: stdlib logging — rejected: spec names structlog and it gives cleaner structured redaction.

## R8. CLI subcommand transport (SSH vs direct HTTP)

**Decision**: `remo notifier {status,logs,test,restart}` operate **over SSH to the host** using the existing `core/ssh.build_ssh_opts` + a subprocess `ssh` invocation, consistent with `remo shell`/`cp`. `status` runs `curl -sf http://{bind}:{port}/v1/health` on the host; `test` runs `curl` POSTing a test `ApprovalRequest` to the same bridge address from the host; `logs` runs `journalctl -u remo-notifier.service [-f] [-n N]`; `restart` runs `sudo systemctl restart remo-notifier.service`. `deploy` runs the `notifier_deploy.yml` playbook via `core/ansible_runner.run_playbook` with `-i "{host},"` and `-e ansible_user=...`, exactly like `hetzner update`. Every subcommand resolves the host via `core/known_hosts` + `core/picker` (fuzzy pick when no host given, FR-031).

**Rationale**: The listener is bound to the bridge and unreachable from the laptop (FR-021), so the laptop cannot curl it directly — it must hop through the host over SSH. Reusing the established SSH/ansible/picker helpers keeps UX identical to existing commands and adds zero laptop runtime deps.

**Alternatives considered**:
- Direct HTTP from laptop — rejected: bridge binding makes it unreachable by design.
- A second control endpoint exposed publicly — rejected: widens attack surface against FR-021.

## R9. HTTP status code for duplicate approval_id

**Decision**: Return **409 Conflict** for a duplicate-of-pending `approval_id` (FR-003a), documented in the contract alongside the spec's 400/408/503. Body: `{ "error": "duplicate_approval_id", "approval_id": "..." }`.

**Rationale**: 409 is the precise semantic ("conflict with current resource state — already pending"). The spec phrased FR-003a as "a client-error signal"; 409 is the most accurate client-error code and is additive to the spec's enumerated set, not contradictory.

**Alternatives considered**: 400 — acceptable per the literal spec wording but less precise; 200-attach (idempotent) was rejected during clarification (Option B not chosen).

## R10. Ansible collection + role wiring

**Decision**: Add `community.docker (>=4.0.0)` to `ansible/requirements.yml` (used by `community.docker.docker_image`). The `remo_notifier` role declares `meta/main.yml` `dependencies: [{role: docker}]`. A new top-level `notifier_deploy.yml` playbook (`hosts: all`, `become: true`) applies the role; `remo notifier deploy` invokes it. Inclusion in the standard configure flow is via `configure_dev_tools.yml` guarded by `configure_remo_notifier | default(true) | bool` (FR-033), following the existing toggle pattern.

**Rationale**: `docker_image`/`docker_container`/systemd are the idiomatic Ansible path here; `community.docker` is the only missing collection. The standalone `notifier_deploy.yml` lets `remo notifier deploy <host>` target an already-provisioned host without re-running the whole configure, while the toggle lets first-time provisioning include it.

**Alternatives considered**:
- `docker_container` module instead of a systemd unit running `docker run` — rejected: the spec mandates a systemd unit with specific `docker run` hardening flags and `Restart=always`; a templated unit matches the spec exactly and keeps restart semantics in systemd.
- Reuse a provider configure playbook — rejected: would couple the notifier to one provider; a dedicated playbook is provider-agnostic.

## R11. Test strategy and new dev dependencies

**Decision**: Add `pytest-asyncio` and `httpx` to the `dev` extra. Tests: `test_state.py` drives the registry directly (register/resolve/timeout/cancel/cap/duplicate-id, including concurrency via `asyncio.gather`); `test_server.py` uses FastAPI `TestClient` (sync) for status-code coverage and `httpx.AsyncClient` + ASGI transport for the await-until-decision and timeout (408) paths with a fake transport that resolves on command; `test_telegram.py` injects a mocked `Bot` into the PTB `Application` and asserts message text, inline keyboard `callback_data`, the callback→resolve path, message edits, and `cancel`; `test_cli_notifier.py` patches the SSH/subprocess and ansible-runner seams and a fake known-hosts registry. Target >85% line coverage on `src/remo_cli/notifier/` (SC/AC-8).

**Rationale**: Mirrors the spec's testing section and existing `tests/` patterns (`tests/unit`, `tests/integration`, `conftest.py`). A fake `NotificationTransport` makes server timeout/fail-secure tests deterministic without Telegram.

**Alternatives considered**: Live Telegram in CI — rejected: non-deterministic, needs secrets; reserved for the manual `remo notifier test` acceptance step.

## Resolved unknowns summary

| Topic | Resolution |
|-------|-----------|
| One-loop FastAPI + PTB | lifespan start/stop, low-level PTB API (R1) |
| Registry + timeout race | Future + registry lock, register-after-send (R2) |
| Timeout clamp | min/max with config bounds (R3) |
| Fail-secure mapping | central resolver; 200/408/400/409/503 (R4, R9) |
| Build context to host | force-include in wheel, role copies + builds (R5) |
| Secrets | file-only token, in-memory, HUP re-read, redaction (R6, R7) |
| CLI transport | SSH hop reusing existing helpers (R8) |
| Ansible wiring | community.docker, role meta→docker, deploy playbook + toggle (R10) |
| Tests/deps | pytest-asyncio + httpx, fake transport, Bot mock (R11) |

All items resolved — ready for Phase 1.
