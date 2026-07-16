"""Shared 3x3 disposable-SSH-target fixture machinery (T043 / T061).

Extracted from ``test_nine_terminals.py`` (the original single-shot
cross-routing test, T043/SC-003) so the resource/soak test
(``test_nine_terminals_soak.py``, T061/NFR-004/SC-013) can reuse the exact
same disposable-container + real-server plumbing instead of duplicating it.
Nothing in this module is test-specific: it is pure setup/teardown machinery
(Docker container lifecycle, ssh-agent identity, a real loopback
``uvicorn.Server`` run as an ``asyncio.Task``, and a minimal stdlib-only async
HTTP POST helper). See ``test_nine_terminals.py``'s module docstring for the
full rationale (pure-asyncio driving, real TCP sockets, the short flat
``tempfile.mkdtemp()``-based ssh ControlPath dir, etc.) -- that reasoning
applies unchanged here and is not repeated.

The higher-level orchestration (provisioning the three containers + building
the real ``DiscoveryService``-backed app + the nine ``(target, banner)``
probes) is intentionally NOT extracted here: it is duplicated, pragmatically,
in each of the two test modules that need it, to avoid risking the
already-passing T043 test's control flow in a broader refactor.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest
import uvicorn
import websockets

__all__ = [
    "INSTANCE_TAGS",
    "PROJECTS_BY_INSTANCE",
    "_DOCKERFILE",
    "_DOCKER_OK",
    "_SKIP_REASON",
    "_RunningApp",
    "_container_running",
    "_docker_available",
    "_free_port",
    "_http_post_json",
    "_install_remo_host_script",
    "_open_probe_and_close",
    "_remo_host_script",
    "_start_container",
    "_stop_container",
    "_wait_for_ssh",
    "sshd_image",
    "ssh_test_identity",
    "trusted_known_hosts",
]

# ---------------------------------------------------------------------------
# Docker availability (checked once at import time).
# ---------------------------------------------------------------------------

_SKIP_REASON = "requires Docker with network access to build disposable SSH targets"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=8, check=True)
    except Exception:
        return False
    try:
        subprocess.run(
            ["docker", "pull", "alpine:3.20"], capture_output=True, timeout=45, check=True
        )
    except Exception:
        return False
    return True


_DOCKER_OK = _docker_available()


# ---------------------------------------------------------------------------
# Three instances x three projects, ONE project name ("shared") repeated
# identically across all three instances -- the specific SC-003 case.
# ---------------------------------------------------------------------------

INSTANCE_TAGS = ["inst-alpha", "inst-bravo", "inst-charlie"]
PROJECTS_BY_INSTANCE: dict[str, list[str]] = {
    "inst-alpha": ["alpha-one", "alpha-two", "shared"],
    "inst-bravo": ["bravo-one", "bravo-two", "shared"],
    "inst-charlie": ["charlie-one", "charlie-two", "shared"],
}


def _remo_host_script(instance_tag: str, projects: list[str]) -> str:
    """A fake `remo-host` for one container: reports *projects*, and on
    `sessions attach --project X` prints a banner naming BOTH this
    container's baked-in *instance_tag* and the actually-invoked project
    (`$4`, from the real argv the web service sent over ssh) before
    `exec cat`-echoing stdin. The banner proves which (instance, project)
    pair actually ran; the echo proves per-terminal stdin isolation.
    """
    projects_json = ",".join(
        '{"name":"%s","has_devcontainer":false,"zellij_state":"absent",'
        '"devcontainer_running":"unknown"}' % p
        for p in projects
    )
    return f"""#!/bin/sh
if [ "$1" = "capabilities" ]; then
  echo '{{"protocol_version":1,"host_tools_version":"9.9.9-test","projects_root":"/home/remo/projects","operations":["capabilities","sessions.list","sessions.attach"],"zellij":false,"docker":false}}'
  exit 0
elif [ "$1" = "sessions" ] && [ "$2" = "list" ]; then
  echo '{{"protocol_version":1,"projects":[{projects_json}]}}'
  exit 0
elif [ "$1" = "sessions" ] && [ "$2" = "attach" ]; then
  echo "BANNER::{instance_tag}::$4"
  exec cat
fi
echo "unsupported verb: $*" >&2
exit 4
"""


_DOCKERFILE = """
FROM alpine:3.20
RUN apk add --no-cache openssh bash coreutils \\
    && ssh-keygen -A \\
    && adduser -D -s /bin/bash remo \\
    && echo "remo:remo-test-account-unlock" | chpasswd \\
    && mkdir -p /home/remo/.ssh \\
    && chmod 700 /home/remo/.ssh
