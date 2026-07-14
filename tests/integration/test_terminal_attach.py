"""Integration test: PTY <-> SSH <-> WebSocket terminal attach over REAL ssh (T033).

Drives the full `POST /terminals` -> WS upgrade -> byte round-trip -> resize ->
disconnect flow against a disposable SSH target, proving the T036/T038 broker
wires a browser WebSocket to a real `ssh -tt <target> "remo-host sessions
attach ..."` PTY (FR-018/FR-019).

Reuses the disposable-SSH-target technique from `test_remo_host_e2e.py`
(ssh-agent identity via `SSH_AUTH_SOCK`, scoped `~/.ssh/known_hosts` trust with
a `finally`-restore, an Alpine+OpenSSH container with a fake `remo-host`). The
fake `remo-host sessions attach --project X` prints a banner then `exec cat`, a
minimal interactive stand-in for the real `project-launch`/Zellij entry — enough
to prove the PTY<->SSH<->WS pipe end to end.

Docker-gated: if Docker/network is unavailable the whole module skips (matching
`test_remo_host_e2e.py`'s precedent), but the real path is attempted first.
"""

from __future__ import annotations

import asyncio
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
from fastapi.testclient import TestClient

from remo_cli.web import app as app_module
from remo_cli.web.config import WebSettings
from remo_cli.web.models import TerminalState

# ---------------------------------------------------------------------------
# Docker availability (checked once at collection time)
# ---------------------------------------------------------------------------

_SKIP_REASON = "requires Docker with network access to build a disposable SSH target"


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
pytestmark = pytest.mark.skipif(not _DOCKER_OK, reason=_SKIP_REASON)


# ---------------------------------------------------------------------------
# Fake `remo-host`: capabilities + sessions list (for discovery) + attach.
# The attach verb prints a banner then execs `cat` — a minimal interactive
# shell stand-in that echoes input, proving the PTY<->SSH<->WS pipe works.
# ---------------------------------------------------------------------------

_REMO_HOST_SCRIPT = """#!/bin/sh
if [ "$1" = "capabilities" ]; then
  echo '{"protocol_version":1,"host_tools_version":"9.9.9-test","projects_root":"/home/remo/projects","operations":["capabilities","sessions.list","sessions.attach"],"zellij":false,"docker":false}'
  exit 0
elif [ "$1" = "sessions" ] && [ "$2" = "list" ]; then
  echo '{"protocol_version":1,"projects":[{"name":"demo-project","has_devcontainer":false,"zellij_state":"absent","devcontainer_running":"unknown"}]}'
  exit 0
elif [ "$1" = "sessions" ] && [ "$2" = "attach" ]; then
  echo "ATTACHED:$4"
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
# Fixtures (mirrors tests/integration/test_remo_host_e2e.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ssh_test_identity():
    tmp_dir = Path(tempfile.mkdtemp(prefix="remo-test-attach-identity-"))
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
    build_dir = Path(tempfile.mkdtemp(prefix="remo-test-attach-sshd-"))
    try:
        (build_dir / "authorized_keys").write_text(pubkey_text)
        (build_dir / "Dockerfile").write_text(_DOCKERFILE)
        tag = f"remo-test-attach-sshd:{uuid.uuid4().hex[:10]}"
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


def _start_container(image_tag: str) -> tuple[str, str]:
    name = f"remo-test-attach-{uuid.uuid4().hex[:10]}"
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
    return name, ip


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


def _install_remo_host_script(container_name: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix="-remo-host", delete=False) as f:
        f.write(_REMO_HOST_SCRIPT)
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
            ["docker", "exec", container_name, "cp",
             "/usr/local/bin/remo-host", "/usr/bin/remo-host"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    finally:
        Path(local_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_ws_terminal_attach_roundtrip_over_real_ssh(
    sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch, tmp_path
):
    _pubkey, auth_sock, agent_pid = ssh_test_identity
    monkeypatch.setenv("SSH_AUTH_SOCK", auth_sock)
    monkeypatch.setenv("SSH_AGENT_PID", agent_pid)

    name, ip = _start_container(sshd_image)
    try:
        trusted_known_hosts(ip)
        _install_remo_host_script(name)

        # Real registry pointing at the container.
        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        (registry_dir / "known_hosts").write_text(f"incus:{name}:{ip}:remo\n")
        monkeypatch.setenv("REMO_HOME", str(registry_dir))

        control_dir = tmp_path / "ssh-control"
        control_dir.mkdir()

        origin = "http://testserver"
        settings = WebSettings(
            allowed_hosts=["testserver", "localhost", "127.0.0.1"],
            allowed_origins=[origin],
            ssh_control_dir=str(control_dir),
            discovery_timeout_s=15.0,
        )
        app = app_module.create_app(settings)

        # Populate the discovery cache against the real container (real ssh).
        asyncio.run(app.state.discovery_service.refresh())
        targets = app.state.discovery_service.get_targets()
        assert len(targets) == 1, f"expected one discovered target, got {targets!r}"
        target_id = targets[0].id
        assert targets[0].project == "demo-project"

        with TestClient(app, base_url=origin) as client:
            created = client.post(
                "/api/v1/terminals",
                json={"session_target_id": target_id, "cols": 100, "rows": 30},
                headers={"Origin": origin},
            )
            assert created.status_code == 201, created.text
            body = created.json()
            terminal_id = body["terminal_id"]
            token = body["ws_token"]

            with client.websocket_connect(
                f"/api/v1/terminals/{terminal_id}",
                subprotocols=["remo-terminal.v1", token],
                headers={"Origin": origin},  # fresh dict (websocket_connect mutates it)
            ) as ws:
                # Handshake: subprotocol accepted + ready control frame.
                assert ws.accepted_subprotocol == "remo-terminal.v1"
                assert ws.receive_json() == {"v": 1, "type": "ready"}

                # PTY bytes round-trip: send input, see the banner and/or echo.
                ws.send_bytes(b"ping-over-pty\n")
                seen = b""
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    msg = ws.receive()
                    if msg.get("bytes"):
                        seen += msg["bytes"]
                        if b"ATTACHED" in seen or b"ping-over-pty" in seen:
                            break
                assert b"ATTACHED" in seen or b"ping-over-pty" in seen, (
                    f"no PTY output round-tripped; saw {seen!r}"
                )

                # A resize control frame must not error the stream.
                ws.send_json({"v": 1, "type": "resize", "cols": 120, "rows": 40})
                ws.send_json({"v": 1, "type": "ping"})
                pong = None
                for _ in range(30):
                    m = ws.receive()
                    if m.get("text"):
                        import json

                        pong = json.loads(m["text"])
                        if pong.get("type") == "pong":
                            break
                assert pong is not None and pong.get("type") == "pong"

            # After disconnect: the LOCAL attachment is reaped (state ->
            # disconnected, no live session), while the container's own sshd
            # (PID 1) is untouched — only the local ssh client was killed.
            reg = app.state.terminal_registry
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                att = reg.get(terminal_id)
                if att is not None and att.state == TerminalState.DISCONNECTED:
                    break
                time.sleep(0.05)
            att = reg.get(terminal_id)
            assert att is not None and att.state == TerminalState.DISCONNECTED
            assert reg.get_session(terminal_id) is None

        assert _container_running(name), "container must survive local ssh teardown"
    finally:
        _stop_container(name)
