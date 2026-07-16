"""Performance/latency verification (T067): SC-010, SC-011, SC-012.

Measures, on the SAME 3-instance x 3-project disposable-container fixture
technique as `tests/integration/test_nine_terminals.py` (a real
`uvicorn.Server` run as an `asyncio.Task`, the `websockets` client library,
pure-asyncio -- no threads, no httpx; see that module's docstring for why):

* **SC-010** (spec.md, quickstart.md V1): discovery of nine projects across
  three instances completes and renders incrementally within 10 seconds.
* **SC-011** (spec.md, quickstart.md V3): for an already-running ("warm")
  remote session, first terminal output appears within 5 seconds.
* **SC-012** (spec.md): web-service-introduced keystroke-to-echo latency
  stays below 100ms at the 95th percentile, EXCLUDING real SSH/network/
  remote-workload latency per the SC's own wording.

Each test prints its measured value (`pytest -s` to see it) and also embeds
it in the assertion message, so a failure -- or a `-s` run -- always records
the actual number, not just pass/fail.

Docker-gated: if Docker/network is unavailable the whole module skips
(matching the other integration/perf fixtures' precedent).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
import uvicorn
import websockets

from remo_cli.web import app as app_module
from remo_cli.web.config import WebSettings

# ---------------------------------------------------------------------------
# Docker availability (checked once at collection time)
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
pytestmark = pytest.mark.skipif(not _DOCKER_OK, reason=_SKIP_REASON)


# ---------------------------------------------------------------------------
# Three instances x three projects -- identical shape to
# test_nine_terminals.py's fixture (nine discoverable targets for SC-010).
# ---------------------------------------------------------------------------

INSTANCE_TAGS = ["inst-alpha", "inst-bravo", "inst-charlie"]
PROJECTS_BY_INSTANCE: dict[str, list[str]] = {
    "inst-alpha": ["alpha-one", "alpha-two", "shared"],
    "inst-bravo": ["bravo-one", "bravo-two", "shared"],
    "inst-charlie": ["charlie-one", "charlie-two", "shared"],
}


def _remo_host_script(instance_tag: str, projects: list[str]) -> str:
    """A fake `remo-host`: reports *projects*, and on `sessions attach
    --project X` prints a banner (naming this container + the invoked
    project) before `exec cat`-echoing stdin -- a fast, deterministic
    stand-in for a real interactive shell, used both to prove the pipe works
    (test_nine_terminals.py) and, here, as the "already-running" (SC-011)
    near-instant echo loop (SC-012).
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
# Identity + image fixtures (module-scoped: shared by all three SC tests
# below so the (expensive-ish) container/image build cost is paid once, not
# per-SC -- only the per-test discovery/attach/echo work is on the clock).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ssh_test_identity():
    tmp_dir = Path(tempfile.mkdtemp(prefix="remo-perf-identity-"))
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


@pytest.fixture(scope="module")
def trusted_known_hosts_module():
    """Module-scoped: trust persists for every SC test in this file, restored
    once teardown runs (instead of re-keyscanning per test)."""
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
    build_dir = Path(tempfile.mkdtemp(prefix="remo-perf-sshd-"))
    try:
        (build_dir / "authorized_keys").write_text(pubkey_text)
        (build_dir / "Dockerfile").write_text(_DOCKERFILE)
        tag = f"remo-test-perf-sshd:{uuid.uuid4().hex[:10]}"
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
# Container helpers (identical to test_nine_terminals.py's).
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
# The shared cluster: three warm, already-running disposable SSH targets +
# a real registry naming them, built ONCE for the whole module. Individual
# SC tests below build their OWN `WebSettings`/app/server against this
# already-warm cluster, so container/ssh/agent/host-key setup cost is never
# counted against any SC's measured budget.
# ---------------------------------------------------------------------------


@dataclass
class _PerfCluster:
    ips: dict[str, str]
    control_dir: Path


