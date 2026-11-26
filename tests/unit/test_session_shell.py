"""remo-session-shell: the devcontainer start-up that used to live in ~/.bashrc (spec 029).

The rendered script is run in a temp HOME against shims for devcontainer, docker
and the user's login shell, so what is asserted is what actually executes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "ansible" / "roles" / "user_setup" / "templates"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

# Everything the script and remo-lib.sh call, linked into an isolated PATH so a
# tool can be left OUT (macOS has no sha256sum/timeout/setsid).
TOOLS = (
    "bash sh sed grep awk cat cut date dirname basename mktemp mv rm mkdir find sort tr tail "
    "head sleep id hostname env nohup printf chmod stat kill wc cp touch ln uname"
).split()


def tool_path(directory: Path, *, extra: tuple[str, ...] = ()) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    for name in (*TOOLS, *extra):
        real = shutil.which(name)
        if real and not (directory / name).exists():
            (directory / name).symlink_to(real)
    return str(directory)


def render(name: str, **ctx: str) -> str:
    ctx.setdefault("dev_workspace_dir", "")
    ctx.setdefault("devcontainer_cli_bin", "devcontainer")
    ctx.setdefault("devcontainer_up_extra_args", "")
    return Environment(autoescape=False).from_string((TEMPLATES / name).read_text()).render(**ctx)


class Session:
    def __init__(self, root: Path, *, engine_up: bool = True, sha: str = "sha256sum") -> None:
        self.root = root
        self.home = root / "home"
        (self.home / ".local" / "bin").mkdir(parents=True)
        self.log = root / "calls.log"
        self.log.write_text("")
        (self.home / ".local" / "bin" / "remo-lib.sh").write_text(render("remo-lib.sh.j2"))
        self.script = self.home / ".local" / "bin" / "remo-session-shell"
        self.script.write_text(render("remo-session-shell.sh.j2", devcontainer_up_extra_args="--x"))
        self.script.chmod(0o755)
        self.tools = Path(tool_path(root / "tools", extra=(sha,)))
        shims = root / "shims"
        shims.mkdir()
        self.shims = shims
        self._shim(
            "devcontainer",
            f'echo "devcontainer $* [REMO_SESSION_SHELL=${{REMO_SESSION_SHELL:-}}]" >>"{self.log}"',
        )
        self._shim(
            "docker",
            f'echo "docker $*" >>"{self.log}"\n'
            + ('[ "$1" = info ] && exit 1\n' if not engine_up else "")
            + "exit 0",
        )
        self._shim("login-shell", f'echo "login-shell $* [REMO_SESSION_SHELL=${{REMO_SESSION_SHELL:-}}]" >>"{self.log}"')
        if sha == "shasum":
            # BSD tool: `shasum -a 256` only.
            self._shim(
                "shasum",
                '[ "$1" = "-a" ] && [ "$2" = "256" ] || exit 2\n'
                'printf "%s  -\\n" "$(cat | wc -c | tr -d " ")"',
            )

    def _shim(self, name: str, body: str) -> None:
        path = self.shims / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def project(self, path: Path, *, devcontainer: bool) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if devcontainer:
            (path / ".devcontainer").mkdir(exist_ok=True)
            (path / ".devcontainer" / "devcontainer.json").write_text("{}")
        index = self.home / ".local" / "state" / "remo" / "projects.tsv"
        index.parent.mkdir(parents=True, exist_ok=True)
        with index.open("a") as fh:
            fh.write(f"{path.name}\t{path}\n")

    def run(
        self,
        session: str | None,
        *,
        extra_env: dict[str, str] | None = None,
        stdin: str = "x",
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": f"{self.shims}:{self.tools}",
            "HOME": str(self.home),
            "SHELL": str(self.shims / "login-shell"),
        }
        if session is not None:
            env["ZELLIJ_SESSION_NAME"] = session
        env.update(extra_env or {})
        assert BASH is not None
        return subprocess.run(
            [BASH, str(self.script)],
            capture_output=True,
            text=True,
            env=env,
            input=stdin,
            timeout=60,
            cwd=cwd,
        )

    def calls(self) -> list[str]:
        return [ln for ln in self.log.read_text().splitlines() if ln]


def _up(session: Session) -> str:
    return next(c for c in session.calls() if c.startswith("devcontainer up"))


@pytest.fixture
def session(tmp_path: Path) -> Session:
    return Session(tmp_path)


def test_devcontainer_project_outside_projects_dir_up_then_exec(session: Session, tmp_path: Path) -> None:
    proj = tmp_path / "code" / "demo"
    session.project(proj, devcontainer=True)
    result = session.run("demo")
    assert result.returncode == 0, result.stderr
    calls = session.calls()
    assert calls[0] == "docker info"  # the engine is checked before anything starts
    assert calls[1] == f"devcontainer up --workspace-folder {proj} --x [REMO_SESSION_SHELL=1]"
    exec_call = next(c for c in calls if c.startswith("devcontainer exec"))
    assert f"--workspace-folder {proj}" in exec_call
    assert "REMO_PROJECT=demo" in exec_call and "/bin/bash -c " in exec_call
    assert any(c.startswith("docker ps -q --filter label=devcontainer.local_folder=") for c in calls)
    assert not any(c.startswith("login-shell") for c in calls)


def test_remo_devcontainer_cmd_runs_inside_the_container(session: Session, tmp_path: Path) -> None:
    session.project(tmp_path / "demo", devcontainer=True)
    session.run("demo", extra_env={"REMO_DEVCONTAINER_CMD": "make test && echo done"})
    exec_call = next(c for c in session.calls() if c.startswith("devcontainer exec"))
    assert "/bin/bash -lc make test && echo done" in exec_call


def test_plain_project_execs_the_login_shell(session: Session, tmp_path: Path) -> None:
    session.project(tmp_path / "plain", devcontainer=False)
    result = session.run("plain")
    assert result.returncode == 0, result.stderr
    assert session.calls() == ["login-shell -l [REMO_SESSION_SHELL=1]"]


def test_plain_project_keeps_the_directory_the_pane_started_in(session: Session, tmp_path: Path) -> None:
    """Zellij starts a new pane in the focused pane's directory; a plain project
    must keep it (only the devcontainer path moves to the project root)."""
    proj = tmp_path / "plain"
    session.project(proj, devcontainer=False)
    sub = proj / "src" / "deep"
    sub.mkdir(parents=True)
    session._shim("login-shell", f'echo "login-shell cwd=$PWD" >>"{session.log}"')
    result = session.run("plain", cwd=sub)
    assert result.returncode == 0, result.stderr
    assert session.calls() == [f"login-shell cwd={sub}"]


@pytest.mark.parametrize("name", [None, "unknown-project"])
def test_no_session_or_unknown_project_falls_back_to_login_shell(session: Session, name: str | None) -> None:
    session.run(name)
    assert session.calls() == ["login-shell -l [REMO_SESSION_SHELL=1]"]


def test_inside_a_devcontainer_it_does_nothing_but_the_shell(session: Session, tmp_path: Path) -> None:
    session.project(tmp_path / "demo", devcontainer=True)
    session.run("demo", extra_env={"REMOTE_CONTAINERS": "true"})
    assert session.calls() == ["login-shell -l [REMO_SESSION_SHELL=1]"]


def test_engine_unreachable_prints_the_hint_and_never_runs_up(tmp_path: Path) -> None:
    down = Session(tmp_path, engine_up=False)
    proj = tmp_path / "demo"
    down.project(proj, devcontainer=True)
    result = down.run("demo")
    assert result.returncode == 0
    calls = down.calls()
    assert not any(c.startswith("devcontainer") for c in calls)
    assert calls[-1] == "login-shell -l [REMO_SESSION_SHELL=1]"
    for hint in ("Docker Desktop", "OrbStack", "colima start", "systemctl start docker"):
        assert hint in result.stdout
    assert "container engine is not running" in result.stdout


def test_up_failure_ends_the_session_with_status_1(session: Session, tmp_path: Path) -> None:
    session.project(tmp_path / "demo", devcontainer=True)
    session._shim("devcontainer", f'echo "devcontainer $*" >>"{session.log}"\nexit 7')
    result = session.run("demo")
    assert result.returncode == 1
    assert "devcontainer up failed" in result.stdout


def test_rebuild_when_the_config_hash_changes(session: Session, tmp_path: Path) -> None:
    proj = tmp_path / "demo"
    session.project(proj, devcontainer=True)
    session.run("demo")
    assert "--remove-existing-container" not in _up(session)
    session.log.write_text("")
    session.run("demo")  # unchanged config: no rebuild
    assert "--remove-existing-container" not in _up(session)
    (proj / ".devcontainer" / "devcontainer.json").write_text('{"name": "changed"}')
    session.log.write_text("")
    result = session.run("demo")
    assert "Devcontainer config changed, rebuilding..." in result.stdout
    assert "--remove-existing-container" in _up(session)


def test_manual_rebuild_flag_file_is_consumed(session: Session, tmp_path: Path) -> None:
    proj = tmp_path / "demo"
    session.project(proj, devcontainer=True)
    (proj / ".devcontainer-rebuild").write_text("")
    session.run("demo")
    assert "--remove-existing-container" in _up(session)
    assert not (proj / ".devcontainer-rebuild").exists()


def test_hash_uses_shasum_when_sha256sum_is_absent(tmp_path: Path) -> None:
    mac = Session(tmp_path, sha="shasum")
    # sha256sum must really be unavailable on this PATH
    assert not (mac.tools / "sha256sum").exists()
    proj = tmp_path / "demo"
    mac.project(proj, devcontainer=True)
    result = mac.run("demo")
    assert result.returncode == 0, result.stderr
    stored = (mac.home / ".cache" / "devcontainer-hashes" / "demo").read_text().strip()
    assert stored.isdigit()  # the shim's "hash" is the byte count of the piped config


def test_stdlib_tools_only_means_no_timeout_still_works(tmp_path: Path) -> None:
    """No timeout/gtimeout on PATH (macOS): the engine check uses the watchdog."""
    s = Session(tmp_path)
    assert not (s.tools / "timeout").exists()
    s.project(tmp_path / "demo", devcontainer=True)
    assert s.run("demo").returncode == 0
    assert os.path.exists(s.log)
