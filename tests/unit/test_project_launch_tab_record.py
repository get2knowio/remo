"""project-launch / project-menu record the tab's project (spec 028 FR-007/FR-009).

project-launch is executed against a fake zellij and a fake ``remo-host`` in a
tmp HOME so the ordering (record BEFORE attach) and the guards (no key, detach,
failing recorder) are proven by running it, not by reading it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "ansible" / "roles" / "user_setup" / "templates"
BASH = shutil.which("bash")
KEY = "0123456789abcdef0123456789abcdef"


def _render(name: str, projects: Path) -> str:
    return Environment(autoescape=False).from_string(
        (TEMPLATES / name).read_text()
    ).render(
        dev_workspace_dir=str(projects),
        devcontainer_cli_bin="devcontainer",
        devcontainer_up_extra_args="",
    )


@pytest.fixture
def env(tmp_path: Path):
    projects = tmp_path / "projects"
    (projects / "alpha").mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    shims = tmp_path / "shims"
    shims.mkdir()
    log = tmp_path / "calls.log"

    zellij = shims / "zellij"
    zellij.write_text(f'#!/bin/bash\necho "zellij $*" >>"{log}"\nexit 0\n')
    zellij.chmod(0o755)

    script = tmp_path / "project-launch"
    script.write_text(_render("project-launch.sh.j2", projects))
    script.chmod(0o755)

    def set_remo_host(exit_code: int = 0) -> None:
        rh = home / ".local" / "bin" / "remo-host"
        rh.write_text(f'#!/bin/bash\necho "remo-host $*" >>"{log}"\nexit {exit_code}\n')
        rh.chmod(0o755)

    def run(*args: str, tab_key: str | None = None):
        e = {"PATH": f"{shims}:/usr/bin:/bin", "HOME": str(home)}
        if tab_key is not None:
            e["REMO_TAB_KEY"] = tab_key
        return subprocess.run(
            [BASH, str(script), *args], capture_output=True, text=True, env=e
        )

    def calls() -> list[str]:
        return log.read_text().splitlines() if log.exists() else []

    return run, calls, set_remo_host


pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def test_records_before_attach(env) -> None:
    run, calls, set_remo_host = env
    set_remo_host()
    result = run("--project", "alpha", tab_key=KEY)
    assert result.returncode == 0, result.stderr
    seen = [c for c in calls() if not c.startswith("zellij list-sessions")]
    assert seen == [
        f"remo-host sessions record --key {KEY} --project alpha",
        "zellij attach --create alpha",
    ]


def test_no_key_never_calls_remo_host(env) -> None:
    run, calls, set_remo_host = env
    set_remo_host()
    result = run("--project", "alpha")
    assert result.returncode == 0, result.stderr
    assert not any(c.startswith("remo-host") for c in calls())
    assert "zellij attach --create alpha" in calls()


def test_empty_key_never_calls_remo_host(env) -> None:
    run, calls, set_remo_host = env
    set_remo_host()
    run("--project", "alpha", tab_key="")
    assert not any(c.startswith("remo-host") for c in calls())


def test_detach_never_records(env) -> None:
    run, calls, set_remo_host = env
    set_remo_host()
    result = run("--project", "alpha", "--detach", "--exec", "true", tab_key=KEY)
    assert result.returncode == 0, result.stderr
    assert not any(c.startswith("remo-host") for c in calls())


def test_failing_recorder_does_not_block_attach(env) -> None:
    run, calls, set_remo_host = env
    set_remo_host(exit_code=1)
    result = run("--project", "alpha", tab_key=KEY)
    assert result.returncode == 0, result.stderr
    assert "zellij attach --create alpha" in calls()


def test_missing_recorder_does_not_block_attach(env) -> None:
    """A host whose remo-host predates `sessions record` / is absent."""
    run, calls, _ = env
    result = run("--project", "alpha", tab_key=KEY)
    assert result.returncode == 0, result.stderr
    assert "zellij attach --create alpha" in calls()


def test_project_menu_records_before_attach_guarded() -> None:
    text = (TEMPLATES / "project-menu.sh.j2").read_text()
    body = text[text.index("launch_session() {") :]
    body = body[: body.index("\n}\n")]
    record = re.search(
        r'if \[ -n "\$\{REMO_TAB_KEY:-\}" \]; then\n\s+"\$HOME/\.local/bin/remo-host" '
        r'sessions record --key "\$REMO_TAB_KEY" --project "\$project_name" '
        r">/dev/null 2>&1 \|\| true\n\s+fi",
        body,
    )
    assert record, "project-menu launch_session lacks the guarded sessions record call"
    attach = body.index('zellij attach --create "$project_name"')
    assert record.start() < attach
    assert body.index('cd "$project_dir"') < record.start()
