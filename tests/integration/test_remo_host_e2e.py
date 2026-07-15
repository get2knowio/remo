"""Integration test for `remo-host` + `DiscoveryService` over REAL SSH (T024).

Exercises the actual `subprocess.run(["ssh", ...])` transport (no mocking)
against disposable SSH targets, proving `remo_host_client`/`DiscoveryService`
correctly parse live SSH+JSON output and classify failures end-to-end
(FR-006): healthy, unreachable, malformed-JSON, incompatible-protocol, and
slow (timeout) hosts each yield the correct typed `DiscoverySnapshot` status.

Test tiers, by infrastructure dependency
-----------------------------------------
1. Docker-free, always run: "unreachable" (a real `ssh` attempt against a
   closed local port -- no server needed at all) and a real-subprocess
   timeout-enforcement check (proves `discovery_timeout_s` is honored over
   an actual OS subprocess, not mocked asyncio, without requiring SSH/Docker
   for the "slow" concept itself).
2. Docker-gated (each test individually marked, not a whole-module skip, so
   the docker-free tier above still runs in a Docker-less CI environment):
   healthy, malformed-JSON, incompatible-protocol, no-remo-host, and a full
   SSH-based "slow" scenario -- each built on a disposable Alpine+OpenSSH
   container with a fake `remo-host` script injected per scenario, matching
   the real wire contract (contracts/remo-host-protocol.md) closely enough
   to prove the client's SSH+JSON parsing, not a full Ansible-provisioned
   host.

Trusting the disposable host (why this is more involved than it looks)
------------------------------------------------------------------------
`core/ssh.py`'s direct-SSH branch (the one exercised here; SSM is a separate
`ProxyCommand` branch) adds no `StrictHostKeyChecking`/identity options of
its own -- by design, so a compromised/incomplete config can't silently
downgrade host-key checking for real users. That means a disposable
container's brand-new host key and this test's generated keypair must be
trusted through the *real* channels OpenSSH actually consults:

- OpenSSH's `ssh` resolves `~` (default `~/.ssh/config`, `~/.ssh/known_hosts`,
  default identity files) via `getpwuid()`'s password-database home
  directory, NOT the `$HOME` environment variable -- so monkeypatching
  `$HOME` has no effect on it (verified empirically while building this
  fixture). The two supported hooks that a plain `subprocess.run(["ssh",
  ...])` *does* honor via the environment are `SSH_AUTH_SOCK` (ssh-agent)
  and whatever is already in the real `~/.ssh/known_hosts`.
- So: identity is supplied via a throwaway `ssh-agent` (`SSH_AUTH_SOCK`
  monkeypatched per test), and host-key trust is supplied by temporarily
  appending an `ssh-keyscan` result for the container's IP to the *real*
  `~/.ssh/known_hosts`, restoring its exact original bytes in a `finally`
  block. This is scoped, additive-only-until-cleanup, and only runs at all
  when Docker (and therefore this whole disposable-container flow) is
  available.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from remo_cli.core.remo_host_client import get_capabilities, list_sessions
from remo_cli.core.ssh import build_ssh_base_cmd
from remo_cli.models.discovery import DiscoverySnapshot, InstanceStatus
from remo_cli.models.host import KnownHost
from remo_cli.web.config import WebSettings
from remo_cli.web.discovery import DiscoveryService

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
requires_docker = pytest.mark.skipif(not _DOCKER_OK, reason=_SKIP_REASON)


# ---------------------------------------------------------------------------
# Fake `remo-host` scripts (subset of contracts/remo-host-protocol.md: only
# the JSON verbs exercised by discovery -- attach is out of scope here).
# ---------------------------------------------------------------------------

_HEALTHY_SCRIPT = """#!/bin/sh
if [ "$1" = "capabilities" ]; then
  echo '{"protocol_version":1,"host_tools_version":"9.9.9-test","projects_root":"/home/remo/projects","operations":["capabilities","sessions.list","sessions.attach"],"zellij":false,"docker":false}'
  exit 0
elif [ "$1" = "sessions" ] && [ "$2" = "list" ]; then
  echo '{"protocol_version":1,"projects":[{"name":"demo-project","has_devcontainer":false,"zellij_state":"absent","devcontainer_running":"unknown"}]}'
  exit 0
