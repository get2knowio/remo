"""ansible/local_setup.yml run for real, twice, in a throwaway HOME (spec 029 FR-012, SC-008).

Offline by construction: the play needs no Galaxy collection, so this invokes
ansible-playbook directly rather than through ``run_playbook`` (which would try
to install collections first).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSIBLE_DIR = REPO_ROOT / "ansible"
PLAYBOOK = ANSIBLE_DIR / "local_setup.yml"

ANSIBLE_PLAYBOOK = Path(sys.executable).parent / "ansible-playbook"

RC_FILES = (".bashrc", ".zshrc", ".profile", ".bash_profile", ".zprofile")

SCRIPTS = (
    "remo-lib.sh",
    "remo-host",
    "project-menu",
    "project-launch",
    "devshell",
    "remo-session-shell",
)


def _play() -> dict:
    return yaml.safe_load(PLAYBOOK.read_text())[0]


def test_play_is_local_unprivileged_and_factless() -> None:
    play = _play()
    assert play["hosts"] == "localhost"
    assert play["connection"] == "local"
    assert play["become"] is False
    assert play["gather_facts"] is False
    assert play["vars"]["remo_local_install"] is True


def test_play_includes_the_shared_script_tasks() -> None:
    text = PLAYBOOK.read_text()
    assert "tasks_from: host_scripts" in text


def test_play_has_no_rc_file_tasks() -> None:
    # Comments may mention the rc files; the tasks must not touch them.
    code = "\n".join(
        line for line in PLAYBOOK.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    for needle in ("blockinfile", "lineinfile", ".bashrc", ".zshrc", ".profile", "bash_profile"):
        assert needle not in code


def _run(home: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_FORCE_COLOR": "0",
        "ANSIBLE_LOCAL_TEMP": str(home / ".ansible" / "tmp"),
        "ANSIBLE_HOME": str(home / ".ansible"),
        "ANSIBLE_RETRY_FILES_ENABLED": "0",
    }
    return subprocess.run(
        [
            str(ANSIBLE_PLAYBOOK),
            "local_setup.yml",
            "-e",
            f"ansible_python_interpreter={sys.executable}",
            "-e",
            "remo_bash_path=/usr/bin/env-bash-for-test",
            "-e",
            "remo_version=9.9.9",
            *extra,
        ],
        cwd=ANSIBLE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _recap(out: str) -> dict[str, int]:
    match = re.search(r"localhost\s+:\s+(ok=\d+.*)", out)
    assert match, out
    return {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", match.group(1))}


def _snapshot(root: Path) -> dict[str, str]:
    snap: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".ansible" not in p.parts:
            snap[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


@pytest.mark.skipif(not ANSIBLE_PLAYBOOK.exists(), reason="ansible-playbook not installed")
def test_local_setup_installs_then_second_run_changes_nothing(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    home = sandbox / "home"
    home.mkdir(parents=True)
    # Pre-seeded rc files: the play must not touch a byte of them.
    seeded = {name: f"# my {name}\nexport KEEP=1\n" for name in RC_FILES}
    for name, text in seeded.items():
        (home / name).write_text(text)
    siblings = sandbox / "elsewhere"
    siblings.mkdir()
    (siblings / "sentinel").write_text("x")

    first = _run(home)
    assert first.returncode == 0, first.stdout + first.stderr
    assert _recap(first.stdout)["changed"] > 0

    bin_dir = home / ".local" / "bin"
    for name in SCRIPTS:
        assert (bin_dir / name).is_file(), name
    for name in SCRIPTS[1:]:
        assert os.access(bin_dir / name, os.X_OK), name
    assert (bin_dir / "remo-host").read_text().startswith("#!/usr/bin/env-bash-for-test\n")
    state = home / ".local" / "state" / "remo"
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert (state / "tabs").is_dir()
    assert (state / "local.env").read_text() == (
        "REMO_BASH=/usr/bin/env-bash-for-test\nREMO_PROJECTS_DIR=\nREMO_PATH=\n"
    )
    assert (home / ".remo-version").read_text() == "9.9.9"
    assert (state / "host-info").read_text() == (
        "host_kind=local\nmenu_command=1\ninstalled_by=9.9.9\n"
    )
    assert stat.S_IMODE((state / "host-info").stat().st_mode) == 0o644

    before = _snapshot(sandbox)
    second = _run(home)
    assert second.returncode == 0, second.stdout + second.stderr
    recap = _recap(second.stdout)
    assert recap["changed"] == 0, second.stdout
    assert recap["failed"] == 0

    for name, text in seeded.items():
        assert (home / name).read_text() == text, f"{name} was modified"
    # Nothing outside HOME was written, and the second run changed no file.
    assert _snapshot(sandbox) == before
    assert (siblings / "sentinel").read_text() == "x"
    assert sorted(p.name for p in sandbox.iterdir()) == ["elsewhere", "home"]


@pytest.mark.skipif(not ANSIBLE_PLAYBOOK.exists(), reason="ansible-playbook not installed")
def test_no_version_marker_without_a_version(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = _run(home, "-e", "remo_version=")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (home / ".remo-version").exists()
    # the default shebang is what remote hosts get
    result2 = _run(home, "-e", "remo_bash_path=/bin/bash")
    assert result2.returncode == 0
    assert (home / ".local" / "bin" / "remo-host").read_text().startswith("#!/bin/bash\n")


def test_projects_dir_is_persisted_verbatim_and_idempotent(tmp_path: Path) -> None:
    """#281: local.env records the projects folder (spaces intact) so a setup
    after `remo remove local` can restore it; a re-run changes nothing."""
    home = tmp_path / "home"
    home.mkdir()
    folder = tmp_path / "My Projects"
    folder.mkdir()
    # One JSON -e document, exactly as providers/local.setup passes it.
    doc = json.dumps({"remo_local_projects_dir": str(folder)})
    first = _run(home, "-e", doc)
    assert first.returncode == 0, first.stdout + first.stderr
    env = home / ".local" / "state" / "remo" / "local.env"
    assert env.read_text() == (
        f"REMO_BASH=/usr/bin/env-bash-for-test\nREMO_PROJECTS_DIR={folder}\nREMO_PATH=\n"
    )
    assert f'PROJECTS_DIR="{folder}"' in (home / ".local" / "bin" / "remo-host").read_text()

    second = _run(home, "-e", doc)
    assert second.returncode == 0, second.stdout + second.stderr
    assert _recap(second.stdout)["changed"] == 0, second.stdout

    cleared = _run(home, "-e", json.dumps({"remo_local_projects_dir": ""}))
    assert cleared.returncode == 0, cleared.stdout + cleared.stderr
    assert env.read_text() == "REMO_BASH=/usr/bin/env-bash-for-test\nREMO_PROJECTS_DIR=\nREMO_PATH=\n"


def _render_host_info(**variables: object) -> str:
    import jinja2

    path = ANSIBLE_DIR / "roles" / "user_setup" / "templates" / "host-info.j2"
    env = jinja2.Environment(keep_trailing_newline=True)
    env.filters["bool"] = bool  # ansible's filter; plain bool is enough for True/False
    return env.from_string(path.read_text()).render(**variables)


def test_host_info_template_remote_and_local() -> None:
    assert _render_host_info(remo_version="1.2.3") == (
        "host_kind=remote\nmenu_command=1\ninstalled_by=1.2.3\n"
    )
    assert _render_host_info(remo_local_install=True) == (
        "host_kind=local\nmenu_command=1\ninstalled_by=\n"
    )
    assert _render_host_info(remo_local_install=False).startswith("host_kind=remote\n")


def test_host_info_tasks_are_defensive_and_ordered() -> None:
    tasks = yaml.safe_load(
        (ANSIBLE_DIR / "roles/user_setup/tasks/host_scripts.yml").read_text()
    )
    names = [t["name"] for t in tasks]
    assert names[-2:] == ["Ensure the remo state directory exists", "Install host-info record"]
    text = (ANSIBLE_DIR / "roles/user_setup/tasks/host_scripts.yml").read_text()
    assert ".local/state/remo/host-info" in text


@pytest.mark.skipif(not ANSIBLE_PLAYBOOK.exists(), reason="ansible-playbook not installed")
def test_tool_path_is_recorded_in_local_env_and_rerun_is_a_no_op(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    extra = ("-e", json.dumps({"remo_local_path": "/opt/homebrew/bin:/opt/my tools/bin"}))
    first = _run(home, *extra)
    assert first.returncode == 0, first.stdout + first.stderr
    env = home / ".local" / "state" / "remo" / "local.env"
    assert "REMO_PATH=/opt/homebrew/bin:/opt/my tools/bin\n" in env.read_text()
    second = _run(home, *extra)
    assert _recap(second.stdout)["changed"] == 0, second.stdout
