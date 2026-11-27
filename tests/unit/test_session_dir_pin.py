"""One session directory on macOS, and the tool PATH recorded at setup (spec 030).

The rendered ``remo-lib.sh`` is EXECUTED with a ``uname`` shim, so the Darwin
behaviour is covered on a Linux runner (precedent: test_host_stats_darwin.py).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment

from tests.unit.test_ansible_templates import _install_lib, _render_remo_host
from tests.unit.test_zellij_socket_dir import _invokes_zellij

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "ansible" / "roles" / "user_setup" / "templates"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

#: Scripts that pin at top level: every run of them is a session. remo-host
#: pins per verb instead (TestRemoHostPinsPerVerb).
SCRIPTS = ["project-menu.sh.j2", "project-launch.sh.j2", "devshell.sh.j2"]

PIN = "remo_pin_session_dir || exit 1"


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

    def test_darwin_dir_owned_by_someone_else_says_remove_it(self, tmp_path):
        # chmod cannot fix another user's directory, so the message must not
        # offer it. An `id` shim reports a different uid than the owner's.
        d = _session_dir(tmp_path)
        d.mkdir(parents=True)
        d.chmod(0o700)
        shims = _shim_uname(tmp_path, "Darwin")
        (shims / "id").write_text('#!/bin/sh\n[ "$1" = "-u" ] && echo 4242424 || /usr/bin/id "$@"\n')
        (shims / "id").chmod(0o755)
        r = _run(tmp_path, "remo_pin_session_dir", system="Darwin")
        assert r.returncode == 1
        assert "refusing to use the session directory" in r.stderr
        assert "not you" in r.stderr
        assert f"rm -rf '{d}'" in r.stderr
        assert "chmod" not in r.stderr

    def test_darwin_open_directory_names_the_exact_chmod(self, tmp_path):
        d = _session_dir(tmp_path)
        d.mkdir(parents=True)
        d.chmod(0o755)
        r = _run(tmp_path, "remo_pin_session_dir", system="Darwin")
        assert f"chmod 700 '{d}'" in r.stderr
        assert "mode 755" in r.stderr

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
        pin = next(i for i, ln in enumerate(lines) if ln.strip() == PIN)
        load = next(i for i, ln in enumerate(lines) if ln.strip() == "remo_load_local_path")
        first = min(i for i, ln in enumerate(lines) if _invokes_zellij(ln))
        assert xdg < pin < load < first


# --- remo-host: pinned per verb, not at top level (#286) ---------------------

_FUNC_START = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\) \{$")
#: `command -v zellij` only asks whether zellij is installed (capabilities'
#: has_zellij); it never touches the session namespace.
_PROBE = re.compile(r"command -v zellij")


def _remo_host_functions() -> tuple[dict[str, list[str]], list[str]]:
    """Function name -> body lines, plus the top-level lines, of remo-host."""
    funcs: dict[str, list[str]] = {}
    top: list[str] = []
    current: str | None = None
    for ln in (TEMPLATES / "remo-host.sh.j2").read_text().splitlines():
        if current is None:
            m = _FUNC_START.match(ln)
            if m:
                current = m.group(1)
                funcs[current] = []
            else:
                top.append(ln)
        elif ln == "}":
            current = None
        else:
            funcs[current].append(ln)
    return funcs, top


def _calls(line: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", line))


def _touches_directly(line: str) -> bool:
    if line.lstrip().startswith("#"):
        return False
    return _invokes_zellij(_PROBE.sub("", line)) or "/project-launch" in line


def _session_touchers(funcs: dict[str, list[str]]) -> set[str]:
    """Functions that reach the zellij namespace, directly or via a helper."""
    touching = {f for f, body in funcs.items() if any(_touches_directly(ln) for ln in body)}
    while True:
        grown = {
            f
            for f, body in funcs.items()
            if f not in touching
            and any(_calls(ln, t) for ln in body if not ln.lstrip().startswith("#") for t in touching)
        }
        if not grown:
            return touching
        touching |= grown


def _touch_lines(body: list[str], helpers: set[str]) -> list[int]:
    return [
        i
        for i, ln in enumerate(body)
        if not ln.lstrip().startswith("#")
        and (_touches_directly(ln) or any(_calls(ln, h) for h in helpers))
    ]


#: The verbs that must refuse on an insecure session directory.
SESSION_VERBS = {
    "cmd_sessions_list",
    "cmd_sessions_attach",
    "cmd_sessions_record",
    "cmd_sessions_lookup",
    "cmd_projects_delete",
    "cmd_projects_rebuild",
}


class TestRemoHostPinsPerVerb:
    def test_no_top_level_pin(self):
        _, top = _remo_host_functions()
        assert not [ln for ln in top if ln.strip() == PIN], (
            "a top-level pin makes every remo-host verb fail on an insecure "
            "session directory, capabilities and host stats included (#286)"
        )

    def test_xdg_block_and_path_load_still_precede_every_function(self):
        lines = (TEMPLATES / "remo-host.sh.j2").read_text().splitlines()
        xdg = next(i for i, ln in enumerate(lines) if "export XDG_RUNTIME_DIR=" in ln)
        load = next(i for i, ln in enumerate(lines) if ln.strip() == "remo_load_local_path")
        first_func = next(i for i, ln in enumerate(lines) if _FUNC_START.match(ln))
        assert xdg < load < first_func

    def test_every_verb_reaching_zellij_is_a_session_verb(self):
        funcs, _ = _remo_host_functions()
        touching_verbs = {f for f in _session_touchers(funcs) if f.startswith("cmd_")}
        # The scan must find the real ones, or it is checking nothing.
        assert {
            "cmd_sessions_list",
            "cmd_sessions_attach",
            "cmd_sessions_lookup",
            "cmd_projects_delete",
            "cmd_projects_rebuild",
        } <= touching_verbs
        assert touching_verbs <= SESSION_VERBS, sorted(touching_verbs - SESSION_VERBS)

    @pytest.mark.parametrize("verb", sorted(SESSION_VERBS))
    def test_pin_precedes_every_zellij_call_in_the_verb(self, verb):
        funcs, _ = _remo_host_functions()
        helpers = _session_touchers(funcs) - {verb}
        body = funcs[verb]
        pins = [i for i, ln in enumerate(body) if ln.strip() == PIN]
        assert len(pins) == 1, f"{verb} must pin exactly once"
        touches = _touch_lines(body, helpers)
        assert all(pins[0] < t for t in touches), (
            f"{verb} reaches zellij at body line {min(touches) + 1} before pinning"
        )

    def test_other_verbs_do_not_pin(self):
        funcs, _ = _remo_host_functions()
        others = {f for f in funcs if f.startswith("cmd_")} - SESSION_VERBS
        assert {"cmd_capabilities", "cmd_host_stats", "cmd_projects_list"} <= others
        pinning = {f for f in others if any(ln.strip() == PIN for ln in funcs[f])}
        assert not pinning, sorted(pinning)


def _remo_host(tmp_path: Path, mode: int | None) -> tuple[Path, Path]:
    """Rendered remo-host + its lib, one project, and (optionally) a session dir."""
    projects = tmp_path / "projects"
    (projects / "alpha").mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "remo-host"
    script.write_text(_render_remo_host(str(projects)))
    script.chmod(0o755)
    _install_lib(bindir)
    if mode is not None:
        d = _session_dir(tmp_path)
        d.mkdir(parents=True)
        d.chmod(mode)
    return script, projects


def _host(tmp_path: Path, script: Path, *args: str, system: str = "Darwin"):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    shims = _shim_uname(tmp_path, system)
    assert BASH is not None
    return subprocess.run(
        [BASH, str(script), *args],
        env={"PATH": f"{shims}:/usr/bin:/bin", "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestRemoHostOnAnInsecureSessionDir:
    def test_capabilities_still_answer(self, tmp_path):
        script, _ = _remo_host(tmp_path, 0o755)
        r = _host(tmp_path, script, "capabilities", "--json")
        assert r.returncode == 0, r.stderr
        assert '"protocol_version":' in r.stdout
        assert r.stderr == ""

    def test_projects_list_still_answers(self, tmp_path):
        script, _ = _remo_host(tmp_path, 0o755)
        r = _host(tmp_path, script, "projects", "list", "--json")
        assert r.returncode == 0, r.stderr
        assert '"alpha"' in r.stdout
        assert r.stderr == ""

    def test_sessions_list_is_refused_with_the_fix(self, tmp_path):
        script, _ = _remo_host(tmp_path, 0o755)
        r = _host(tmp_path, script, "sessions", "list", "--json")
        assert r.returncode == 1
        assert r.stdout == ""
        assert "refusing to use the session directory" in r.stderr
        assert f"chmod 700 '{_session_dir(tmp_path)}'" in r.stderr

    def test_sessions_lookup_is_refused(self, tmp_path):
        script, _ = _remo_host(tmp_path, 0o755)
        r = _host(tmp_path, script, "sessions", "lookup", "--key", "0" * 32, "--json")
        assert r.returncode == 1
        assert "refusing to use the session directory" in r.stderr

    def test_projects_delete_is_refused_before_removing_anything(self, tmp_path):
        script, projects = _remo_host(tmp_path, 0o755)
        r = _host(tmp_path, script, "projects", "delete", "--project", "alpha", "--json")
        assert r.returncode == 1
        assert "refusing to use the session directory" in r.stderr
        assert (projects / "alpha").is_dir()

    def test_usage_errors_keep_their_exit_code(self, tmp_path):
        # Argument validation runs before the pin: a malformed call is still 2.
        script, _ = _remo_host(tmp_path, 0o755)
        r = _host(tmp_path, script, "sessions", "attach")
        assert r.returncode == 2

    def test_secure_dir_is_exported_to_zellij(self, tmp_path):
        script, _ = _remo_host(tmp_path, None)
        shims = _shim_uname(tmp_path, "Darwin")
        seen = tmp_path / "seen"
        (shims / "zellij").write_text(f'#!/bin/sh\nprintf "%s" "$ZELLIJ_SOCKET_DIR" > "{seen}"\n')
        (shims / "zellij").chmod(0o755)
        r = _host(tmp_path, script, "sessions", "list", "--json")
        assert r.returncode == 0, r.stderr
        assert seen.read_text() == str(_session_dir(tmp_path))
        assert oct(_session_dir(tmp_path).stat().st_mode & 0o777) == "0o700"

    def test_linux_ignores_the_session_dir(self, tmp_path):
        script, _ = _remo_host(tmp_path, 0o755)
        r = _host(tmp_path, script, "sessions", "list", "--json", system="Linux")
        assert r.returncode == 0, r.stderr
        assert r.stderr == ""


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
