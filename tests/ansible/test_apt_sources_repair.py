"""Coverage for the apt-sources repair pre-task (#120).

A container provisioned before #110 carries two `deb` lines for the NodeSource
repo — one naming the keyring that #110 deleted. apt refuses to read *any*
source list while that conflict exists, so `Update apt cache` in every
configure/site playbook's `pre_tasks` dies before the `nodejs` role that would
repair itself could ever run, permanently wedging `remo <provider> upgrade`.

Two things have to hold, and both are tested here:

1. **Ordering** — the repair is included in `pre_tasks` *ahead of* `Update apt
   cache` in all eight configure/site playbooks. A repair that runs after the
   thing it unwedges is no repair at all.
2. **Behavior** — the repair shell drops exactly the lines whose `signed-by=`
   keyring is absent and leaves everything else byte-identical, reporting
   `changed` only when it actually removed something (Principle VII).

The behavioral half runs the real script body extracted from the task file,
rendered against a temp directory via the `apt_sources_repair_dir` override, so
it exercises the shipped code rather than a paraphrase of it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from jinja2 import Template

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSIBLE_DIR = REPO_ROOT / "ansible"
REPAIR_TASKS = ANSIBLE_DIR / "tasks" / "repair_apt_sources.yml"

#: Every playbook whose `pre_tasks` touch apt on the managed host.
PLAYBOOKS = [
    "proxmox_configure.yml",
    "incus_configure.yml",
    "hetzner_configure.yml",
    "aws_configure.yml",
    "proxmox_site.yml",
    "incus_site.yml",
    "hetzner_site.yml",
    "aws_site.yml",
]

REPAIR_INCLUDE = "tasks/repair_apt_sources.yml"
MISSING_KEY_LINE = (
    "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] "
    "https://deb.nodesource.com/node_24.x nodistro main"
)
PRESENT_KEY_LINE = (
    "deb [signed-by=/etc/apt/keyrings/nodesource.asc] "
    "https://deb.nodesource.com/node_24.x nodistro main"
)


def _repair_tasks() -> list[dict[str, Any]]:
    data = yaml.safe_load(REPAIR_TASKS.read_text())
    assert isinstance(data, list), "repair_apt_sources.yml is expected to be a list of tasks"
    return data


def _repair_script(sources_dir: Path) -> str:
    """The shipped shell body, rendered for *sources_dir*."""
    task = _repair_tasks()[0]
    return Template(task["ansible.builtin.shell"]).render(
        apt_sources_repair_dir=str(sources_dir)
    )


def _run_repair(sources_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", _repair_script(sources_dir)],
        capture_output=True,
        text=True,
        check=False,
    )


def _reported_changed(result: subprocess.CompletedProcess[str]) -> bool:
    """Mirror the task's `changed_when` expression."""
    return "remo_apt_sources_repaired=1" in result.stdout


class TestPreTaskOrdering:
    """The repair must precede the apt call it exists to unwedge."""

    @pytest.mark.parametrize("playbook", PLAYBOOKS)
    def test_repair_runs_before_update_apt_cache(self, playbook: str) -> None:
        plays = yaml.safe_load((ANSIBLE_DIR / playbook).read_text())
        found = False
        for play in plays:
            pre_tasks = play.get("pre_tasks") or []
            names = [t.get("ansible.builtin.include_tasks") for t in pre_tasks]
            titles = [t.get("name") for t in pre_tasks]
            if REPAIR_INCLUDE not in names:
                continue
            found = True
            assert "Update apt cache" in titles, (
                f"{playbook}: repair included in a play with no apt cache update"
            )
            assert names.index(REPAIR_INCLUDE) < titles.index("Update apt cache"), (
                f"{playbook}: apt sources repair must come BEFORE 'Update apt cache' — "
                "after it, the wedged host never reaches the repair"
            )
        assert found, f"{playbook}: no pre_task includes {REPAIR_INCLUDE}"

    @pytest.mark.parametrize("playbook", PLAYBOOKS)
    def test_repair_play_is_privileged(self, playbook: str) -> None:
        """Rewriting /etc/apt/sources.list.d requires root."""
        plays = yaml.safe_load((ANSIBLE_DIR / playbook).read_text())
        for play in plays:
            pre_tasks = play.get("pre_tasks") or []
            if any(t.get("ansible.builtin.include_tasks") == REPAIR_INCLUDE for t in pre_tasks):
                assert play.get("become") is True, f"{playbook}: repair play is not become: true"


