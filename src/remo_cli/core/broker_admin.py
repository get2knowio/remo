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


def _send_via_incus(
    request: dict[str, Any],
    *,
    incus_host: str,
    incus_host_user: str,
    container: str,
) -> dict[str, Any]:
    """Run the admin-socket bridge inside an Incus *container*.

    Same NDJSON wire protocol as :func:`_send`, but the bridge script is
    executed via ``incus exec <container> -- sudo python3 -c ...`` instead
    of a direct SSH login to the instance. When *incus_host* is
    ``"localhost"`` the ``incus exec`` runs locally (no outer SSH).
    """
    payload = json.dumps(request, separators=(",", ":"))
    inner_cmd = (
        f"incus exec {shlex.quote(container)} -- "
        f"sudo python3 -c {shlex.quote(_BRIDGE_SCRIPT)} {shlex.quote(payload)}"
    )
    if incus_host == "localhost":
        cmd = ["bash", "-c", inner_cmd]
    else:
        cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            f"{incus_host_user}@{incus_host}",
            inner_cmd,
        ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or "(no stderr)"
        raise BrokerAdminError(
            f"admin-socket incus-exec bridge failed (rc={proc.returncode}): {stderr}"
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


def rotate_bootstrap_via_incus(
    *,
    incus_host: str,
    incus_host_user: str,
    container: str,
) -> None:
    """Tell the broker daemon inside an Incus *container* to re-read its token.

    Wraps :func:`rotate_bootstrap` semantics in an ``incus exec`` indirection
    so the admin-socket bridge runs inside the container. *incus_host* is
    the Incus host (``"localhost"`` or remote), *incus_host_user* is the
    host-side SSH user (ignored when *incus_host* is ``"localhost"``).
    """
    resp = _send_via_incus(
        {"op": "rotate-bootstrap"},
        incus_host=incus_host,
        incus_host_user=incus_host_user,
        container=container,
    )
    if not resp.get("ok"):
        code = resp.get("error") or "unknown_error"
        message = resp.get("message") or "(no message)"
        raise BrokerAdminError(
            f"broker rotate-bootstrap returned {code}: {message}"
        )


def _send_via_proxmox(
    request: dict[str, Any],
    *,
    proxmox_host: str,
    host_user: str,
    vmid: str,
) -> dict[str, Any]:
    """Run the admin-socket bridge inside a Proxmox LXC container by *vmid*.

    Mirror of :func:`_send_via_incus`. Proxmox hosts are always remote
    (no localhost flavour); the bridge always tunnels via
    ``ssh <host_user>@<proxmox_host> 'pct exec <vmid> -- sudo python3 …'``.
    """
    payload = json.dumps(request, separators=(",", ":"))
    inner_cmd = (
        f"pct exec {shlex.quote(str(vmid))} -- "
        f"sudo python3 -c {shlex.quote(_BRIDGE_SCRIPT)} {shlex.quote(payload)}"
    )
    ssh_target = f"{host_user}@{proxmox_host}" if host_user else proxmox_host
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        ssh_target,
        inner_cmd,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or "(no stderr)"
        raise BrokerAdminError(
            f"admin-socket pct-exec bridge failed (rc={proc.returncode}): {stderr}"
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


def rotate_bootstrap_via_proxmox(
    *,
    proxmox_host: str,
    host_user: str,
    vmid: str,
) -> None:
    """Tell the broker daemon inside a Proxmox LXC *vmid* to re-read its token."""
    resp = _send_via_proxmox(
        {"op": "rotate-bootstrap"},
        proxmox_host=proxmox_host,
        host_user=host_user,
        vmid=vmid,
    )
    if not resp.get("ok"):
        code = resp.get("error") or "unknown_error"
        message = resp.get("message") or "(no message)"
        raise BrokerAdminError(
            f"broker rotate-bootstrap returned {code}: {message}"
        )
