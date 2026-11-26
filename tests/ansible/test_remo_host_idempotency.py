"""Idempotency checks for the remo-host install task (T010).

`ansible.builtin.template` is idempotent by construction: it compares
rendered content against what's on disk and only reports `changed` when the
content actually differs, so running the playbook twice (fresh host, or a
host that already has project-menu/project-launch installed) converges to
`changed=false` on the second run. Since a live `ansible-playbook` run isn't
available in this sandbox, the reliable, always-run core of this test is
structural: parse `ansible/roles/user_setup/tasks/host_scripts.yml` (the one task
file the remote role and the local setup play both include, spec 029) and assert the
"Install remo-host script" task exists, uses the `template` module (not
`copy`/`shell`/`command`, which would NOT give that idempotency guarantee),
is unconditional (no `when:` that could skip it on an already-provisioned
host), and mirrors the same dest/owner/group/mode shape as its
"Install project-launch script" sibling.

If `ansible-playbook`/`ansible` happens to be installed, an additional
syntax-check is attempted, but it's skipped (not failed) when unavailable or
when a full inventory/connection isn't set up for a live run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "ansible" / "roles" / "user_setup" / "tasks"
MAIN_YML = TASKS_DIR / "main.yml"
HOST_SCRIPTS_YML = TASKS_DIR / "host_scripts.yml"

TEMPLATE_MODULE = "ansible.builtin.template"
NON_IDEMPOTENT_MODULES = {
    "ansible.builtin.copy",
    "ansible.builtin.shell",
    "ansible.builtin.command",
    "copy",
    "shell",
    "command",
}


# Remote layout by default; the local play overrides remo_scripts_home.
SCRIPTS_BIN = "{{ remo_scripts_home | default('/home/' ~ remo_user) }}/.local/bin"


def _load_tasks() -> list[dict[str, Any]]:
    data = yaml.safe_load(HOST_SCRIPTS_YML.read_text())
    assert isinstance(data, list), "host_scripts.yml is expected to be a flat list of tasks"
    return data


def _find_task_by_name(tasks: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [t for t in tasks if t.get("name") == name]
    assert len(matches) == 1, f"expected exactly one task named {name!r}, found {len(matches)}"
    return matches[0]


def _module_key(task: dict[str, Any]) -> str:
    """Return the module invocation key of a task (e.g. 'ansible.builtin.template')."""
    reserved = {"name", "when", "register", "vars", "tags", "become", "loop", "with_items"}
    module_keys = [k for k in task if k not in reserved]
    assert len(module_keys) == 1, f"expected exactly one module key in task {task.get('name')!r}, found {module_keys}"
    return module_keys[0]


def test_main_yml_includes_host_scripts_and_no_longer_renders_them() -> None:
    """The remote role reaches the scripts only through the shared task file."""
    data = yaml.safe_load(MAIN_YML.read_text())
    includes = [t for t in data if "ansible.builtin.include_tasks" in t]
    assert [t["ansible.builtin.include_tasks"] for t in includes] == ["host_scripts.yml"]
    assert not [
        t for t in data if str(t.get("name", "")).startswith("Install remo-host script")
    ]


def test_every_host_script_task_omits_owner_for_a_local_install() -> None:
    """No sudo, no remo user locally: owner/group vanish when remo_local_install."""
    for task in _load_tasks():
        args = task[TEMPLATE_MODULE]
        for key in ("owner", "group"):
            assert "omit if (remo_local_install | default(false) | bool) else remo_user" in args[key]


def test_install_remo_host_task_exists() -> None:
    tasks = _load_tasks()
    task = _find_task_by_name(tasks, "Install remo-host script")
    assert task is not None


def test_install_remo_host_task_exactly_once() -> None:
    tasks = _load_tasks()
    matches = [t for t in tasks if t.get("name") == "Install remo-host script"]
    assert len(matches) == 1


def test_install_remo_host_uses_template_module() -> None:
    """`template` (not `copy`/`shell`/`command`) is what makes this idempotent."""
    tasks = _load_tasks()
    task = _find_task_by_name(tasks, "Install remo-host script")
    module_key = _module_key(task)
    assert module_key == TEMPLATE_MODULE
    assert module_key not in NON_IDEMPOTENT_MODULES


def test_install_remo_host_is_unconditional() -> None:
    """No `when:` gate that could skip installation on an already-provisioned host."""
    tasks = _load_tasks()
    task = _find_task_by_name(tasks, "Install remo-host script")
    assert "when" not in task


def test_install_remo_host_mirrors_project_launch_shape() -> None:
    """Structurally identical pattern to the sibling install task = idempotent
    by the same mechanism, on both a fresh host and one that already has
    project-menu/project-launch installed."""
    tasks = _load_tasks()

    remo_host_task = _find_task_by_name(tasks, "Install remo-host script")
    project_launch_task = _find_task_by_name(tasks, "Install project-launch script")

    remo_host_args = remo_host_task[TEMPLATE_MODULE]
    project_launch_args = project_launch_task[TEMPLATE_MODULE]

    assert remo_host_args["src"] == "remo-host.sh.j2"
    assert remo_host_args["dest"] == SCRIPTS_BIN + "/remo-host"

    # Same owner/group/mode shape as the sibling task.
    assert remo_host_args["owner"] == project_launch_args["owner"]
    assert remo_host_args["group"] == project_launch_args["group"]
    assert remo_host_args["mode"] == project_launch_args["mode"]
    assert remo_host_args["mode"] == "0755"

    # dest follows the same "<scripts home>/.local/bin/<name>" pattern.
    assert remo_host_args["dest"].startswith(SCRIPTS_BIN + "/")
    assert project_launch_args["dest"].startswith(SCRIPTS_BIN + "/")


def test_install_remo_host_task_ordered_after_project_launch() -> None:
    """Not load-bearing for idempotency, but keeps the sibling-task pattern
    (mirrored install tasks placed together) intact for readability."""
    tasks = _load_tasks()
    names = [t.get("name") for t in tasks]
    assert names.index("Install remo-host script") == names.index("Install project-launch script") + 1


# ---------------------------------------------------------------------------
# Optional live syntax-check, gated on ansible actually being installed.
# ---------------------------------------------------------------------------

ANSIBLE_PLAYBOOK = shutil.which("ansible-playbook")


@pytest.mark.skipif(ANSIBLE_PLAYBOOK is None, reason="ansible-playbook not available in this sandbox")
def test_user_setup_role_syntax_check() -> None:
    """Best-effort syntax-check of the role; skipped (not failed) if inventory
    or role dependencies aren't set up for a live run in this environment."""
    role_dir = REPO_ROOT / "ansible" / "roles" / "user_setup"
    entry_playbook = REPO_ROOT / "ansible" / "incus_configure.yml"
    if not role_dir.exists() or not entry_playbook.exists():
        pytest.skip("user_setup role or entry playbook not found")

    result = subprocess.run(
        [ANSIBLE_PLAYBOOK, "--syntax-check", str(entry_playbook)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0 and (
        "Unable to parse" in result.stderr or "No such file" in result.stderr
    ):
        pytest.skip(f"live syntax-check not runnable in this sandbox: {result.stderr.strip()}")
    assert result.returncode == 0, result.stderr
