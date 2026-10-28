"""The sshd AcceptEnv drop-in carries REMO_TAB_KEY (spec 028 FR-006/FR-020).

Structural, like the neighbouring idempotency tests: a live sshd restart is
not available in this sandbox, so assert the properties that make a second
configure a no-op — a ``copy`` (content-compared) feeding a restart gated on
``changed``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = REPO_ROOT / "ansible" / "tasks" / "configure_dev_tools.yml"
DROPIN = "/etc/ssh/sshd_config.d/accept-tz.conf"


def _tasks() -> list[dict[str, Any]]:
    return yaml.safe_load(TASKS.read_text())


def _dropin_task() -> dict[str, Any]:
    matches = [
        t for t in _tasks() if t.get("ansible.builtin.copy", {}).get("dest") == DROPIN
    ]
    assert len(matches) == 1, f"expected exactly one task writing {DROPIN}"
    return matches[0]


def test_accept_env_line_lists_tz_and_tab_key() -> None:
    content = _dropin_task()["ansible.builtin.copy"]["content"]
    accept = [ln for ln in content.splitlines() if ln.startswith("AcceptEnv")]
    assert len(accept) == 1
    assert accept[0].split()[1:] == ["TZ", "REMO_TAB_KEY"]


def test_dropin_is_a_copy_and_registers_change() -> None:
    task = _dropin_task()
    assert "ansible.builtin.copy" in task  # content-compared => idempotent
    assert task["register"] == "sshd_tz_config"
    assert "when" not in task


def test_restart_only_when_dropin_changed() -> None:
    restarts = [
        t
        for t in _tasks()
        if t.get("ansible.builtin.service", {}).get("state") == "restarted"
    ]
    assert len(restarts) == 1
    assert restarts[0]["when"] == "sshd_tz_config.changed | default(false)"
