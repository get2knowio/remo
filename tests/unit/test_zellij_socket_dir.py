"""Every host entry point must resolve the SAME zellij session namespace.

zellij keeps its session sockets in ``$XDG_RUNTIME_DIR/zellij``, falling back to
``/tmp/zellij-$UID`` when that variable is unset. ssh sets it only for a *login
session* — ``ssh host``, a shell channel — and **not** for ``ssh host
<command>``, which is an exec channel.

`remo shell` (no project) sends no command, so it gets a login session.
`remo shell -p NAME` sends ``~/.local/bin/project-launch --project NAME``
(core/ssh.py's ``build_project_launch_remote_cmd``), as do the web console and
the SSM connector via ``remo-host sessions attach``. So those paths ran with the
variable unset and looked in a different directory:

- ``remo shell`` reattached to the live session in ``/run/user/$UID/zellij``
- ``remo shell -p NAME`` found nothing in ``/tmp/zellij-$UID``, created a
  duplicate session there, and landed the user in a NEW shell — the container
  was correctly reused, so it looked like "a new devcontainer project"
- ``remo-host sessions list`` reported no active sessions to the web console

Observed on a real host: six live sessions under ``/run/user/1000/zellij`` while
the exec-channel context listed only two stale EXITED ones in
``/tmp/zellij-1000`` — including a duplicate of a project that was live in the
other namespace.

Adding ``-t`` or ``-l`` to the ssh command does **not** fix it: that yields a
login *shell* but still an exec *session*, so pam_systemd never runs. The
variable has to be normalised on the host side, which is what these tests pin.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "ansible" / "roles" / "user_setup" / "templates"

#: Every host script that invokes zellij. A new one must normalise too, or it
#: reintroduces a split namespace for whichever surface uses it.
ZELLIJ_SCRIPTS = [
    "project-launch.sh.j2",
    "project-menu.sh.j2",
    "remo-host.sh.j2",
    "devshell.sh.j2",
]

BASH = shutil.which("bash")


def _source(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _normalisation_snippet(name: str) -> str:
    """The `if … fi` block that pins XDG_RUNTIME_DIR, straight from the script."""
    text = _source(name)
    match = re.search(
        r'^if \[ -z "\$\{XDG_RUNTIME_DIR:-\}" \].*?\n^fi$', text, re.S | re.M
    )
    assert match, f"{name} has no XDG_RUNTIME_DIR normalisation block"
    return match.group(0)


class TestEveryZellijScriptNormalises:
    @pytest.mark.parametrize("name", ZELLIJ_SCRIPTS)
    def test_script_normalises_xdg_runtime_dir(self, name: str) -> None:
        assert "export XDG_RUNTIME_DIR=" in _source(name), (
            f"{name} invokes zellij but does not pin the socket directory, so it "
            "will see a different set of sessions than the other entry points"
        )

    @pytest.mark.parametrize("name", ZELLIJ_SCRIPTS)
    def test_normalisation_precedes_every_zellij_call(self, name: str) -> None:
        """Setting it after the first call would leave that call on the wrong
        namespace — the bug, narrowed rather than fixed."""
        lines = _source(name).splitlines()
        export_at = next(
            i for i, ln in enumerate(lines) if "export XDG_RUNTIME_DIR=" in ln
        )
        calls = [
            i
            for i, ln in enumerate(lines)
            if "zellij" in ln and not ln.lstrip().startswith("#")
        ]
        assert calls, f"{name} is listed as invoking zellij but never does"
        assert export_at < min(calls), (
            f"{name} invokes zellij at line {min(calls) + 1} before normalising "
            f"at line {export_at + 1}"
        )

    def test_the_list_covers_every_script_that_uses_zellij(self) -> None:
        """So a newly added host script cannot quietly skip this."""
        users = {
            p.name
            for p in sorted(TEMPLATES.glob("*.j2"))
            if any(
                "zellij" in ln and not ln.lstrip().startswith("#")
                for ln in p.read_text(encoding="utf-8").splitlines()
            )
        }
        assert users == set(ZELLIJ_SCRIPTS), (
            "scripts invoking zellij changed; update ZELLIJ_SCRIPTS and make sure "
            f"each normalises XDG_RUNTIME_DIR. Found: {sorted(users)}"
        )


@pytest.mark.skipif(BASH is None, reason="bash not available")
class TestNormalisationBehaviour:
    """Executes the real snippet, so the three branches are checked rather than
    assumed."""

    def _run(self, name: str, *, env: dict[str, str], runtime_dir: Path | None) -> str:
        """Return XDG_RUNTIME_DIR after the snippet runs.

        `id -u` is stubbed so the test can point /run/user/<uid> at a temp path
        instead of depending on the runner's own uid directory.
        """
        snippet = _normalisation_snippet(name)
        if runtime_dir is not None:
            # Rewrite only the two /run/user references to the fixture path,
            # leaving the logic itself untouched.
            snippet = snippet.replace('/run/user/$(id -u)', str(runtime_dir))
        script = f'{snippet}\nprintf "%s" "${{XDG_RUNTIME_DIR:-}}"\n'
        assert BASH is not None
        result = subprocess.run(
            [BASH, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": os.environ["PATH"], **env},
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    @pytest.mark.parametrize("name", ZELLIJ_SCRIPTS)
    def test_unset_and_dir_exists_gets_pinned(
        self, name: str, tmp_path: Path
    ) -> None:
        """The exec-channel case: this is the fix."""
        runtime = tmp_path / "run-user"
        runtime.mkdir()
        assert self._run(name, env={}, runtime_dir=runtime) == str(runtime)

    @pytest.mark.parametrize("name", ZELLIJ_SCRIPTS)
    def test_already_set_is_left_alone(self, name: str, tmp_path: Path) -> None:
        """The login-session case must be untouched — it already works, and
        overriding it would move those sessions out from under the user."""
        runtime = tmp_path / "run-user"
        runtime.mkdir()
        got = self._run(
            name, env={"XDG_RUNTIME_DIR": "/somewhere/else"}, runtime_dir=runtime
        )
        assert got == "/somewhere/else"

    @pytest.mark.parametrize("name", ZELLIJ_SCRIPTS)
    def test_missing_dir_keeps_the_tmp_fallback(
        self, name: str, tmp_path: Path
    ) -> None:
        """A host without systemd-logind has no /run/user/<uid>. Pointing at a
        path nothing will create would break it differently; leaving the variable
        unset keeps zellij's own /tmp/zellij-$UID fallback."""
        missing = tmp_path / "does-not-exist"
        assert self._run(name, env={}, runtime_dir=missing) == ""

    @pytest.mark.parametrize("name", ZELLIJ_SCRIPTS)
    def test_empty_string_is_treated_as_unset(
        self, name: str, tmp_path: Path
    ) -> None:
        """What the real host showed: the variable present but empty, not
        absent. A plain `-z` test covers both, but pin it — a switch to
        `[ -v XDG_RUNTIME_DIR ]` would silently stop fixing the observed case."""
        runtime = tmp_path / "run-user"
        runtime.mkdir()
        got = self._run(name, env={"XDG_RUNTIME_DIR": ""}, runtime_dir=runtime)
        assert got == str(runtime)