@pytest.fixture(scope="module")
def perf_cluster(sshd_image, ssh_test_identity, trusted_known_hosts_module):
    _pubkey, auth_sock, agent_pid = ssh_test_identity
    saved_env = {
        k: os.environ.get(k) for k in ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "REMO_HOME")
    }
    os.environ["SSH_AUTH_SOCK"] = auth_sock
    os.environ["SSH_AGENT_PID"] = agent_pid

    container_names = [f"remo-test-perf-{tag}-{uuid.uuid4().hex[:8]}" for tag in INSTANCE_TAGS]
    ips: dict[str, str] = {}
    # A short, flat control dir -- NOT pytest's `tmp_path` (nested paths
    # overflow the AF_UNIX ControlPath socket path limit; see
    # test_nine_terminals.py's docstring for the real bug this avoids).
    control_dir = Path(tempfile.mkdtemp(prefix="remo-perf-ctl-"))
    registry_dir = Path(tempfile.mkdtemp(prefix="remo-perf-registry-"))
    try:
        for tag, name in zip(INSTANCE_TAGS, container_names):
            ip = _start_container(sshd_image, name)
            ips[tag] = ip
            trusted_known_hosts_module(ip)
            _install_remo_host_script(name, _remo_host_script(tag, PROJECTS_BY_INSTANCE[tag]))

        registry_lines = [f"incus:{tag}:{ips[tag]}:remo\n" for tag in INSTANCE_TAGS]
        (registry_dir / "known_hosts").write_text("".join(registry_lines))
        os.environ["REMO_HOME"] = str(registry_dir)

        yield _PerfCluster(ips=ips, control_dir=control_dir)
    finally:
        for name in container_names:
            _stop_container(name)
        shutil.rmtree(control_dir, ignore_errors=True)
        shutil.rmtree(registry_dir, ignore_errors=True)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# A real loopback server for one app instance (identical technique to
# test_nine_terminals.py's `_RunningApp`).
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _RunningApp:
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


async def _http_post_json(
    port: int, path: str, payload: dict, *, origin: str, timeout: float = 15.0
) -> tuple[int, dict]:
    """Minimal stdlib-only async HTTP/1.1 POST -- see test_nine_terminals.py's
    dependency note for why this isn't `httpx.AsyncClient` (not a declared
    project dependency)."""
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


def _perf_settings(cluster: _PerfCluster, *, port: int, origin: str) -> WebSettings:
    return WebSettings(
        allowed_hosts=["127.0.0.1"],
        allowed_origins=[origin],
        ssh_control_dir=str(cluster.control_dir),
        discovery_timeout_s=15.0,
        discovery_concurrency=8,
        terminal_cap_global=32,
        terminal_cap_per_client=16,
    )


# ---------------------------------------------------------------------------
# SC-010: discovery of nine projects (3 instances x 3 projects) renders
# incrementally, full results available <= 10s.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sc010_discovery_of_nine_projects_within_10s(perf_cluster):
    # Fresh DiscoveryService against the ALREADY-WARM cluster (sshd running,
    # host keys trusted, ssh-agent identity loaded -- all of that setup cost
    # was paid by the module-scoped `perf_cluster` fixture, not here). Only
    # `refresh()` itself is on the clock below -- a generous bound (SC-010's
    # 10s budget is for real home-LAN/tailnet SSH; three local Docker
    # containers over loopback should clear it with room to spare).
    settings = WebSettings(
        ssh_control_dir=str(perf_cluster.control_dir),
        discovery_timeout_s=15.0,
        discovery_concurrency=8,
    )
    app = app_module.create_app(settings)

    t0 = time.perf_counter()
    await app.state.discovery_service.refresh()
    elapsed = time.perf_counter() - t0

    targets = app.state.discovery_service.get_targets()
    print(f"SC-010 discovery: {elapsed:.2f}s (budget 10s) for {len(targets)} targets")
    assert len(targets) == 9, f"expected nine discovered targets, got {targets!r}"
    assert elapsed < 10.0, f"SC-010 discovery took {elapsed:.2f}s, exceeding the 10s budget"


# ---------------------------------------------------------------------------
# SC-011: first warm-session output < 5s.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sc011_first_warm_session_output_within_5s(perf_cluster):
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    settings = _perf_settings(perf_cluster, port=port, origin=origin)
    app = app_module.create_app(settings)

    # Untimed setup: populate discovery (not on the clock -- SC-011 measures
    # attach latency to an ALREADY-DISCOVERED, already-running session, not
    # discovery time; that's SC-010's job above).
    await app.state.discovery_service.refresh()
    targets = app.state.discovery_service.get_targets()
    target = next(t for t in targets if t.instance_name == "inst-alpha" and t.project == "alpha-one")

    running = _RunningApp(app, port)
    await running.start()
    try:
        status_code, body = await _http_post_json(
            port,
            "/api/v1/terminals",
            {"session_target_id": target.id, "cols": 80, "rows": 24},
            origin=origin,
        )
        assert status_code == 201, body
        terminal_id = body["terminal_id"]
        token = body["ws_token"]

        ws_uri = origin.replace("http://", "ws://", 1) + f"/api/v1/terminals/{terminal_id}"
        async with websockets.connect(
            ws_uri,
            subprotocols=["remo-terminal.v1", token],
            additional_headers={"Origin": origin},
            open_timeout=15,
        ) as ws:
            # The WS is accepted the instant `connect()` returns -- start the
            # clock here per SC-011's "within N seconds of opening it".
            t_accept = time.perf_counter()
            first_byte_elapsed: float | None = None
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                msg = await asyncio.wait_for(ws.recv(), timeout=15.0)
                if isinstance(msg, bytes):
                    # First PTY-output byte (the fake remote's attach
                    # banner) -- text control frames (e.g. "ready", sent
                    # before the PTY has produced any output) don't count.
                    first_byte_elapsed = time.perf_counter() - t_accept
                    break

        assert first_byte_elapsed is not None, "never received a PTY output byte"
        print(f"SC-011 first warm-session output: {first_byte_elapsed:.3f}s (budget 5s)")
        assert first_byte_elapsed < 5.0, (
            f"SC-011 first output took {first_byte_elapsed:.3f}s, exceeding the 5s budget"
        )
    finally:
        await running.stop()


