"""Liveness and readiness checks for the web service.

Implements the two endpoints from `contracts/rest-api.md`:

- ``GET /health`` — liveness. Always ``200`` while the process is up,
  independent of configuration validity (FR-045).
- ``GET /ready`` — readiness. ``200`` when the service is configured and its
  prerequisites hold (registry readable, an SSH identity available, the SSH
  ControlMaster runtime dir writable, required executables present), and
  *also* ``200`` with ``"status": "unconfigured"`` when the service is
  healthy-awaiting-adoption (011-web-adopt research R11 / FR-001/FR-003: an
  unconfigured deployment must pass compose healthchecks, never crash-loop).
  ``broken`` keeps the ``503`` semantics with per-check detail
  (FR-045/FR-046).

These are best-effort checks appropriate for process-level health probing.
The full `remo web check` CLI diagnostic (reachability + protocol
compatibility against actual registered hosts) is implemented in T051.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from remo_cli.core.config import get_known_hosts_path_readonly
from remo_cli.web.config import WebSettings
from remo_cli.web.state import ConfigurationState, detect_state

router = APIRouter()

_SSH_IDENTITY_CANDIDATES = ("id_ed25519", "id_ecdsa", "id_rsa", "id_dsa")

_UNCONFIGURED_DETAIL = (
    "Awaiting adoption. Run `remo web adopt <service-url>` from a workstation "
    "to push a registry and authorize this service's identity."
)

#: Broken-state detail used when the four per-check probes all pass but the
#: configuration is still unusable (e.g. a half-generated service keypair).
_BROKEN_DETAIL = (
    "Configuration is present but unusable. If the service identity is "
    "damaged (half-generated keypair), reset the state volume; otherwise "
    "check that the mounted registry/key files are readable."
)


@router.get("/health")
async def health() -> dict:
    """Liveness probe: the process is up. Never checks configuration."""
    return {"status": "alive"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness probe: configuration state + registry/identity/runtime/executables."""
    settings: WebSettings = getattr(request.app.state, "settings", None) or WebSettings()
    state = detect_state(settings)

    registry_status = _check_registry()
    ssh_identity_status = _check_ssh_identity(settings)
    runtime_dir_status = _check_runtime_dir(settings.ssh_control_dir)
    ssh_status = "ok" if shutil.which("ssh") else "missing"
    aws_cli_status = "ok" if shutil.which("aws") else "missing"
    ssm_plugin_status = "ok" if shutil.which("session-manager-plugin") else "missing"

    checks = {
        "registry": registry_status,
        "ssh_identity": ssh_identity_status,
        "runtime_dir": runtime_dir_status,
        "ssh": ssh_status,
        "aws_cli": aws_cli_status,
        "ssm_plugin": ssm_plugin_status,
        # Additive operator-auth posture (012-web-adopt-pairing, FR-013): a
        # constant diagnostic that surfaces the weaker network-restricted mode.
        # It is independent of pairing-session state (dormant/live/post-adoption)
        # so readiness output stays byte-stable across those states (SC-008),
        # and it never gates readiness (not read by required_ok below).
        "operator_auth": _operator_auth_posture(settings),
    }

    # Unconfigured is a *healthy* 200 state (research R11): the container is
    # doing its job — awaiting adoption — and must pass compose healthchecks
    # (SC-006 no-crash-loop). Registry/identity absence is expected here and
    # never gates readiness; missing runtime prerequisites (runtime dir, ssh
    # executable) are still "broken", not "unconfigured" (US2 scenario 5),
    # and fall through to the 503 path below.
    if (
        state is ConfigurationState.UNCONFIGURED
        and runtime_dir_status == "ok"
        and ssh_status == "ok"
    ):
        return JSONResponse(
            status_code=200,
            content={
                "status": "unconfigured",
                "checks": checks,
                "detail": _UNCONFIGURED_DETAIL,
            },
        )

    # aws_cli/ssm_plugin only gate readiness when SSM targets are actually
    # registered; that requires reading the registry contents (deferred to
    # `remo web check`, T051). Here they gate liveness-of-config only via
    # the four checks explicitly required by FR-045: registry, ssh_identity,
    # runtime_dir, ssh.
    required_ok = (
        registry_status == "ok"
        and ssh_identity_status == "ok"
        and runtime_dir_status == "ok"
        and ssh_status == "ok"
    )

    # `broken` keeps today's 503 even when the four probes above happen to
    # pass (e.g. a half-generated service keypair alongside a mounted user
    # identity): unusable configuration is never "ready".
    if required_ok and state is not ConfigurationState.BROKEN:
        return JSONResponse(status_code=200, content={"status": "ready", "checks": checks})

    if state is ConfigurationState.UNCONFIGURED:
        # Only reachable when a runtime prerequisite failed; mask the
        # (expected, non-gating) registry/identity statuses so the detail
        # names the actual problem.
        detail = _not_ready_detail({**checks, "registry": "ok", "ssh_identity": "ok"})
    else:
        detail = _not_ready_detail(checks)
        if detail == "Not ready." and state is ConfigurationState.BROKEN:
            detail = _BROKEN_DETAIL

    return JSONResponse(
        status_code=503,
        content={
            "status": "not_ready",
            "checks": checks,
            "detail": detail,
        },
    )


