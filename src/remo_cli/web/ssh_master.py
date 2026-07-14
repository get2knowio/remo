"""Per-instance SSH ControlMaster lifecycle management (T035).

Design decision (why this module is small):
--------------------------------------------
OpenSSH's ``ControlMaster=auto`` + ``ControlPersist`` — already emitted by
:func:`remo_cli.core.ssh.build_ssh_opts` whenever ``multiplex=True`` — does the
multiplexing implicitly, so no dedicated ``ssh -M -N`` master supervisor is
needed:

* The FIRST connection to a given ``ControlPath`` transparently becomes the
  master and persists (``ControlPersist=60s``) after it exits.
* Subsequent connections to the same ``ControlPath`` multiplex over that
  master automatically — the 9-terminal / 3-instance example collapses to 3
  real SSH handshakes with no explicit coordination here (R5).
* If the control socket is stale/dead, ``ControlMaster=auto`` silently falls
  back to a fresh direct connection, which then becomes the new master. So a
  dead master surfaces as a transparent per-terminal reconnect, never a
  corrupted sibling (FR-024).

Given that, this module's only genuinely load-bearing job is **stale-socket
cleanup on startup**: sockets left in ``$REMO_SSH_CONTROL_DIR`` by a previously
crashed web-service process must be swept so they neither accumulate nor get
mistaken for a live master. Everything else about mastering is implicit, so we
deliberately do not build a manual master-process supervisor.
"""

from __future__ import annotations

import glob
import os
import subprocess

from remo_cli.models.host import KnownHost

__all__ = ["control_master_key", "stale_socket_cleanup"]

# build_ssh_opts writes ControlPath=f"{dir}/remo-%r@%h-%p"; the sockets it
# leaves therefore all share this prefix.
_SOCKET_PREFIX = "remo-"


def control_master_key(host: KnownHost) -> tuple[str, str, str]:
    """Effective ControlMaster key for *host* (R5).

    ``KnownHost`` carries no explicit SSH port field, and the effective SSH
    destination differs by access mode (direct targets ``user@host``; SSM
    targets ``user@instance_id`` via a ProxyCommand). So we key by the tuple
    that actually determines the ControlPath/destination:
    ``(user, effective_host, access_mode)``. This is used only for
    observability/dedup reasoning — the socket itself is managed by OpenSSH.
    """
    effective_host = host.instance_id if host.access_mode == "ssm" else host.host
    return (host.user, effective_host, host.access_mode or "direct")


def _parse_target_from_socket(path: str) -> str | None:
    """Recover an ``ssh -O`` destination from a ``remo-<user>@<host>-<port>`` socket."""
    base = os.path.basename(path)
    if not base.startswith(_SOCKET_PREFIX):
        return None
    body = base[len(_SOCKET_PREFIX) :]
    if "@" not in body:
        return None
    user, rest = body.split("@", 1)
    host = rest.rsplit("-", 1)[0] if "-" in rest else rest
    if not user or not host:
        return None
    return f"{user}@{host}"


def _socket_is_live(path: str, target: str) -> bool:
    """True when ``ssh -O check`` reports a live master for *path*."""
    try:
        result = subprocess.run(
            ["ssh", "-O", "check", "-o", f"ControlPath={path}", target],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def stale_socket_cleanup(control_dir: str) -> list[str]:
    """Remove dead ControlMaster sockets left in *control_dir*.

    Best-effort and side-effect-tolerant: a missing directory is a no-op, and
    any per-socket error is swallowed (the next attachment would transparently
    establish a fresh master anyway). Returns the list of paths removed, for
    logging/observability by the caller.
    """
    if not control_dir or not os.path.isdir(control_dir):
        return []

    removed: list[str] = []
    for path in glob.glob(os.path.join(control_dir, _SOCKET_PREFIX + "*")):
        target = _parse_target_from_socket(path)
        # A socket we can't attribute to a target, or one whose master no
        # longer answers, is stale — unlink it.
        if target is not None and _socket_is_live(path, target):
            continue
        try:
            os.unlink(path)
            removed.append(path)
        except OSError:
            continue
    return removed
