"""One session directory on macOS, and the tool PATH recorded at setup (spec 030).

The rendered ``remo-lib.sh`` is EXECUTED with a ``uname`` shim, so the Darwin
behaviour is covered on a Linux runner (precedent: test_host_stats_darwin.py).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment

from tests.unit.test_zellij_socket_dir import _invokes_zellij

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "ansible" / "roles" / "user_setup" / "templates"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

SCRIPTS = ["project-menu.sh.j2", "project-launch.sh.j2", "remo-host.sh.j2", "devshell.sh.j2"]


def _lib(tmp_path: Path) -> Path:
    path = tmp_path / "remo-lib.sh"
    path.write_text(Environment(autoescape=False).from_string((TEMPLATES / "remo-lib.sh.j2").read_text()).render())
    return path


def _shim_uname(tmp_path: Path, system: str) -> Path:
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    uname = shims / "uname"
    uname.write_text(f'#!/bin/sh\n[ "$1" = "-s" ] && echo {system} || /usr/bin/uname "$@"\n')
    uname.chmod(0o755)
    return shims


def _run(tmp_path: Path, body: str, *, system: str, env_extra: dict[str, str] | None = None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    shims = _shim_uname(tmp_path, system)
    lib = _lib(tmp_path)
    env = {"PATH": f"{shims}:/usr/bin:/bin", "HOME": str(home), **(env_extra or {})}
    assert BASH is not None
    return subprocess.run(
        [BASH, "-c", f'. "{lib}"; {body}'], env=env, capture_output=True, text=True, timeout=30
    )


def _session_dir(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".local" / "state" / "remo" / "zellij"


class TestPinSessionDir:
    def test_darwin_unset_creates_private_dir_and_exports(self, tmp_path):
        r = _run(tmp_path, 'remo_pin_session_dir && printf "%s" "$ZELLIJ_SOCKET_DIR"', system="Darwin")
        assert r.returncode == 0, r.stderr
        assert r.stdout == str(_session_dir(tmp_path))
        assert oct(_session_dir(tmp_path).stat().st_mode & 0o777) == "0o700"

    def test_darwin_creates_remo_state_dir_private(self, tmp_path):
        """The pin can be the first thing to create ~/.local/state/remo (before
        the project index); that directory must still come out 0700."""
        r = _run(tmp_path, "remo_pin_session_dir", system="Darwin")
        assert r.returncode == 0, r.stderr
        assert oct(_session_dir(tmp_path).parent.stat().st_mode & 0o777) == "0o700"

    def test_darwin_second_call_is_a_no_op(self, tmp_path):
        r = _run(tmp_path, "remo_pin_session_dir; remo_pin_session_dir; echo ok", system="Darwin")
        assert r.returncode == 0 and r.stdout.strip() == "ok"

    def test_darwin_user_set_variable_is_untouched(self, tmp_path):
        r = _run(
            tmp_path,
            'remo_pin_session_dir && printf "%s" "$ZELLIJ_SOCKET_DIR"',
            system="Darwin",
            env_extra={"ZELLIJ_SOCKET_DIR": "/mine"},
        )
        assert r.stdout == "/mine"
        assert not _session_dir(tmp_path).exists()

    def test_darwin_open_directory_is_an_error(self, tmp_path):
        d = _session_dir(tmp_path)
        d.mkdir(parents=True)
        d.chmod(0o755)
        r = _run(tmp_path, "remo_pin_session_dir", system="Darwin")
        assert r.returncode == 1
        assert "refusing to use the session directory" in r.stderr
        assert "chmod 700" in r.stderr

    def test_darwin_group_only_open_is_an_error(self, tmp_path):
        d = _session_dir(tmp_path)
        d.mkdir(parents=True)
        d.chmod(0o770)
        assert _run(tmp_path, "remo_pin_session_dir", system="Darwin").returncode == 1

    def test_darwin_losing_the_create_race_is_not_an_error(self, tmp_path):
        # Another script creates the directory between the -d test and mkdir:
        # simulated with a mkdir shim that makes it first, then fails EEXIST.
        shims = _shim_uname(tmp_path, "Darwin")
        (shims / "mkdir").write_text(
            '#!/bin/sh\n/bin/mkdir "$@" 2>/dev/null\n'
            'for a in "$@"; do last="$a"; done\n'
            '[ -d "$last" ] && [ "${last##*/}" = zellij ] && { echo "mkdir: File exists" >&2; exit 1; }\n'
            "exit 0\n"
        )
        (shims / "mkdir").chmod(0o755)
        r = _run(tmp_path, 'remo_pin_session_dir && printf "%s" "$ZELLIJ_SOCKET_DIR"', system="Darwin")
        assert r.returncode == 0, r.stderr
        assert r.stdout == str(_session_dir(tmp_path))

    def test_darwin_uncreatable_dir_is_an_error(self, tmp_path):
        state = tmp_path / "home" / ".local" / "state"
        state.mkdir(parents=True)
        (state / "remo").write_text("not a directory")
        r = _run(tmp_path, "remo_pin_session_dir", system="Darwin")
        assert r.returncode == 1
        assert "cannot create the session directory" in r.stderr

    def test_linux_is_a_no_op(self, tmp_path):
        r = _run(tmp_path, 'remo_pin_session_dir; printf "%s" "${ZELLIJ_SOCKET_DIR:-unset}"', system="Linux")
        assert r.returncode == 0
        assert r.stdout == "unset"
        assert not _session_dir(tmp_path).exists()


class TestEveryScriptPinsBeforeZellij:
    @pytest.mark.parametrize("name", SCRIPTS)
    def test_pin_follows_xdg_block_and_precedes_first_zellij(self, name):
        lines = (TEMPLATES / name).read_text().splitlines()
        xdg = next(i for i, ln in enumerate(lines) if "export XDG_RUNTIME_DIR=" in ln)
        pin = next(i for i, ln in enumerate(lines) if ln.strip() == "remo_pin_session_dir || exit 1")
        load = next(i for i, ln in enumerate(lines) if ln.strip() == "remo_load_local_path")
        first = min(i for i, ln in enumerate(lines) if _invokes_zellij(ln))
        assert xdg < pin < load < first


def _write_env(tmp_path: Path, text: str) -> None:
    state = tmp_path / "home" / ".local" / "state" / "remo"
    state.mkdir(parents=True, exist_ok=True)
    (state / "local.env").write_text(text)


class TestLoadLocalPath:
    def test_prepends_missing_dirs_once(self, tmp_path):
        _write_env(tmp_path, "REMO_BASH=/b/bash\nREMO_PATH=/a:/b\nREMO_PROJECTS_DIR=\n")
        r = _run(
            tmp_path,
            'remo_load_local_path; remo_load_local_path; printf "%s" "$PATH"',
            system="Darwin",
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.startswith("/a:/b:")
        assert r.stdout.count("/a") == 1

    def test_dirs_already_present_are_not_added(self, tmp_path):
        _write_env(tmp_path, "REMO_PATH=/usr/bin:/new\n")
        r = _run(tmp_path, 'remo_load_local_path; printf "%s" "$PATH"', system="Darwin")
        assert r.stdout.startswith("/new:")
        assert r.stdout.count("/usr/bin") == 1

    def test_no_local_env_leaves_path_unchanged(self, tmp_path):
        r = _run(tmp_path, 'before="$PATH"; remo_load_local_path; [ "$before" = "$PATH" ] && echo same', system="Darwin")
        assert r.stdout.strip() == "same"

    def test_empty_or_absent_line_leaves_path_unchanged(self, tmp_path):
        _write_env(tmp_path, "REMO_BASH=/b\nREMO_PATH=\n")
        r = _run(tmp_path, 'before="$PATH"; remo_load_local_path; [ "$before" = "$PATH" ] && echo same', system="Darwin")
        assert r.stdout.strip() == "same"

    def test_path_with_spaces_survives(self, tmp_path):
        _write_env(tmp_path, "REMO_PATH=/opt/my tools/bin\n")
        r = _run(tmp_path, 'remo_load_local_path; printf "%s" "$PATH"', system="Darwin")
        assert r.stdout.startswith("/opt/my tools/bin:")

    def test_glob_characters_are_taken_literally(self, tmp_path):
        # A matching directory exists, so an unquoted expansion would swap it in.
        (tmp_path / "gx").mkdir()
        literal = f"{tmp_path}/g*"
        _write_env(tmp_path, f"REMO_PATH={literal}\n")
        r = _run(tmp_path, 'remo_load_local_path; printf "%s" "$PATH"', system="Darwin")
        assert r.stdout.startswith(literal + ":")
