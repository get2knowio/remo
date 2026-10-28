"""A zellij upgrade must not silently hide live sessions.

zellij namespaces session sockets by version, so replacing the binary under a
running session leaves it alive but invisible: the next ``zellij attach
--create NAME`` starts a fresh session and the user's work appears to vanish.
``ansible/roles/zellij/tasks/live_session_guard.yml`` defers the upgrade when
the installed version has live sessions and says so via a task NAME.

The behavioural tests run the real task file through ``ansible-playbook``
against a fake ``zellij`` on PATH, then feed the output through the CLI's own
``_filter_line`` — a warning that only exists in a suppressed ``debug`` body
would pass a structural check while never reaching the user.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from remo_cli.core.ansible_runner import _filter_line

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_TASKS = REPO_ROOT / "ansible" / "roles" / "zellij" / "tasks"
GUARD = ROLE_TASKS / "live_session_guard.yml"
MAIN = ROLE_TASKS / "main.yml"

ANSIBLE_PLAYBOOK = shutil.which("ansible-playbook")
RUN_USER_DIR = Path(f"/run/user/{os.getuid()}")

pytestmark = pytest.mark.skipif(ANSIBLE_PLAYBOOK is None, reason="ansible-playbook not available")


def _fake_zellij(bin_dir: Path, *, runtime_dir_out: str, tmp_out: str, crash: bool = False) -> None:
    """A zellij whose `list-sessions` answer depends on the namespace asked.

    With XDG_RUNTIME_DIR set it prints ``runtime_dir_out``; unset, ``tmp_out``
    — mirroring zellij's real /run/user vs /tmp/zellij-$UID split.
    """
    (bin_dir / "runtime.out").write_text(runtime_dir_out, encoding="utf-8")
    (bin_dir / "tmp.out").write_text(tmp_out, encoding="utf-8")
    fake = bin_dir / "zellij"
    fake.write_text(
        "#!/bin/sh\n"
        + ("echo 'Segmentation fault' >&2; exit 139\n" if crash else "")
        + '[ "$1" = "list-sessions" ] || exit 0\n'
        'if [ -n "${XDG_RUNTIME_DIR:-}" ]; then\n'
        f'  cat "{bin_dir}/runtime.out"\n'
        "else\n"
        f'  cat "{bin_dir}/tmp.out"\n'
        "fi\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def _run_guard(
    tmp_path: Path, *, runtime_dir_out: str = "", tmp_out: str = "", crash: bool = False
) -> tuple[dict[str, Any], list[str]]:
    """Run the guard; return (facts it set, lines the CLI would display)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_zellij(bin_dir, runtime_dir_out=runtime_dir_out, tmp_out=tmp_out, crash=crash)
    facts_file = tmp_path / "facts.json"

    playbook = tmp_path / "play.yml"
    playbook.write_text(
        textwrap.dedent(
            f"""\
            - hosts: localhost
              connection: local
              gather_facts: false
              vars:
                remo_user: "{{{{ lookup('env', 'USER') | default('root', true) }}}}"
                zellij_installed_version: "0.44.3"
                zellij_target_version: "0.45.1"
              tasks:
                - ansible.builtin.include_tasks: {GUARD}
                - ansible.builtin.copy:
                    dest: {facts_file}
                    content: "{{{{ {{'deferred': zellij_upgrade_deferred, 'sessions': zellij_live_sessions}} | to_json }}}}"
            """
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_LOCALHOST_WARNING": "0",
        "ANSIBLE_INVENTORY_UNPARSED_WARNING": "0",
        "USER": os.environ.get("USER") or subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip(),
    }
    env.pop("ANSIBLE_CONFIG", None)
    assert ANSIBLE_PLAYBOOK is not None
    # ansible_become=false: the guard's become/become_user is for a real host
    # (run as the workspace user); a connection variable outranks the keyword,
    # so the test runs it as whoever runs pytest, with no sudo.
    result = subprocess.run(
        [ANSIBLE_PLAYBOOK, "-i", "localhost,", str(playbook), "-e", "ansible_become=false"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=tmp_path,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    pending = [""]
    shown = [out for line in result.stdout.splitlines() if (out := _filter_line(line, pending)) is not None]
    return json.loads(facts_file.read_text(encoding="utf-8")), shown


def _warning_lines(shown: list[str]) -> list[str]:
    return [line for line in shown if "DEFERRED" in line]


class TestDeferral:
    def test_live_sessions_defer_and_warn_visibly(self, tmp_path: Path) -> None:
        facts, shown = _run_guard(
            tmp_path,
            tmp_out=(
                "maverick [Created 2h ago] \n"
                "hangar [Created 3days ago] (current)\n"
            ),
        )
        assert facts["deferred"] is True
        assert sorted(facts["sessions"]) == ["hangar", "maverick"]

        warnings = _warning_lines(shown)
        assert len(warnings) == 1, f"the warning must survive the CLI filter; shown={shown}"
        assert "0.44.3" in warnings[0] and "0.45.1" in warnings[0]
        assert "hangar" in warnings[0] and "maverick" in warnings[0]
        assert "--only zellij" in warnings[0]

    def test_exited_sessions_do_not_defer(self, tmp_path: Path) -> None:
        """Nothing runs in an EXITED session, so there is nothing to hide."""
        facts, shown = _run_guard(
            tmp_path,
            tmp_out="hangar [Created 14days ago] (EXITED - attach to resurrect)\n",
        )
        assert facts["deferred"] is False
        assert facts["sessions"] == []
        assert _warning_lines(shown) == []

    def test_no_sessions_does_not_defer(self, tmp_path: Path) -> None:
        """Real zellij prints a prose line when empty; it must not parse as a session."""
        facts, shown = _run_guard(tmp_path, tmp_out="No active zellij sessions found.\n")
        assert facts["deferred"] is False
        assert _warning_lines(shown) == []

    def test_unrunnable_probe_does_not_block_the_upgrade(self, tmp_path: Path) -> None:
        """A broken zellij must stay upgradable: probe failure means proceed."""
        facts, shown = _run_guard(tmp_path, crash=True)
        assert facts["deferred"] is False
        assert _warning_lines(shown) == []

    @pytest.mark.skipif(not RUN_USER_DIR.is_dir(), reason=f"{RUN_USER_DIR} does not exist on this machine")
    def test_login_session_namespace_is_probed_too(self, tmp_path: Path) -> None:
        """Sessions started from a login shell live under /run/user/$UID."""
        facts, _ = _run_guard(tmp_path, runtime_dir_out="apps [Created 1h ago] \n")
        assert facts["deferred"] is True
        assert facts["sessions"] == ["apps"]


class TestWiring:
    """The guard must gate BOTH install paths, and never a fresh install."""

    @staticmethod
    def _flatten(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in tasks:
            out.append(t)
            out.extend(TestWiring._flatten(t.get("block", [])))
        return out

    def _tasks(self) -> dict[str, dict[str, Any]]:
        return {t["name"]: t for t in self._flatten(yaml.safe_load(MAIN.read_text(encoding="utf-8"))) if "name" in t}

    @pytest.mark.parametrize(
        ("guard", "install"),
        [
            ("Check for live sessions before an apt upgrade", "Install zellij from apt if version is adequate"),
            ("Check for live sessions before a binary upgrade", "Download zellij binary"),
        ],
    )
    def test_install_path_is_gated(self, guard: str, install: str) -> None:
        tasks = self._tasks()
        assert tasks[guard]["ansible.builtin.include_tasks"] == "live_session_guard.yml"
        assert "zellij_installed_version != '0.0.0'" in tasks[guard]["when"], "a fresh install has nothing to hide"
        assert any("zellij_upgrade_deferred" in cond for cond in tasks[install]["when"])
