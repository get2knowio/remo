"""The PROJECT MENU .bashrc hook and the explicit SSH menu command (spec 030).

Both are executed under real bash with a stub ``project-menu``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

from remo_cli.core.host_access import SSH_MENU_COMMAND

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_YML = REPO_ROOT / "ansible/roles/user_setup/tasks/main.yml"


def _hook_block() -> str:
    for task in yaml.safe_load(MAIN_YML.read_text()):
        if task.get("name") == "Configure project-menu auto-start on SSH login":
            return task["ansible.builtin.blockinfile"]["block"]
    raise AssertionError("PROJECT MENU task not found")


def _stub_menu(home: Path, exit_code: int = 0) -> Path:
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    marker = home / "menu-ran"
    menu = bin_dir / "project-menu"
    menu.write_text(f'#!/bin/bash\necho "REMO_MENU=${{REMO_MENU:-}}" >> "{marker}"\nexit {exit_code}\n')
    menu.chmod(0o755)
    return marker


def _run_hook(home: Path, *, interactive: bool, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    rc = home / "rc.sh"
    # Mirrors the real .bashrc, where ~/.local/bin is on PATH before the block.
    rc.write_text(f'PATH="{home}/.local/bin:$PATH"\n' + _hook_block())
    env = {"PATH": os.environ["PATH"], "HOME": str(home), **env_extra}
    argv = ["bash", "--norc", "--noprofile"]
    if interactive:
        argv.append("-i")
    argv += ["-c", f"source {rc}; echo survived"]
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)


def test_hook_fires_interactively_over_ssh(tmp_path):
    marker = _stub_menu(tmp_path)
    r = _run_hook(tmp_path, interactive=True, env_extra={"SSH_CONNECTION": "a b c d"})
    assert marker.read_text() == "REMO_MENU=\n"
    assert "survived" in r.stdout


def test_hook_exits_when_menu_exits_nonzero(tmp_path):
    marker = _stub_menu(tmp_path, exit_code=1)
    r = _run_hook(tmp_path, interactive=True, env_extra={"SSH_TTY": "/dev/pts/1"})
    assert marker.exists()
    assert "survived" not in r.stdout


def test_hook_does_not_fire_with_remo_menu(tmp_path):
    marker = _stub_menu(tmp_path)
    r = _run_hook(
        tmp_path, interactive=True, env_extra={"SSH_CONNECTION": "a b c d", "REMO_MENU": "1"}
    )
    assert not marker.exists()
    assert "survived" in r.stdout


def test_hook_does_not_fire_non_interactively(tmp_path):
    marker = _stub_menu(tmp_path)
    r = _run_hook(tmp_path, interactive=False, env_extra={"SSH_CONNECTION": "a b c d"})
    assert not marker.exists()
    assert "survived" in r.stdout


def test_hook_does_not_fire_outside_ssh(tmp_path):
    marker = _stub_menu(tmp_path)
    _run_hook(tmp_path, interactive=True, env_extra={})
    assert not marker.exists()


def _run_menu_command(home: Path, shell_stub: Path | None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    if shell_stub is not None:
        env["SHELL"] = str(shell_stub)
    return subprocess.run(
        ["bash", "-c", SSH_MENU_COMMAND], env=env, capture_output=True, text=True, timeout=20
    )


def _shell_stub(home: Path) -> Path:
    stub = home / "fake-shell"
    stub.write_text(f'#!/bin/bash\necho "shell REMO_MENU=$REMO_MENU args=$*" > "{home}/shell-ran"\n')
    stub.chmod(0o755)
    return stub


def test_menu_command_exit_zero_execs_login_shell_with_guard(tmp_path):
    marker = _stub_menu(tmp_path, exit_code=0)
    r = _run_menu_command(tmp_path, _shell_stub(tmp_path))
    assert r.returncode == 0
    assert marker.read_text() == "REMO_MENU=1\n"
    assert (tmp_path / "shell-ran").read_text() == "shell REMO_MENU=1 args=-l\n"


def test_menu_command_nonzero_ends_without_a_shell(tmp_path):
    _stub_menu(tmp_path, exit_code=1)
    r = _run_menu_command(tmp_path, _shell_stub(tmp_path))
    assert r.returncode == 1
    assert not (tmp_path / "shell-ran").exists()


def test_menu_command_without_shell_variable_falls_back_to_bin_sh(tmp_path):
    _stub_menu(tmp_path, exit_code=0)
    r = _run_menu_command(tmp_path, None)
    # /bin/sh -l with no stdin exits cleanly; the point is it did not fail to exec.
    assert r.returncode == 0


# --- the .bashrc TERM fallback, applied by project-menu itself ------------------------
#
# The .bashrc TERM FALLBACK block is inserted right before the PROJECT MENU hook,
# so the old login-hook path always ran the menu with a TERM the host knows. The
# explicit SSH menu command bypasses .bashrc, so project-menu applies it itself.

TEMPLATES = REPO_ROOT / "ansible/roles/user_setup/templates"


def _source_menu_header(home: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    render = Environment(autoescape=False)
    ctx = {"dev_workspace_dir": "", "remo_version": "", "devcontainer_cli_bin": "devcontainer",
           "devcontainer_up_extra_args": ""}
    for name, dest in (("remo-lib.sh.j2", "remo-lib.sh"), ("project-menu.sh.j2", "project-menu")):
        (bin_dir / dest).write_text(render.from_string((TEMPLATES / name).read_text()).render(**ctx))
    bash = shutil.which("bash")
    assert bash is not None
    # Sourced, so main() does not run: only the header (and its TERM fallback) does.
    return subprocess.run(
        [bash, "-c", f'. "{bin_dir}/project-menu"; printf "%s" "$TERM"'],
        env={"PATH": os.environ["PATH"], "HOME": str(home), **env_extra},
        capture_output=True, text=True, timeout=20,
    )


needs_infocmp = pytest.mark.skipif(shutil.which("infocmp") is None, reason="infocmp not available")


@needs_infocmp
def test_menu_over_ssh_falls_back_from_an_unknown_term(tmp_path):
    r = _source_menu_header(
        tmp_path, {"SSH_CONNECTION": "a b c d", "TERM": "xterm-remo-test-unknown"}
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout == "xterm-256color"


@needs_infocmp
def test_menu_keeps_a_known_term_and_the_desk_term(tmp_path):
    known = _source_menu_header(tmp_path, {"SSH_CONNECTION": "a b c d", "TERM": "xterm"})
    assert known.stdout == "xterm"
    desk = _source_menu_header(tmp_path, {"TERM": "xterm-remo-test-unknown"})
    assert desk.stdout == "xterm-remo-test-unknown"
