"""Liveness and readiness checks for the web service.

Implements the two endpoints from `contracts/rest-api.md`:

- ``GET /health`` — liveness. Always ``200`` while the process is up,
  independent of configuration validity (FR-045).
- ``GET /ready`` — readiness. ``200`` only when the registry is readable, an
  SSH identity is available, the SSH ControlMaster runtime dir is writable,
  and required executables are present; otherwise ``503`` with per-check
  detail (FR-045/FR-046).

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

router = APIRouter()

_SSH_IDENTITY_CANDIDATES = ("id_ed25519", "id_ecdsa", "id_rsa", "id_dsa")


@router.get("/health")
async def health() -> dict:
    """Liveness probe: the process is up. Never checks configuration."""
    return {"status": "alive"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness probe: registry + SSH identity + runtime dir + executables."""
    settings: WebSettings = getattr(request.app.state, "settings", None) or WebSettings()

    registry_status = _check_registry()
    ssh_identity_status = _check_ssh_identity()
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
    }

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

    if required_ok:
        return JSONResponse(status_code=200, content={"status": "ready", "checks": checks})

    return JSONResponse(
        status_code=503,
        content={
            "status": "not_ready",
            "checks": checks,
            "detail": _not_ready_detail(checks),
        },
    )


def _check_registry() -> str:
    """Registry is "readable" when its containing directory exists.

    Uses the read-only-safe accessor (no ``mkdir`` side effect) so this
    check is safe to run against a read-only-mounted (or entirely absent)
    ``~/.config/remo``. A missing registry *file* with a present directory
    is treated as "ok" (an empty/not-yet-populated registry, not a broken
    mount); a missing *directory* means nothing was mounted at all.
    """
    path = get_known_hosts_path_readonly()
    if not path.parent.is_dir():
        return "missing"
    if path.exists() and not os.access(path, os.R_OK):
        return "unreadable"
    return "ok"


def _check_ssh_identity() -> str:
    """Best-effort check for a readable SSH private key.

    Checks ``$REMO_WEB_SSH_IDENTITY_FILE`` (explicit override) and the
    conventional ``~/.ssh/id_*`` filenames. The registry is metadata, not
    authentication material (see the 503 detail message below) — this is a
    deliberately separate check.
    """
    explicit = os.environ.get("REMO_WEB_SSH_IDENTITY_FILE")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    ssh_dir = Path.home() / ".ssh"
    candidates.extend(ssh_dir / name for name in _SSH_IDENTITY_CANDIDATES)

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.R_OK):
            return "ok"
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
