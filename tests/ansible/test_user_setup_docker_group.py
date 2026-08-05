"""`user_setup` must not require a docker group it did not create.

`--skip docker` (equivalently `configure_docker=false`) skips the `docker`
role, which is what creates the `docker` group. The membership task that
follows used to run unconditionally and died with "Group docker does not
exist" — *after* the play had already changed the host — so the documented
`--skip` flag was in practice unusable on every provider, not just on the new
`remo configure` path where it was found.

The gate is on the group's existence rather than on `configure_docker` so a
host where Docker arrived by other means (or on an earlier run) still gets the
membership.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_SETUP_TASKS = REPO_ROOT / "ansible" / "roles" / "user_setup" / "tasks" / "main.yml"

DOCKER_GROUP_TASK = "Add remo user to docker group"
PROBE_TASK = "Check whether a docker group exists"


def _tasks() -> list[dict[str, Any]]:
    data = yaml.safe_load(USER_SETUP_TASKS.read_text())
    assert isinstance(data, list)
    return data


def _by_name(name: str) -> dict[str, Any]:
    for task in _tasks():
        if task.get("name") == name:
            return task
    raise AssertionError(f"no task named {name!r} in {USER_SETUP_TASKS}")


class TestDockerGroupIsConditional:
    def test_membership_task_is_gated(self) -> None:
        task = _by_name(DOCKER_GROUP_TASK)
        assert "when" in task, (
            "an ungated `groups: docker` fails the whole play when the docker "
            "role was skipped, after the host has already been changed"
        )

    def test_gate_reads_the_probe_defensively(self) -> None:
        # Constitution Principle V: a registered variable is never accessed
        # without `| default()`. Here it matters for real — the probe is a
        # task like any other and can be skipped.
        when = _by_name(DOCKER_GROUP_TASK)["when"]
        clause = when if isinstance(when, str) else " ".join(str(c) for c in when)
        assert "user_setup_docker_group" in clause
        assert "| default(" in clause, "unguarded .rc access (Principle V)"

    def test_probe_precedes_the_task_that_uses_it(self) -> None:
        names = [t.get("name") for t in _tasks()]
        assert names.index(PROBE_TASK) < names.index(DOCKER_GROUP_TASK)

    def test_probe_never_reports_changed_or_fails(self) -> None:
        # A pure query: it must not fail the run when the group is absent —
        # that absence is the condition it exists to detect.
        probe = _by_name(PROBE_TASK)
        assert probe.get("changed_when") is False
        assert probe.get("failed_when") is False


@pytest.mark.skipif(shutil.which("getent") is None, reason="getent not available")
class TestProbeCommandBehavesAsAssumed:
    """The gate's correctness rests on `getent group` exit codes — verify them
    against the real binary rather than assuming."""

    def _rc(self, group: str) -> int:
        return subprocess.run(
            ["getent", "group", group], capture_output=True
        ).returncode

    def test_existing_group_exits_zero(self) -> None:
        assert self._rc("root") == 0

    def test_missing_group_exits_nonzero(self) -> None:
        assert self._rc("definitely-not-a-real-group-xyzzy") != 0
