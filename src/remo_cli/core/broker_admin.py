"""Admin-socket client for the in-instance remo-broker daemon.

The broker exposes an admin Unix socket at ``/run/remo-broker/admin.sock``
speaking NDJSON (per get2knowio/remo-broker docs/wire-protocol.md). The
socket is mode 0600 owned by ``root`` / the ``remo-broker`` user, so the
laptop talks to it by SSHing to the instance and running a small ``sudo
python3`` shim that bridges stdin/stdout to the socket.

Used by ``cli/rotate.py`` to drive the ``rotate-bootstrap`` op after a
fresh sub-token has been pushed to ``/etc/remo-broker/bootstrap-token``.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any

ADMIN_SOCKET_PATH = "/run/remo-broker/admin.sock"

# Self-contained Python shim. Reads the NDJSON request from argv[1], sends
# it over the admin socket, returns the first response line on stdout.
_BRIDGE_SCRIPT = (
    "import socket,sys\n"
    "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
    "s.settimeout(15)\n"
    f"s.connect({ADMIN_SOCKET_PATH!r})\n"
    "s.sendall(sys.argv[1].encode()+b'\\n')\n"
    "buf=b''\n"
    "while b'\\n' not in buf:\n"
    "    chunk=s.recv(65536)\n"
    "    if not chunk: break\n"
    "    buf+=chunk\n"
    "sys.stdout.buffer.write(buf)\n"
)


class BrokerAdminError(RuntimeError):
    """Raised when an admin-socket call fails (transport or broker-reported error)."""


def _send(
    request: dict[str, Any],
    *,
    ssh_host: str,
    ssh_user: str,
    ssh_options: list[str] | None = None,
) -> dict[str, Any]:
    """SSH to *ssh_host* and execute one admin-socket request/response cycle.

    Returns the broker's decoded JSON response.
    """
    payload = json.dumps(request, separators=(",", ":"))
    remote_cmd = (
        f"sudo python3 -c {shlex.quote(_BRIDGE_SCRIPT)} {shlex.quote(payload)}"
    )
    base_opts = ssh_options if ssh_options is not None else [
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]
    cmd = ["ssh", *base_opts, f"{ssh_user}@{ssh_host}", remote_cmd]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or "(no stderr)"
        raise BrokerAdminError(
            f"admin-socket SSH bridge failed (rc={proc.returncode}): {stderr}"
        )
    raw = (proc.stdout or "").strip()
    if not raw:
        raise BrokerAdminError(
            "admin-socket returned no response (broker daemon may not be running)"
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrokerAdminError(
            f"admin-socket returned non-JSON: {raw[:200]!r}"
        ) from exc


def rotate_bootstrap(
    *,
    ssh_host: str,
    ssh_user: str,
    ssh_options: list[str] | None = None,
) -> None:
    """Tell the broker to re-read its bootstrap token + re-open ``fnox.toml``.

    The caller must have already written the fresh token to
    ``/etc/remo-broker/bootstrap-token`` on the instance. The broker
    atomically swaps to the new session on success; on failure it keeps
    serving with the previous session and the call raises so the rotation
    workflow can surface the failure to the user.
    """
    resp = _send(
        {"op": "rotate-bootstrap"},
        ssh_host=ssh_host,
        ssh_user=ssh_user,
        ssh_options=ssh_options,
    )
    if not resp.get("ok"):
        code = resp.get("error") or "unknown_error"
        message = resp.get("message") or "(no message)"
        raise BrokerAdminError(
            f"broker rotate-bootstrap returned {code}: {message}"
        )
