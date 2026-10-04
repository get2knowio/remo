"""The devcontainer stop-on-exit cleanup must stop EVERY container it finds.

`roles/user_setup/tasks/main.yml`'s `.bashrc` block stops the project's
devcontainer when the user exits it. It used to do::

    _container_id=$(docker ps -q --filter "label=devcontainer.local_folder=$dir")
    docker stop "$_container_id" >/dev/null 2>&1 || true

`docker ps -q` prints one id per line, so with two matching containers that
passed a single newline-joined argument. docker rejects it, `|| true` hid the
failure, and the cleanup silently stopped *neither* — precisely when it mattered,
because duplicates are the case it exists to clean up. One container stopped
fine, which is why it went unnoticed.

A static "uses an array now" assertion would not have caught the original, so the
first test here executes the real snippet against a fake `docker` and checks what
`docker stop` actually received.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from jinja2 import Environment, TemplateSyntaxError

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_SETUP_TASKS = REPO_ROOT / "ansible" / "roles" / "user_setup" / "tasks" / "main.yml"
ANSIBLE_DIR = REPO_ROOT / "ansible"

BASH = shutil.which("bash")


def _stop_snippet() -> str:
    """The cleanup block, dedented, straight from the playbook.

    Anchored on the user-visible "Stopping devcontainer..." line rather than on
    any implementation detail, so a rewrite that is still broken is *found and
    executed* rather than silently skipped. Anchoring on `mapfile` made these
    tests fail for the wrong reason — extraction missing, not behaviour wrong —
    which is a false negative waiting to become a false positive.
    """
    text = USER_SETUP_TASKS.read_text(encoding="utf-8")
    match = re.search(
        r'^( *)echo "Stopping devcontainer\.\.\."\n.*?\n\1fi$', text, re.S | re.M
    )
    assert match, "the container-stop cleanup block was not found in main.yml"
    return textwrap.dedent(match.group(0))


@pytest.mark.skipif(BASH is None, reason="bash not available")
class TestEveryContainerIsStopped:
    def _run(self, tmp_path: Path, ids: list[str]) -> tuple[int, list[str]]:
        """Run the real snippet against a fake docker.

        Returns ``(argc, args)`` for the `docker stop` call, or ``(-1, [])`` if
        it was never called. **argc is the load-bearing value**: the original bug
        passed all ids as ONE newline-joined argument, and recording only the
        text cannot tell that apart from two separate arguments — the newline
        lands in the file either way. Counting `$#` can.
        """
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        argc_file = tmp_path / "stop-argc"
        args_file = tmp_path / "stop-args"

        fake = bin_dir / "docker"
        fake.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "ps" ]; then\n'
            + "".join(f'  echo "{i}"\n' for i in ids)
            + "  exit 0\n"
            "fi\n"
            'if [ "$1" = "stop" ]; then\n'
            "  shift\n"
            f'  printf "%s" "$#" >"{argc_file}"\n'
            f'  for a in "$@"; do printf "%s\\n" "$a" >>"{args_file}"; done\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)

        script = f"_project_dir=/home/dev/projects/demo\n{_stop_snippet()}\n"
        assert BASH is not None
        result = subprocess.run(
            [BASH, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )
        assert result.returncode == 0, result.stderr

        if not argc_file.exists():
            return -1, []
        argc = int(argc_file.read_text(encoding="utf-8").strip())
        args = (
            [ln for ln in args_file.read_text(encoding="utf-8").splitlines() if ln]
            if args_file.exists()
            else []
        )
        return argc, args

    def test_two_containers_are_both_stopped(self, tmp_path: Path) -> None:
        """The regression. Previously docker received ONE newline-joined
        argument, errored, and `|| true` swallowed it — neither was stopped."""
        argc, args = self._run(tmp_path, ["aaa111", "bbb222"])
        assert argc == 2, (
            f"docker stop received {argc} argument(s) for 2 containers — the ids "
            "are being passed as a single newline-joined string again"
        )
        assert args == ["aaa111", "bbb222"]

    def test_three_containers_are_all_stopped(self, tmp_path: Path) -> None:
        argc, args = self._run(tmp_path, ["a1", "b2", "c3"])
        assert argc == 3
        assert args == ["a1", "b2", "c3"]

    def test_one_container_still_works(self, tmp_path: Path) -> None:
        """The case that masked the bug: a single id worked fine before."""
        argc, args = self._run(tmp_path, ["solo99"])
        assert argc == 1
        assert args == ["solo99"]

    def test_no_containers_does_not_call_stop(self, tmp_path: Path) -> None:
        """`docker stop` with no arguments is a usage error, so an empty result
        must skip the call entirely rather than invoke it bare."""
        argc, args = self._run(tmp_path, [])
        assert argc == -1, "docker stop should not be called when nothing matched"
        assert args == []


class TestFailureIsReportedNotSwallowed:
    def test_stop_failure_prints_a_warning(self) -> None:
        """`|| true` is what let a broken stop look successful. The replacement
        stays non-fatal — a failed cleanup must not break the user's shell exit —
        but it says so."""
        snippet = _stop_snippet()
        assert "|| true" not in snippet, (
            "a bare `|| true` hides exactly the failure this block got wrong"
        )
        assert "Warning: could not stop container" in snippet


class TestJinjaCommentTrap:
    """Ansible templates these files through Jinja2, which reads a brace-hash
    sequence as a comment opener and swallows everything after it. The bash
    array-length idiom is spelled with those two characters, so using it inside a
    playbook silently truncates the value.

    This bit during the very commit that fixed the stop bug: a comment *warning*
    about the trap contained it, and Jinja2 then failed to parse the file.
    """

    def test_user_setup_tasks_parse_as_jinja(self) -> None:
        try:
            Environment().parse(USER_SETUP_TASKS.read_text(encoding="utf-8"))
        except TemplateSyntaxError as exc:  # pragma: no cover - failure path
            pytest.fail(
                f"{USER_SETUP_TASKS.relative_to(REPO_ROOT)} is not Jinja2-parseable "
                f"({exc.message} at line {exc.lineno}). A bash array-length "
                "expression is the usual cause — use star expansion instead."
            )

    @pytest.mark.parametrize(
        "path",
        sorted(
            p
            for p in ANSIBLE_DIR.rglob("*.yml")
            if "molecule" not in p.parts
        ),
        ids=lambda p: str(p.relative_to(REPO_ROOT)),
    )
    def test_no_playbook_contains_the_trap(self, path: Path) -> None:
        """Across every playbook and role file, not just the one changed here."""
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{n}"
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if "${#" in line
        ]
        assert not offenders, (
            "bash array-length expressions break Ansible's Jinja2 templating: "
            + ", ".join(offenders)
        )