class TestRepairTaskShape:
    def test_changed_when_is_defensive(self) -> None:
        """Principle V: the registered var is read through `| default()`."""
        task = _repair_tasks()[0]
        assert "| default('')" in task["changed_when"]

    def test_report_task_guards_on_changed(self) -> None:
        report = _repair_tasks()[1]
        assert "apt_sources_repair.changed | default(false)" in report["when"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
class TestRepairBehavior:
    """Both branches of the repair (Principle VI)."""

    def _sources(self, tmp_path: Path, name: str, body: str) -> Path:
        d = tmp_path / "sources.list.d"
        d.mkdir(exist_ok=True)
        f = d / name
        f.write_text(body)
        return f

    def test_removes_line_whose_keyring_is_missing(self, tmp_path: Path) -> None:
        f = self._sources(
            tmp_path,
            "nodesource.list",
            f"{MISSING_KEY_LINE}\n{PRESENT_KEY_LINE}\n",
        )
        keyring = tmp_path / "keyrings"
        keyring.mkdir()
        # Only the .asc keyring survives, exactly as #110 leaves the box.
        (keyring / "nodesource.asc").write_text("key")
        body = f.read_text().replace("/etc/apt/keyrings", str(keyring))
        f.write_text(body)

        result = _run_repair(tmp_path / "sources.list.d")

        assert result.returncode == 0, result.stderr
        assert _reported_changed(result)
        remaining = f.read_text().splitlines()
        assert remaining == [PRESENT_KEY_LINE.replace("/etc/apt/keyrings", str(keyring))]

    def test_already_clean_file_is_untouched_and_not_changed(self, tmp_path: Path) -> None:
        keyring = tmp_path / "keyrings"
        keyring.mkdir()
        (keyring / "nodesource.asc").write_text("key")
        body = PRESENT_KEY_LINE.replace("/etc/apt/keyrings", str(keyring)) + "\n"
        f = self._sources(tmp_path, "nodesource.list", body)

        result = _run_repair(tmp_path / "sources.list.d")

        assert result.returncode == 0, result.stderr
        assert not _reported_changed(result)
        assert f.read_text() == body

    def test_second_run_is_a_no_op(self, tmp_path: Path) -> None:
        """Principle VII: repair converges after one pass."""
        keyring = tmp_path / "keyrings"
        keyring.mkdir()
        (keyring / "nodesource.asc").write_text("key")
        f = self._sources(
            tmp_path,
            "nodesource.list",
            f"{MISSING_KEY_LINE}\n"
            f"{PRESENT_KEY_LINE.replace('/etc/apt/keyrings', str(keyring))}\n",
        )

        first = _run_repair(tmp_path / "sources.list.d")
        after_first = f.read_text()
        second = _run_repair(tmp_path / "sources.list.d")

        assert _reported_changed(first)
        assert not _reported_changed(second)
        assert f.read_text() == after_first

    def test_comments_and_unrelated_lines_survive(self, tmp_path: Path) -> None:
        keyring = tmp_path / "keyrings"
        keyring.mkdir()
        (keyring / "docker.asc").write_text("key")
        body = (
            "# Docker repo, managed by remo\n"
            "\n"
            f"deb [arch=amd64 signed-by={keyring}/docker.asc] "
            "https://download.docker.com/linux/ubuntu noble stable\n"
        )
        f = self._sources(tmp_path, "docker.list", body)

        result = _run_repair(tmp_path / "sources.list.d")

        assert not _reported_changed(result)
        assert f.read_text() == body

    def test_line_without_signed_by_is_never_touched(self, tmp_path: Path) -> None:
        """A repo trusting the system keyring has no `signed-by=` to validate."""
        body = "deb http://archive.ubuntu.com/ubuntu noble main\n"
        f = self._sources(tmp_path, "extra.list", body)

        result = _run_repair(tmp_path / "sources.list.d")

        assert not _reported_changed(result)
        assert f.read_text() == body

    def test_empty_sources_dir_succeeds(self, tmp_path: Path) -> None:
        (tmp_path / "sources.list.d").mkdir()

        result = _run_repair(tmp_path / "sources.list.d")

        assert result.returncode == 0, result.stderr
        assert not _reported_changed(result)

    def test_missing_sources_dir_succeeds(self, tmp_path: Path) -> None:
        """A host with no sources.list.d at all must not fail the play."""
        result = _run_repair(tmp_path / "nope")

        assert result.returncode == 0, result.stderr
        assert not _reported_changed(result)

    def test_no_temp_file_is_left_behind(self, tmp_path: Path) -> None:
        keyring = tmp_path / "keyrings"
        keyring.mkdir()
        (keyring / "nodesource.asc").write_text("key")
        self._sources(
            tmp_path,
            "nodesource.list",
            f"{MISSING_KEY_LINE}\n"
            f"{PRESENT_KEY_LINE.replace('/etc/apt/keyrings', str(keyring))}\n",
        )

        _run_repair(tmp_path / "sources.list.d")

        leftovers = list((tmp_path / "sources.list.d").glob("*.remo-repair"))
        assert leftovers == []
