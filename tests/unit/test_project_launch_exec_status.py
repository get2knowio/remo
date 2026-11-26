"""`project-launch --exec` returns the command's exit status (spec 029 US3 #2, #279).

For a devcontainer project the command runs inside the project's zellij session,
whose shell is remo-session-shell, so zellij's own exit status never carries it.
These tests run the REAL rendered project-launch -> remo-session-shell chain in a
temp HOME: the zellij shim starts the session's --default-shell the way zellij
creates a session's first pane, and the devcontainer shim really executes the
`env ... /bin/bash -lc CMD` it is handed, so the status asserted is the one the
command produced.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "ansible" / "roles" / "user_setup" / "templates"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

# zellij: `attach --create NAME options --default-shell SH --default-cwd DIR`
# runs SH in DIR as the session's first pane (ZELLIJ_SESSION_NAME=NAME), then
# exits with ZELLIJ_RC. ZELLIJ_SKIP_PANE=1 models attaching to a session that
# already exists (or a detach): the pane's shell never runs here.
ZELLIJ = """#!/bin/bash
echo "zellij $*" >>"$CALLS"
[ "$1" = attach ] || exit 0
name="$3"; shell=""; cwd=""
shift 4
while [ $# -gt 0 ]; do
    case "$1" in
        --default-shell) shell="$2"; shift 2 ;;
        --default-cwd) cwd="$2"; shift 2 ;;
        *) shift ;;
    esac
done
if [ "${ZELLIJ_SKIP_PANE:-}" != 1 ]; then
    (cd "$cwd" && ZELLIJ_SESSION_NAME="$name" "$shell" </dev/null)
