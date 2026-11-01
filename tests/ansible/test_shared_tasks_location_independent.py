"""remo's shared task files must resolve from any playbook directory.

``tasks/*.yml`` is included by remo's own playbooks AND, through
``remo_core_ansible_dir``, by provider plugins' playbooks that live in another
package (docs/provider-plugins.md#shipping-ansible-playbooks). Ansible resolves
a relative ``include_tasks: tasks/x.yml`` against the *playbook's* directory,
so a shared file that includes a sibling relatively works for core and breaks
for every plugin — at run time only, since ``--syntax-check`` does not follow
dynamic includes. That is exactly how the Hetzner plugin's first live
provision failed. Pin the location-independent form instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SHARED_TASKS = Path(__file__).resolve().parents[2] / "ansible" / "tasks"
CORE_DIR_EXPR = "{{ remo_core_ansible_dir | default(playbook_dir) }}/"
INCLUDE_KEYS = (
    "include_tasks",
    "import_tasks",
    "ansible.builtin.include_tasks",
    "ansible.builtin.import_tasks",
)


def _walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for nested in ("block", "rescue", "always"):
            yield from _walk(task.get(nested))


def _includes(path: Path) -> list[str]:
    found = []
    for task in _walk(yaml.safe_load(path.read_text())):
        for key in INCLUDE_KEYS:
            if key in task:
                value = task[key]
                found.append(value["file"] if isinstance(value, dict) else value)
    return found


SHARED_FILES = sorted(SHARED_TASKS.glob("*.yml"))


def test_there_are_shared_task_files() -> None:
    assert SHARED_FILES


@pytest.mark.parametrize("path", SHARED_FILES, ids=lambda p: p.name)
def test_every_include_is_rooted_at_remos_ansible_dir(path: Path) -> None:
    for target in _includes(path):
        assert target.startswith(CORE_DIR_EXPR), (
            f"{path.name} includes {target!r} relative to the playbook's "
            f"directory; spell it {CORE_DIR_EXPR}<path> so plugin playbooks "
            "resolve it too"
        )
        assert (SHARED_TASKS.parent / target[len(CORE_DIR_EXPR) :]).is_file(), target


def test_the_runtime_resolver_include_is_covered() -> None:
    """The include that broke must be one this guard actually inspects."""
    assert any(
        t.endswith("tasks/resolve_devcontainer_runtime.yml")
        for t in _includes(SHARED_TASKS / "configure_dev_tools.yml")
    )