# ---------------------------------------------------------------------------
# SC-012: web-introduced keystroke->echo latency < 100ms p95.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sc012_keystroke_echo_p95_under_100ms(perf_cluster):
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    settings = _perf_settings(perf_cluster, port=port, origin=origin)
    app = app_module.create_app(settings)

    await app.state.discovery_service.refresh()
    targets = app.state.discovery_service.get_targets()
    target = next(t for t in targets if t.instance_name == "inst-bravo" and t.project == "bravo-one")

    running = _RunningApp(app, port)
    await running.start()
    try:
        status_code, body = await _http_post_json(
            port,
            "/api/v1/terminals",
            {"session_target_id": target.id, "cols": 80, "rows": 24},
            origin=origin,
        )
        assert status_code == 201, body
        terminal_id = body["terminal_id"]
        token = body["ws_token"]

        ws_uri = origin.replace("http://", "ws://", 1) + f"/api/v1/terminals/{terminal_id}"
        async with websockets.connect(
            ws_uri,
            subprotocols=["remo-terminal.v1", token],
            additional_headers={"Origin": origin},
            open_timeout=15,
        ) as ws:
            # Wait for the attach banner first (session fully warm) before
            # starting the timed loop below -- otherwise the first sample
            # would include cold PTY-spawn time, which SC-012 explicitly
            # excludes ("excluding network/remote [workload] latency").
            banner = b"BANNER::inst-bravo::bravo-one"
            seen = b""
            deadline = time.monotonic() + 15.0
            while banner not in seen and time.monotonic() < deadline:
                msg = await asyncio.wait_for(ws.recv(), timeout=15.0)
                if isinstance(msg, bytes):
                    seen += msg
            assert banner in seen, f"never saw attach banner; saw {seen!r}"

            # N small input frames, each with a unique marker, sent
            # sequentially (send -> await its own echo -> next) against the
            # fake remote's `cat` echo loop (near-instant, so this is a
            # faithful measurement of the web service's OWN
            # WS-receive -> PTY-write -> PTY-read -> WS-send overhead).
            #
            # NOTE: this necessarily also includes the loopback-SSH hop to
            # the local Docker fixture (ssh -tt ... over 127.0.0.1) -- there
            # is no way to exercise the real WS<->PTY<->ssh pump code path
            # without going over an actual ssh connection. Loopback Docker
            # overhead is negligible next to the 100ms budget, so this
            # remains a faithful stand-in for "web-service-introduced"
            # latency; mocking out ssh entirely would stop testing the real
            # code path, which is worse.
            num_samples = 60
            latencies_ms: list[float] = []
            for i in range(num_samples):
                marker = f"K{i:04d}".encode()
                sample_seen = b""
                t0 = time.perf_counter()
                await ws.send(marker + b"\n")
                found = False
                sample_deadline = time.monotonic() + 2.0
                while time.monotonic() < sample_deadline:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    if isinstance(msg, bytes):
                        sample_seen += msg
                        if marker in sample_seen:
                            found = True
                            break
                assert found, f"marker {marker!r} was never echoed back"
                latencies_ms.append((time.perf_counter() - t0) * 1000.0)

        latencies_ms.sort()
        p95_index = max(0, math.ceil(0.95 * len(latencies_ms)) - 1)
        p95_ms = latencies_ms[p95_index]
        print(
            f"SC-012 keystroke->echo p95: {p95_ms:.1f}ms over {num_samples} samples "
            f"(budget 100ms); min={latencies_ms[0]:.1f}ms max={latencies_ms[-1]:.1f}ms"
        )
        assert p95_ms < 100.0, (
            f"SC-012 p95 keystroke->echo latency {p95_ms:.1f}ms exceeds the 100ms budget "
            f"(samples: {latencies_ms!r})"
        )
    finally:
        await running.stop()
