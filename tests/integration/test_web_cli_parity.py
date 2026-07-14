"""Integration test: web attach and `remo shell -p <project>` reach the SAME
Zellij session (T056, SC-002).

The web path and the CLI path both eventually invoke the remote
`~/.local/bin/project-launch --project NAME` helper -- the web path via
`remo-host sessions attach --project NAME` (which the real `remo-host.sh.j2`
execs straight into `project-launch`), the CLI path via
`core.ssh.shell_connect()`'s direct `build_project_launch_remote_cmd()` +
`build_ssh_base_cmd()` invocation. Both are, by construction, thin wrappers
around the same shared SSH-argv builder (`build_ssh_base_cmd`, T058) and the
same project-name validator (`validate_project_name`, T059).

This test proves the two paths are provably the same mechanism without
needing real Zellij: a disposable container's fake `project-launch` script
appends `(caller, project, timestamp)` to a *per-project-name* state file
(never per-caller) each time it is invoked. Real `project-launch --project X`
always resolves to the same Zellij session name `X` regardless of who calls
it, so "both invocations append to the exact same state file" is the proof of
"same session identity" (SC-002) -- exactly like two people running
`zellij attach X` land in the one session named `X`.

Two scopes, matching the task's honest split:

* `TestSsmArgvParity` -- pure unit-level, no Docker/network: proves
  `build_ssh_base_cmd()` produces the correct SSM `ProxyCommand`-bearing argv
  for an SSM `KnownHost`, and that both the CLI's
  `build_project_launch_remote_cmd()` and the web's `build_attach_argv()`
  build their SSH transport through that exact same function.
* `TestLiveDirectSshParity` -- Docker-gated, real subprocess/WS execution
  against a disposable Alpine+OpenSSH container (fixture technique reused
  from `test_terminal_attach.py`/`test_remo_host_e2e.py`).
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

from remo_cli.core.ssh import build_project_launch_remote_cmd, build_ssh_base_cmd
from remo_cli.models.host import KnownHost
from remo_cli.web import app as app_module
from remo_cli.web.config import WebSettings
from remo_cli.web.terminal import build_attach_argv

# ---------------------------------------------------------------------------
# Docker availability (checked once at collection time; same technique as
# test_terminal_attach.py / test_remo_host_e2e.py).
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
requires_docker = pytest.mark.skipif(not _DOCKER_OK, reason=_SKIP_REASON)


# ---------------------------------------------------------------------------
# Pure unit-level: SSM argv parity (no Docker, no network).
# ---------------------------------------------------------------------------


class TestSsmArgvParity:
    """Both the CLI and web paths build SSH transport via build_ssh_base_cmd.

    This proves argv-shape parity for SSM hosts without ever executing `aws
    ssm` or touching the network -- exactly what the task calls an "honest,
    achievable" split for the access mode real AWS credentials can't reach in
    a sandbox.
    """

    @pytest.fixture(autouse=True)
    def _suppress_tz(self, monkeypatch):
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr("remo_cli.core.ssh.detect_timezone", lambda: "")

    @pytest.fixture
    def ssm_host(self, mocker):
        mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")
        return KnownHost(
            type="aws",
            name="devbox",
            host="3.14.15.92",
            user="remo",
            instance_id="i-0abc123def",
            access_mode="ssm",
            region="us-west-2",
        )

    def test_cli_ssh_transport_carries_ssm_proxy_command(self, ssm_host):
        """The CLI path's transport (built the same way shell_connect() does)
        carries the SSM ProxyCommand and targets the instance id."""
        cmd = build_ssh_base_cmd(ssm_host, tty=True, multiplex=True, control_dir=None)

        assert cmd[0] == "ssh"
        assert cmd[-2] == "-tt"
        assert cmd[-1] == "remo@i-0abc123def"
        assert any("ProxyCommand=" in part and "aws ssm start-session" in part for part in cmd)

    def test_web_ssh_transport_carries_ssm_proxy_command(self, ssm_host, tmp_path):
        """build_attach_argv() (the web path) builds transport via the exact
        same build_ssh_base_cmd() call shape -- proving argv-construction
        parity for SSM hosts."""
        control_dir = str(tmp_path / "ssh-control")
        argv = build_attach_argv(ssm_host, "demo-project", control_dir=control_dir)

        assert argv[0] == "ssh"
        assert "-tt" in argv
        assert any("ProxyCommand=" in part and "aws ssm start-session" in part for part in argv)
        # Target (user@instance_id) immediately precedes the remote command.
        target_idx = argv.index("remo@i-0abc123def")
        assert argv[target_idx + 1] == "remo-host sessions attach --project demo-project"

    def test_cli_and_web_transports_agree_on_target_and_proxy_shape(self, ssm_host, tmp_path):
        """Direct structural comparison: strip the CLI's -tt/target/remote-cmd
        tail and the web's -o BatchMode=yes + -tt/target/remote-cmd tail; the
        remaining SSH option prefix (ProxyCommand, StrictHostKeyChecking,
        ControlMaster/...) must be identical -- both were built by the same
        build_ssh_opts() call inside build_ssh_base_cmd()."""
        control_dir = str(tmp_path / "ssh-control")

        cli_cmd = build_ssh_base_cmd(
            ssm_host, tty=True, multiplex=True, control_dir=control_dir
        )
        web_argv = build_attach_argv(ssm_host, "demo-project", control_dir=control_dir)

        # web_argv == [cli_cmd[0], "-o", "BatchMode=yes", *cli_cmd[1:-2], "-tt",
        #              target, remote_cmd]  (per build_attach_argv's own
        #              docstring: "base == ['ssh', *opts, '-tt', target]").
        cli_opts = cli_cmd[1:-2]  # strip "ssh" prefix and "-tt", target suffix
        web_opts = web_argv[3:-3]  # strip "ssh","-o","BatchMode=yes" and "-tt",target,cmd
        assert cli_opts == web_opts
        assert cli_cmd[-1] == web_argv[-2] == "remo@i-0abc123def"


# ---------------------------------------------------------------------------
# Live end-to-end parity over a disposable direct-SSH container.
# ---------------------------------------------------------------------------

# The fake `remo-host` mirrors remo-host.sh.j2's real behavior: `sessions
# attach --project NAME` execs straight into project-launch (no shell
# re-interpretation of NAME beyond argv passing).
_REMO_HOST_SCRIPT = """#!/bin/sh
if [ "$1" = "capabilities" ]; then
  echo '{"protocol_version":1,"host_tools_version":"9.9.9-test","projects_root":"/home/remo/projects","operations":["capabilities","sessions.list","sessions.attach"],"zellij":false,"docker":false}'
  exit 0