fi
echo "unsupported verb: $*" >&2
exit 4
"""

_MALFORMED_SCRIPT = """#!/bin/sh
echo 'this is not json {{{'
exit 0
"""

_INCOMPATIBLE_SCRIPT = """#!/bin/sh
echo '{"protocol_version":99,"host_tools_version":"99.0.0-future","projects_root":"/home/remo/projects"}'
exit 0
"""

_SLOW_SCRIPT = """#!/bin/sh
sleep 8
echo '{"protocol_version":1,"host_tools_version":"1.0.0","projects_root":"/home/remo/projects"}'
exit 0
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
# Note: the account needs a *set* (non-locked) password even though
# PasswordAuthentication is disabled server-side -- OpenSSH's own
# account-lock check (independent of PAM, tripped by `adduser -D`'s default
# locked/empty password field) rejects ALL auth methods, including
# pubkey, for a locked account ("User remo not allowed because account is
# locked"). Setting any password unlocks the account; PasswordAuthentication
# no still means that password can never actually be used to log in.


# ---------------------------------------------------------------------------
# Fixtures: disposable ssh-agent identity + host-key trust + sshd image
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ssh_test_identity():
    """A throwaway keypair loaded into a throwaway `ssh-agent`.

    Yields `(pubkey_text, auth_sock, agent_pid)`. `SSH_AUTH_SOCK` is the one
    identity-related hook a plain `ssh` invocation honors via the
    environment regardless of the passwd-db home-directory quirk described
    in the module docstring, so tests apply `auth_sock`/`agent_pid` via
    `monkeypatch.setenv` rather than writing any identity file into a real
    home directory.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="remo-test-ssh-identity-"))
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
    """Temporarily-extendable REAL `~/.ssh/known_hosts`, restored exactly after.

    See the module docstring: OpenSSH resolves `~/.ssh/known_hosts` via the
    passwd-database home directory, not `$HOME`, so per-test host-key trust
    has to go through the real file. Original bytes (or absence) are
    captured and restored in a `finally` block regardless of test outcome.
    """
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
    build_dir = Path(tempfile.mkdtemp(prefix="remo-test-sshd-"))
    try:
        (build_dir / "authorized_keys").write_text(pubkey_text)
        (build_dir / "Dockerfile").write_text(_DOCKERFILE)
        tag = f"remo-test-sshd:{uuid.uuid4().hex[:10]}"
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
# Container lifecycle helpers
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
    name = f"remo-test-{uuid.uuid4().hex[:10]}"
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


def _install_remo_host_script(container_name: str, script_text: str) -> None:
    """Copy *script_text* into the container as an executable `remo-host`.

    Installed ONLY at the `remo` user's `~/.local/bin/remo-host` — the exact
    location the `user_setup` Ansible role uses in production, and NOT on the
    default PATH of a non-interactive `ssh <host> <command>` shell. This makes
    the test faithful: it passes only because the client prefixes the remote
    command with `PATH="$HOME/.local/bin:$PATH"` (regression guard for the
    "remo-host: command not found" bug that a `/usr/local/bin` install hid).
    """
    with tempfile.NamedTemporaryFile("w", suffix="-remo-host", delete=False) as f:
        f.write(script_text)
        local_path = f.name
    dest = "/home/remo/.local/bin/remo-host"
    try:
        subprocess.run(
            ["docker", "exec", container_name, "mkdir", "-p", "/home/remo/.local/bin"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["docker", "cp", local_path, f"{container_name}:{dest}"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["docker", "exec", container_name, "chmod", "755", dest],
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["docker", "exec", container_name, "chown", "-R", "remo:remo", "/home/remo/.local"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    finally:
        Path(local_path).unlink(missing_ok=True)


def _connect_host(name: str, ip: str) -> KnownHost:
    return KnownHost(type="incus", name=name, host=ip, user="remo")


async def _discover_via_service(
    monkeypatch, tmp_path: Path, host: KnownHost, *, discovery_timeout_s: float = 10.0
) -> DiscoverySnapshot:
    """Register *host* in a real (temp) registry and run `DiscoveryService.refresh()`."""
    registry_dir = tmp_path / f"registry-{uuid.uuid4().hex[:8]}"
    registry_dir.mkdir()
    (registry_dir / "known_hosts").write_text(
        f"{host.type}:{host.name}:{host.host}:{host.user}\n"
    )
    monkeypatch.setenv("REMO_HOME", str(registry_dir))

    service = DiscoveryService(
        WebSettings(discovery_timeout_s=discovery_timeout_s, discovery_concurrency=4)
    )
    await service.refresh()
    snapshots = service.get_snapshot()
    assert len(snapshots) == 1, f"expected exactly one snapshot, got {snapshots!r}"
    return snapshots[0]


# ---------------------------------------------------------------------------
# Tier 1: Docker-free, always run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unreachable_host_yields_unreachable_snapshot(monkeypatch, tmp_path):
    """A real `ssh` attempt against an unreachable host -- no server, no Docker.

    Targets an RFC 2606 `.invalid` name, which is reserved precisely so it can
    never resolve. This deliberately does NOT probe a "closed" port on
    127.0.0.1: `KnownHost` carries no port and `build_ssh_base_cmd` always
    uses 22, so that only reads as unreachable where nothing happens to be
    listening on the loopback ssh port. On any host running sshd -- including
    GitHub's runners -- the connection is answered and rejected instead,
    yielding AUTH_FAILED and failing this test. An unresolvable name keeps the
    failure environment-independent, and still exercises the path under test:
    SshTransportError -> `_classify_ssh_transport` -> a retryable, target-less
    snapshot.
    """
    host = KnownHost(type="incus", name="unreachable", host="unreachable.invalid", user="nobody")
    snapshot = await _discover_via_service(monkeypatch, tmp_path, host, discovery_timeout_s=5.0)

    assert snapshot.status in (InstanceStatus.UNREACHABLE, InstanceStatus.TIMEOUT)
    assert snapshot.error is not None
    assert snapshot.error.retryable is True
    assert snapshot.targets == []


def test_timeout_enforced_over_real_subprocess():
    """Proves `timeout=` is honored by a REAL (non-mocked) subprocess call.

    Stands in for a "slow ssh target" using a plain slow local command in
    place of `ssh` itself: what's under test here is that
    `remo_host_client`'s `timeout` parameter actually bounds a real OS
    subprocess (via `subprocess.run(..., timeout=...)`), independent of
    whether the far end is SSH or Docker. The full SSH-based slow scenario
    is additionally covered by `test_slow_host_yields_timeout_snapshot`
    below when Docker is available.
    """
    from remo_cli.core.remo_host_client import SshTransportError

    slow_argv_prefix = [sys.executable, "-c", "import time; time.sleep(5)"]
    start = time.monotonic()
    with pytest.raises(SshTransportError, match="timed out"):
        get_capabilities(slow_argv_prefix, timeout=0.5)
    elapsed = time.monotonic() - start

    assert elapsed < 4.0, f"expected the 0.5s timeout to bound elapsed time, got {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Tier 2: Docker-gated
# ---------------------------------------------------------------------------


@requires_docker
def test_healthy_host_remo_host_client_parses_live_ssh_json(
    sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch
):
    """`remo_host_client` against a real SSH+JSON `remo-host` (not DiscoveryService)."""
    _pubkey, auth_sock, agent_pid = ssh_test_identity
    monkeypatch.setenv("SSH_AUTH_SOCK", auth_sock)
    monkeypatch.setenv("SSH_AGENT_PID", agent_pid)
    name, ip = _start_container(sshd_image)
    try:
        trusted_known_hosts(ip)
        _install_remo_host_script(name, _HEALTHY_SCRIPT)
        ssh_argv_prefix = build_ssh_base_cmd(_connect_host(name, ip))

        capability = get_capabilities(ssh_argv_prefix, timeout=10.0)
        assert capability.protocol_version == 1
        assert capability.host_tools_version == "9.9.9-test"

        entries = list_sessions(ssh_argv_prefix, timeout=10.0)
        assert len(entries) == 1
        assert entries[0].name == "demo-project"
    finally:
        _stop_container(name)


@requires_docker
@pytest.mark.asyncio
async def test_healthy_host_yields_ok_snapshot(
    sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch, tmp_path
):
    _pubkey, auth_sock, agent_pid = ssh_test_identity
    monkeypatch.setenv("SSH_AUTH_SOCK", auth_sock)
    monkeypatch.setenv("SSH_AGENT_PID", agent_pid)
    name, ip = _start_container(sshd_image)
    try:
        trusted_known_hosts(ip)
        _install_remo_host_script(name, _HEALTHY_SCRIPT)
        snapshot = await _discover_via_service(monkeypatch, tmp_path, _connect_host(name, ip))

        assert snapshot.status is InstanceStatus.OK
        assert snapshot.error is None
        assert snapshot.capability is not None
        assert snapshot.capability.protocol_version == 1
        assert len(snapshot.targets) == 1
        assert snapshot.targets[0].project == "demo-project"
    finally:
        _stop_container(name)


@requires_docker
@pytest.mark.asyncio
async def test_malformed_host_yields_malformed_snapshot(
    sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch, tmp_path
):
    _pubkey, auth_sock, agent_pid = ssh_test_identity
    monkeypatch.setenv("SSH_AUTH_SOCK", auth_sock)
    monkeypatch.setenv("SSH_AGENT_PID", agent_pid)
    name, ip = _start_container(sshd_image)
    try:
        trusted_known_hosts(ip)
        _install_remo_host_script(name, _MALFORMED_SCRIPT)
        snapshot = await _discover_via_service(monkeypatch, tmp_path, _connect_host(name, ip))

        assert snapshot.status is InstanceStatus.MALFORMED
        assert snapshot.error is not None
        assert snapshot.error.code == "malformed"
        assert snapshot.targets == []
    finally:
        _stop_container(name)


@requires_docker
@pytest.mark.asyncio
async def test_incompatible_protocol_host_yields_incompatible_snapshot(
    sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch, tmp_path
):
    _pubkey, auth_sock, agent_pid = ssh_test_identity
    monkeypatch.setenv("SSH_AUTH_SOCK", auth_sock)
    monkeypatch.setenv("SSH_AGENT_PID", agent_pid)
    name, ip = _start_container(sshd_image)
    try:
        trusted_known_hosts(ip)
        _install_remo_host_script(name, _INCOMPATIBLE_SCRIPT)
        snapshot = await _discover_via_service(monkeypatch, tmp_path, _connect_host(name, ip))

        assert snapshot.status is InstanceStatus.INCOMPATIBLE_PROTOCOL
        assert snapshot.error is not None
        assert snapshot.error.code == "incompatible_protocol"
        assert snapshot.error.retryable is False
        assert "update" in snapshot.error.remediation.lower()
    finally:
        _stop_container(name)


@requires_docker
@pytest.mark.asyncio
async def test_slow_host_yields_timeout_snapshot(
    sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch, tmp_path
):
    """A real `remo-host` that sleeps past `discovery_timeout_s`, over real SSH."""
    _pubkey, auth_sock, agent_pid = ssh_test_identity
    monkeypatch.setenv("SSH_AUTH_SOCK", auth_sock)
    monkeypatch.setenv("SSH_AGENT_PID", agent_pid)
    name, ip = _start_container(sshd_image)
    try:
        trusted_known_hosts(ip)
        _install_remo_host_script(name, _SLOW_SCRIPT)

        start = time.monotonic()
        snapshot = await _discover_via_service(
            monkeypatch, tmp_path, _connect_host(name, ip), discovery_timeout_s=1.5
        )
        elapsed = time.monotonic() - start

        assert snapshot.status is InstanceStatus.TIMEOUT
        assert snapshot.error is not None
        assert snapshot.error.retryable is True
        # Bounded well under the script's 8s sleep -- proves discovery_timeout_s
        # was actually enforced over the real SSH subprocess.
        assert elapsed < 6.0, f"expected the 1.5s timeout to bound elapsed time, got {elapsed:.2f}s"
    finally:
        _stop_container(name)


@requires_docker
@pytest.mark.asyncio
async def test_no_remo_host_yields_no_remo_host_snapshot(
    sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch, tmp_path
):
    """No `remo-host` installed at all -- real "command not found" over SSH."""
    _pubkey, auth_sock, agent_pid = ssh_test_identity
    monkeypatch.setenv("SSH_AUTH_SOCK", auth_sock)
    monkeypatch.setenv("SSH_AGENT_PID", agent_pid)
    name, ip = _start_container(sshd_image)
    try:
        trusted_known_hosts(ip)
        # Deliberately skip _install_remo_host_script: remo-host is absent.
        snapshot = await _discover_via_service(monkeypatch, tmp_path, _connect_host(name, ip))

        assert snapshot.status is InstanceStatus.NO_REMO_HOST
        assert snapshot.error is not None
        assert snapshot.error.code == "no_remo_host"
        assert snapshot.error.retryable is False
    finally:
        _stop_container(name)