def _operator_auth_posture(settings: WebSettings) -> str:
    """Label the operator-auth posture for readiness diagnostics (FR-013).

    Derived directly from config (never raises) so readiness is robust: the
    fail-fast for forward-auth-without-a-header lives in app startup, so a
    running service is always in a valid posture.
    """
    mode = settings.operator_auth.strip().lower()
    if mode == "forward":
        return "forward"
    if mode == "none":
        return "network-restricted"
    if mode == "":
        return "unconfigured"
    return "unknown"


def _check_registry() -> str:
    """Registry is "readable" when its containing directory exists.

    Uses the read-only-safe accessor (no ``mkdir`` side effect) so this
    check is safe to run against a read-only-mounted (or entirely absent)
    ``~/.config/remo``. A missing registry *file* with a present directory
    is treated as "ok" (an empty/not-yet-populated registry, not a broken
    mount); a missing *directory* means nothing was mounted at all.
    """
    path = get_known_hosts_path_readonly()
    # `Path.is_dir()`/`Path.exists()` swallow only ENOENT-ish errors and
    # *raise* on EACCES, so probing a registry this process cannot traverse
    # escapes as a PermissionError traceback and the os.access() branch
    # below is never reached. That is a real deployment case, not a corner:
    # bind mounts keep their host ownership, so a host user whose uid isn't
    # 1000 mounting a 0700 ~/.config/remo produces exactly this. Report it
    # as "unreadable" (callers turn that into a 503 / a [FAIL] line with
    # remediation) rather than crashing.
    try:
        if not path.parent.is_dir():
            return "missing"
        if path.exists() and not os.access(path, os.R_OK):
            return "unreadable"
    except OSError:
        return "unreadable"
    return "ok"


def _check_ssh_identity(settings: WebSettings | None = None) -> str:
    """Best-effort check for a readable SSH private key.

    Checks ``$REMO_WEB_SSH_IDENTITY_FILE`` (explicit override), the service
    keypair under ``<REMO_HOME>/web-identity/`` (011-web-adopt T028 — an
    *adopted* deployment authenticates with its own generated identity, so it
    must pass this probe), and the conventional ``~/.ssh/id_*`` filenames.
    The registry is metadata, not authentication material (see the 503 detail
    message below) — this is a deliberately separate check.
    """
    settings = settings or WebSettings()
    explicit = os.environ.get("REMO_WEB_SSH_IDENTITY_FILE")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(settings.service_private_key_path)
    ssh_dir = Path.home() / ".ssh"
    candidates.extend(ssh_dir / name for name in _SSH_IDENTITY_CANDIDATES)

    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(candidate, os.R_OK):
                return "ok"
        except OSError:
            continue
    return "missing"


def _check_runtime_dir(control_dir: str) -> str:
    """The SSH ControlMaster socket directory must exist and be writable."""
    path = Path(control_dir)
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return "not_writable"
    if path.is_dir() and os.access(path, os.W_OK):
        return "ok"
    return "not_writable"


def _not_ready_detail(checks: dict[str, str]) -> str:
    if checks["registry"] != "ok":
        return (
            "Registry is not readable. Mount the Remo registry read-only "
            "(see docs) at the configured REMO_HOME / XDG_CONFIG_HOME path."
        )
    if checks["ssh_identity"] != "ok":
        return (
            "Registry is readable but no SSH identity is mounted. The "
            "registry is metadata, not authentication material. Mount a "
            "private key read-only (see docs)."
        )
    if checks["runtime_dir"] != "ok":
        return (
            "The SSH ControlMaster runtime directory is not writable. "
            "Mount a writable tmpfs at the configured path (default "
            "/run/remo-ssh; see docs)."
        )
    if checks["ssh"] != "ok":
        return "The 'ssh' executable was not found on PATH inside the container."
    return "Not ready."