fi
exit "${ZELLIJ_RC:-0}"
"""

# devcontainer: `up` succeeds unless UP_RC says otherwise; `exec` runs the
# command it was given (everything from `env` on), with its real exit status.
DEVCONTAINER = """#!/bin/bash
echo "devcontainer $1" >>"$CALLS"
if [ "$1" = up ]; then exit "${UP_RC:-0}"; fi
while [ $# -gt 0 ] && [ "$1" != env ]; do shift; done
exec "$@"
"""

DOCKER = """#!/bin/bash
echo "docker $*" >>"$CALLS"
exit 0
"""


def _render(name: str, projects: Path) -> str:
    return Environment(autoescape=False).from_string((TEMPLATES / name).read_text()).render(
        dev_workspace_dir=str(projects),
        devcontainer_cli_bin="devcontainer",
        devcontainer_up_extra_args="",
    )


class Host:
    def __init__(self, root: Path) -> None:
        self.projects = root / "projects"
        self.home = root / "home"
        self.bin = self.home / ".local" / "bin"
        self.bin.mkdir(parents=True)
        self.status_dir = self.home / ".local" / "state" / "remo" / "exec-status"
        self.calls_log = root / "calls.log"
        shims = root / "shims"
        shims.mkdir()
        self.shims = shims
        for name, body in (("zellij", ZELLIJ), ("devcontainer", DEVCONTAINER), ("docker", DOCKER)):
            (shims / name).write_text(body)
            (shims / name).chmod(0o755)
        (self.bin / "remo-lib.sh").write_text(_render("remo-lib.sh.j2", self.projects))
        for name, template in (
            ("project-launch", "project-launch.sh.j2"),
            ("remo-session-shell", "remo-session-shell.sh.j2"),
        ):
            (self.bin / name).write_text(_render(template, self.projects))
            (self.bin / name).chmod(0o755)

    def project(self, name: str, *, devcontainer: bool) -> Path:
        path = self.projects / name
        path.mkdir(parents=True)
        if devcontainer:
            (path / ".devcontainer").mkdir()
            (path / ".devcontainer" / "devcontainer.json").write_text("{}")
        return path

    def env(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": f"{self.shims}:/usr/bin:/bin",
            "HOME": str(self.home),
            "SHELL": "/bin/sh",
            "CALLS": str(self.calls_log),
            **extra,
        }

    def launch(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        return subprocess.run(
            [BASH, str(self.bin / "project-launch"), *args],
            capture_output=True,
            text=True,
            env=self.env(**extra),
            stdin=subprocess.DEVNULL,
            timeout=60,
        )

    def calls(self) -> list[str]:
        return self.calls_log.read_text().splitlines() if self.calls_log.exists() else []

    def leftover_status_files(self) -> list[Path]:
        return sorted(self.status_dir.iterdir()) if self.status_dir.exists() else []


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


@pytest.mark.parametrize("status", [1, 7, 42])
def test_failing_exec_in_a_devcontainer_returns_its_status(host: Host, status: int) -> None:
    host.project("alpha", devcontainer=True)
    result = host.launch("--project", "alpha", "--exec", f"echo ran-in-$REMO_PROJECT; exit {status}")
    assert result.returncode == status, result.stderr
    # It really went through zellij -> remo-session-shell -> devcontainer exec.
    assert any(c.startswith("zellij attach --create alpha") for c in host.calls())
    assert "devcontainer exec" in host.calls()
    assert host.leftover_status_files() == []


def test_succeeding_exec_in_a_devcontainer_returns_0(host: Host, tmp_path: Path) -> None:
    host.project("alpha", devcontainer=True)
    marker = tmp_path / "ran"
    result = host.launch("--project", "alpha", "--exec", f"touch {marker}")
    assert result.returncode == 0, result.stderr
    assert marker.exists()
    assert host.leftover_status_files() == []


def test_exec_status_wins_over_zellij_status(host: Host) -> None:
    host.project("alpha", devcontainer=True)
    assert host.launch("--project", "alpha", "--exec", "exit 3", ZELLIJ_RC="9").returncode == 3
    assert host.launch("--project", "alpha", "--exec", "true", ZELLIJ_RC="9").returncode == 0


def test_up_failure_under_exec_is_a_failure(host: Host) -> None:
    host.project("alpha", devcontainer=True)
    result = host.launch("--project", "alpha", "--exec", "true", UP_RC="5")
    assert result.returncode == 1
    assert "devcontainer exec" not in host.calls()
    assert host.leftover_status_files() == []


@pytest.mark.parametrize("zellij_rc", [0, 4])
def test_no_reported_status_falls_back_to_zellij(host: Host, zellij_rc: int) -> None:
    """Detached, or an existing session that never runs the command: as before,
    zellij's own status is the launch's, and the status file is still removed."""
    host.project("alpha", devcontainer=True)
    result = host.launch(
        "--project", "alpha", "--exec", "exit 7", ZELLIJ_SKIP_PANE="1", ZELLIJ_RC=str(zellij_rc)
    )
    assert result.returncode == zellij_rc
    assert "devcontainer exec" not in host.calls()
    assert host.leftover_status_files() == []


def test_plain_launch_is_unaffected(host: Host) -> None:
    """No --exec: project-launch still execs zellij (its status is the launch's),
    a failing interactive shell does not turn into a launch failure, and no
    status file is ever created."""
    host.project("alpha", devcontainer=True)
    (host.shims / "devcontainer").write_text(
        DEVCONTAINER.replace('exec "$@"', "exit 6")
    )
    result = host.launch("--project", "alpha")
    assert result.returncode == 0, result.stderr
    assert "devcontainer exec" in host.calls()
    assert not host.status_dir.exists()
    assert host.launch("--project", "alpha", ZELLIJ_RC="2").returncode == 2
    assert not host.status_dir.exists()


def test_plain_project_exec_still_returns_its_status(host: Host) -> None:
    """No devcontainer: --exec bypasses zellij entirely (unchanged)."""
    host.project("plain", devcontainer=False)
    result = host.launch("--project", "plain", "--exec", "exit 4")
    assert result.returncode == 4
    assert not any(c.startswith("zellij attach") for c in host.calls())
    assert not host.status_dir.exists()


def test_session_shell_never_recreates_a_removed_status_file(host: Host) -> None:
    """A pane that finishes after project-launch stopped waiting (it detached)
    must not leave a status file behind."""
    proj = host.project("alpha", devcontainer=True)
    gone = host.status_dir / "launch.gone"
    assert BASH is not None
    result = subprocess.run(
        [BASH, str(host.bin / "remo-session-shell")],
        capture_output=True,
        text=True,
        env=host.env(
            ZELLIJ_SESSION_NAME="alpha",
            REMO_DEVCONTAINER_CMD="exit 3",
            REMO_EXEC_STATUS_FILE=str(gone),
        ),
        stdin=subprocess.DEVNULL,
        cwd=proj,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Command exited with status 3." in result.stdout
    assert not gone.exists()