elif [ "$1" = "sessions" ] && [ "$2" = "list" ]; then
  echo '{"protocol_version":1,"projects":[{"name":"demo-project","has_devcontainer":false,"zellij_state":"absent","devcontainer_running":"unknown"}]}'
  exit 0
elif [ "$1" = "sessions" ] && [ "$2" = "attach" ]; then
  shift 2
  # $1 == --project, $2 == NAME
  exec "$HOME/.local/bin/project-launch" --project "$2"
fi
echo "unsupported verb: $*" >&2
exit 4
"""

# Fake `project-launch`: the ONE real entry point both the web path (via
# remo-host) and the CLI path (directly) converge on. Records each
# invocation to a state file keyed ONLY by project name (never by caller),
# then exits -- real project-launch would instead exec into (or attach) a
# long-lived Zellij session named after the project; the state file standing
# in for "which session got resolved to."
_PROJECT_LAUNCH_SCRIPT = """#!/bin/sh
project=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project) project="$2"; shift 2 ;;
    --detach) shift ;;
    --exec) shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p /tmp/project-launch-calls
echo "caller=$REMO_TEST_CALLER project=$project pid=$$" >> "/tmp/project-launch-calls/$project.log"
echo "LAUNCHED:$project"
exit 0
"""

_DOCKERFILE = """
FROM alpine:3.20
RUN apk add --no-cache openssh bash coreutils \\
    && ssh-keygen -A \\
    && adduser -D -s /bin/bash remo \\
    && echo "remo:remo-test-account-unlock" | chpasswd \\
    && mkdir -p /home/remo/.ssh /home/remo/.local/bin \\
    && chmod 700 /home/remo/.ssh