COPY authorized_keys /home/remo/.ssh/authorized_keys
RUN chmod 600 /home/remo/.ssh/authorized_keys \\
    && chown -R remo:remo /home/remo/.ssh \\
    && { \\
         echo 'PasswordAuthentication no'; \\
         echo 'KbdInteractiveAuthentication no'; \\
         echo 'PubkeyAuthentication yes'; \\
         echo 'UsePAM no'; \\
       } >> /etc/ssh/sshd_config
EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
"""


# ---------------------------------------------------------------------------
# Fixtures (mirrors test_remo_host_e2e.py / test_terminal_attach.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ssh_test_identity():
    tmp_dir = Path(tempfile.mkdtemp(prefix="remo-test-nine-identity-"))
    key_path = tmp_dir / "id_test"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path), "-q"],
        check=True,
        timeout=20,
    )
    pubkey_text = (key_path.with_suffix(".pub")).read_text()

    agent_out = subprocess.run(
        ["ssh-agent", "-s"], capture_output=True, text=True, check=True, timeout=10
    ).stdout
    auth_sock = re.search(r"SSH_AUTH_SOCK=([^;]+);", agent_out).group(1)  # type: ignore[union-attr]
    agent_pid = re.search(r"SSH_AGENT_PID=(\d+);", agent_out).group(1)  # type: ignore[union-attr]
    agent_env = dict(os.environ, SSH_AUTH_SOCK=auth_sock, SSH_AGENT_PID=agent_pid)
    subprocess.run(
        ["ssh-add", str(key_path)], env=agent_env, check=True, capture_output=True, timeout=10
    )
    try:
        yield pubkey_text, auth_sock, agent_pid
    finally:
        subprocess.run(["ssh-agent", "-k"], env=agent_env, capture_output=True, timeout=10)
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def trusted_known_hosts():
    kh_path = Path.home() / ".ssh" / "known_hosts"
    kh_path.parent.mkdir(mode=0o700, exist_ok=True)
    original = kh_path.read_bytes() if kh_path.exists() else None

    def _trust(ip: str) -> None:
        result = subprocess.run(
            ["ssh-keyscan", "-T", "5", "-p", "22", ip],
            capture_output=True,
            text=True,
            timeout=10,
        )
        with kh_path.open("a") as f:
            f.write(result.stdout)

    try:
        yield _trust
    finally:
        if original is None:
            kh_path.unlink(missing_ok=True)
        else:
            kh_path.write_bytes(original)


@pytest.fixture(scope="module")
def sshd_image(ssh_test_identity):
    if not _DOCKER_OK:
        pytest.skip(_SKIP_REASON)
    pubkey_text, _auth_sock, _agent_pid = ssh_test_identity
    build_dir = Path(tempfile.mkdtemp(prefix="remo-test-nine-sshd-"))
    try:
        (build_dir / "authorized_keys").write_text(pubkey_text)
        (build_dir / "Dockerfile").write_text(_DOCKERFILE)
        tag = f"remo-test-nine-sshd:{uuid.uuid4().hex[:10]}"
        subprocess.run(
            ["docker", "build", "-q", "-t", tag, str(build_dir)],
            check=True,
            capture_output=True,
            timeout=180,
        )
        yield tag
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, timeout=30)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Container helpers
# ---------------------------------------------------------------------------


def _wait_for_ssh(ip: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((ip, 22), timeout=1.0):
                return
        except OSError as exc:
            last_exc = exc
            time.sleep(0.3)
    raise RuntimeError(f"sshd on {ip}:22 never became reachable: {last_exc}")


def _start_container(image_tag: str, name: str) -> str:
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, image_tag],
        check=True,
        capture_output=True,
        timeout=20,
    )
    ip = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            name,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    _wait_for_ssh(ip)
    return ip


def _stop_container(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15)


def _container_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() == "true"


def _install_remo_host_script(container_name: str, script_text: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix="-remo-host", delete=False) as f:
        f.write(script_text)
        local_path = f.name
    try:
        subprocess.run(
            ["docker", "cp", local_path, f"{container_name}:/usr/local/bin/remo-host"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["docker", "exec", container_name, "chmod", "755", "/usr/local/bin/remo-host"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            [
                "docker",
                "exec",
                container_name,
                "cp",
                "/usr/local/bin/remo-host",
                "/usr/bin/remo-host",
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
    finally:
        Path(local_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# A real loopback server for the app (see test_nine_terminals.py's module
# docstring for why this replaced an in-process `TestClient`-over-threads
# approach).
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _RunningApp:
    """A real `uvicorn.Server` for *app*, run as an `asyncio.Task`.

    `start()`/`stop()` bracket the server's lifetime; `origin` is the real
    ``http://127.0.0.1:<port>`` base URL once started.
    """

    def __init__(self, app, port: int) -> None:  # noqa: ANN001
        self.port = port
        self.origin = f"http://127.0.0.1:{self.port}"
        self._config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on"
        )
        self._server = uvicorn.Server(self._config)
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._server.serve())
        deadline = time.monotonic() + 15.0
        while not self._server.started and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert self._server.started, "uvicorn server never reported started"

    async def stop(self) -> None:
        if self._task is None:
            return
        self._server.should_exit = True
        await asyncio.wait_for(self._task, timeout=10.0)


# ---------------------------------------------------------------------------
# Minimal async HTTP/1.1 POST (stdlib-only -- see test_nine_terminals.py's
# module docstring for why this isn't `httpx.AsyncClient`).
# ---------------------------------------------------------------------------


async def _http_post_json(
    port: int, path: str, payload: dict, *, origin: str, timeout: float = 15.0
) -> tuple[int, dict]:
    """POST a small JSON *payload* to ``127.0.0.1:<port><path>``.

    Returns ``(status_code, json_body)``. Sends ``Connection: close`` so the
    server closes the socket after responding -- reading until EOF is then
    sufficient, no ``Content-Length``/chunked-transfer parsing required.
    """
    body = json.dumps(payload).encode()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", port), timeout=timeout
    )
    try:
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Origin: {origin}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode() + body
        writer.write(request)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), timeout=timeout)
    finally:
        writer.close()
    header_blob, _, resp_body = raw.partition(b"\r\n\r\n")
    status_line = header_blob.split(b"\r\n", 1)[0]
    status_code = int(status_line.split(b" ")[1])
    return status_code, (json.loads(resp_body) if resp_body.strip() else {})


# ---------------------------------------------------------------------------
# Per-terminal probe: open one WS, verify its banner, round-trip its marker.
# ---------------------------------------------------------------------------


async def _open_probe_and_close(
    port: int,
    origin: str,
    target_id: str,
    expected_banner: str,
    marker: str,
) -> tuple[str, bytes]:
    """Create + WS-attach one terminal over a REAL socket, verify banner+marker, close.

    Returns ``(terminal_id, seen_bytes)`` -- *seen_bytes* is every byte seen
    on this terminal's socket, for the caller's cross-terminal "nobody else's
    data leaked in here" assertions; *terminal_id* lets the caller confirm a
    clean reap afterwards. Pure `asyncio` (real TCP client, no threads) so
    nine of these run concurrently as plain `asyncio.gather`-ed coroutines.
    """
    status_code, body = await _http_post_json(
        port,
        "/api/v1/terminals",
        {"session_target_id": target_id, "cols": 80, "rows": 24},
        origin=origin,
    )
    assert status_code == 201, body
    terminal_id = body["terminal_id"]
    token = body["ws_token"]

    ws_uri = origin.replace("http://", "ws://", 1) + f"/api/v1/terminals/{terminal_id}"
    seen = b""
    control_frames: list[str] = []
    try:
        async with websockets.connect(
            ws_uri,
            subprotocols=["remo-terminal.v1", token],
            additional_headers={"Origin": origin},
            open_timeout=15,
        ) as ws:
            assert ws.subprotocol == "remo-terminal.v1"
            first = await asyncio.wait_for(ws.recv(), timeout=15.0)
            assert json.loads(first) == {"v": 1, "type": "ready"}

            # Wait for THIS terminal's own attach banner. Control (text)
            # frames -- e.g. a server-side `error`/`exit` frame -- are
            # captured for diagnosis rather than silently discarded.
            banner_bytes = expected_banner.encode()
            deadline = time.monotonic() + 15.0
            while banner_bytes not in seen and time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                if isinstance(msg, bytes):
                    seen += msg
                else:
                    control_frames.append(msg)
            assert banner_bytes in seen, (
                f"terminal {terminal_id} never saw its own banner {expected_banner!r}; "
                f"saw bytes={seen!r} control_frames={control_frames!r}"
            )

            # Send this terminal's unique marker; wait for its own echo.
            marker_bytes = marker.encode()
            await ws.send(marker_bytes)
            deadline = time.monotonic() + 15.0
            while marker_bytes.strip() not in seen and time.monotonic() < deadline:
                remaining = max(0.1, deadline - time.monotonic())
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                if isinstance(msg, bytes):
                    seen += msg
                else:
                    control_frames.append(msg)
            assert marker_bytes.strip() in seen, (
                f"terminal {terminal_id} never echoed its own marker {marker!r}; "
                f"saw bytes={seen!r} control_frames={control_frames!r}"
            )
    except websockets.exceptions.ConnectionClosed as exc:
        raise AssertionError(
            f"terminal {terminal_id} ({expected_banner!r}) WS closed unexpectedly: {exc!r}; "
            f"saw bytes={seen!r} control_frames={control_frames!r}"
        ) from exc

    return terminal_id, seen