COPY authorized_keys /home/remo/.ssh/authorized_keys
RUN chmod 600 /home/remo/.ssh/authorized_keys \\
    && chown -R remo:remo /home/remo/.ssh /home/remo/.local \\
    && { \\
         echo 'PasswordAuthentication no'; \\
         echo 'KbdInteractiveAuthentication no'; \\
         echo 'PubkeyAuthentication yes'; \\
         echo 'UsePAM no'; \\
         echo 'AcceptEnv REMO_TEST_CALLER'; \\
       } >> /etc/ssh/sshd_config
EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
"""


@pytest.fixture(scope="module")
def ssh_test_identity():
    tmp_dir = Path(tempfile.mkdtemp(prefix="remo-test-parity-identity-"))
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
    build_dir = Path(tempfile.mkdtemp(prefix="remo-test-parity-sshd-"))
    try:
        (build_dir / "authorized_keys").write_text(pubkey_text)
        (build_dir / "Dockerfile").write_text(_DOCKERFILE)
        tag = f"remo-test-parity-sshd:{uuid.uuid4().hex[:10]}"
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
    name = f"remo-test-parity-{uuid.uuid4().hex[:10]}"
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


def _install_script(container_name: str, script_text: str, dest: str) -> None:
    with tempfile.NamedTemporaryFile("w", suffix="-script", delete=False) as f:
        f.write(script_text)
        local_path = f.name
    try:
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
            ["docker", "exec", "-u", "remo", container_name, "chown", "remo:remo", dest],
            capture_output=True,
            timeout=10,
        )
    finally:
        Path(local_path).unlink(missing_ok=True)


def _read_call_log(container_name: str, project: str) -> str:
    result = subprocess.run(
        ["docker", "exec", container_name, "cat", f"/tmp/project-launch-calls/{project}.log"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout


@requires_docker
class TestLiveDirectSshParity:
    """Real subprocess/WS execution against a disposable direct-SSH target."""

    def test_web_and_cli_paths_reach_the_same_project_launch_session(
        self, sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch
    ):
        _pubkey, auth_sock, agent_pid = ssh_test_identity
        monkeypatch.setenv("SSH_AUTH_SOCK", auth_sock)
        monkeypatch.setenv("SSH_AGENT_PID", agent_pid)

        # Short-named dirs directly under /tmp (NOT pytest's nested tmp_path):
        # the ControlPath is a Unix domain socket, capped at ~104 bytes, and
        # tmp_path's "pytest-of-<user>/pytest-N/<long-test-name>/..." nesting
        # overflows that limit.
        work_dir = Path(tempfile.mkdtemp(prefix="remo-parity-"))

        name, ip = _start_container(sshd_image)
        try:
            trusted_known_hosts(ip)
            _install_script(name, _REMO_HOST_SCRIPT, "/usr/local/bin/remo-host")
            subprocess.run(
                ["docker", "exec", name, "cp",
                 "/usr/local/bin/remo-host", "/usr/bin/remo-host"],
                check=True, capture_output=True, timeout=10,
            )
            _install_script(name, _PROJECT_LAUNCH_SCRIPT, "/home/remo/.local/bin/project-launch")

            project = "demo-project"
            host = KnownHost(type="incus", name=name, host=ip, user="remo")

            # ------------------------------------------------------------
            # (a) Drive the WEB path: POST /terminals + WS upgrade, attach
            # to `project` via remo-host sessions attach -> project-launch.
            # ------------------------------------------------------------
            registry_dir = work_dir / "registry"
            registry_dir.mkdir()
            (registry_dir / "known_hosts").write_text(f"incus:{name}:{ip}:remo\n")
            monkeypatch.setenv("REMO_HOME", str(registry_dir))

            control_dir = work_dir / "sc"
            control_dir.mkdir()

            origin = "http://testserver"
            settings = WebSettings(
                allowed_hosts=["testserver", "localhost", "127.0.0.1"],
                allowed_origins=[origin],
                ssh_control_dir=str(control_dir),
                discovery_timeout_s=15.0,
            )
            app = app_module.create_app(settings)

            asyncio.run(app.state.discovery_service.refresh())
            targets = app.state.discovery_service.get_targets()
            assert len(targets) == 1, f"expected one discovered target, got {targets!r}"
            target_id = targets[0].id
            assert targets[0].project == project

            with TestClient(app, base_url=origin) as client:
                created = client.post(
                    "/api/v1/terminals",
                    json={"session_target_id": target_id, "cols": 80, "rows": 24},
                    headers={"Origin": origin},
                )
                assert created.status_code == 201, created.text
                body = created.json()
                terminal_id = body["terminal_id"]
                token = body["ws_token"]

                with client.websocket_connect(
                    f"/api/v1/terminals/{terminal_id}",
                    subprotocols=["remo-terminal.v1", token],
                    headers={"Origin": origin},
                ) as ws:
                    assert ws.accepted_subprotocol == "remo-terminal.v1"
                    assert ws.receive_json() == {"v": 1, "type": "ready"}

                    # project-launch exits immediately after logging its
                    # call (no interactive stand-in needed for this proof),
                    # so the stream should reach a clean exit/EOF shortly.
                    deadline = time.monotonic() + 10.0
                    saw_launched = False
                    while time.monotonic() < deadline:
                        msg = ws.receive()
                        if msg.get("bytes") and b"LAUNCHED" in msg["bytes"]:
                            saw_launched = True
                        if msg.get("type") == "websocket.close" or msg.get("text"):
                            if msg.get("text"):
                                import json

                                payload = json.loads(msg["text"])
                                if payload.get("type") in ("exit", "error"):
                                    break
                    assert saw_launched, "web path never reached project-launch"

            web_log = _read_call_log(name, project)
            assert f"project={project}" in web_log

            # ------------------------------------------------------------
            # (b) Drive the CLI path directly: build the exact argv
            # shell_connect() would build via build_project_launch_remote_cmd()
            # + build_ssh_base_cmd(), then execute it ourselves against the
            # SAME container.
            # ------------------------------------------------------------
            remote_cmd = build_project_launch_remote_cmd(project, detach=False, exec_cmd=None)
            cli_ssh_cmd = build_ssh_base_cmd(
                host, tty=True, multiplex=False, control_dir=str(control_dir)
            )
            cli_ssh_cmd.append(remote_cmd)

            result = subprocess.run(
                cli_ssh_cmd,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert "LAUNCHED:" + project in result.stdout, (
                f"CLI path did not reach project-launch; stdout={result.stdout!r} "
                f"stderr={result.stderr!r}"
            )

            # ------------------------------------------------------------
            # (c) Prove identity: both invocations logged to the exact same
            # per-project-name state file (never per-caller) -- the proxy
            # signal for "same Zellij session" (SC-002).
            # ------------------------------------------------------------
            combined_log = _read_call_log(name, project)
            call_lines = [ln for ln in combined_log.splitlines() if ln.strip()]
            assert len(call_lines) == 2, (
                f"expected exactly 2 project-launch invocations logged to the "
                f"SAME {project}.log (one from web, one from CLI); got: {call_lines!r}"
            )
            for line in call_lines:
                assert f"project={project}" in line
        finally:
            _stop_container(name)
            shutil.rmtree(work_dir, ignore_errors=True)
