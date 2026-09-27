"""Structural tests for the `ssm_connector` role and its two playbooks
(specs/025-ssm-connector, contracts/ansible-role.md). Same style as
`tests/ansible/test_remo_host_idempotency.py`: PyYAML parsing plus an
optional, gated live syntax-check — no live host is contacted here.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSIBLE_DIR = REPO_ROOT / "ansible"
ROLE_DIR = ANSIBLE_DIR / "roles" / "ssm_connector"
TASKS_FILE = ROLE_DIR / "tasks" / "main.yml"
DEFAULTS_FILE = ROLE_DIR / "defaults" / "main.yml"
HANDLERS_FILE = ROLE_DIR / "handlers" / "main.yml"
ENROLL_PLAYBOOK = ANSIBLE_DIR / "ssm_connector_enroll.yml"
UNENROLL_PLAYBOOK = ANSIBLE_DIR / "ssm_connector_unenroll.yml"


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, list)
    return data


def _load_playbook(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, list)
    return data


def _module_key(task: dict[str, Any]) -> str | None:
    reserved = {
        "name", "when", "register", "vars", "tags", "become", "loop",
        "loop_control", "with_items", "notify", "failed_when", "changed_when",
        "no_log", "environment", "args",
    }
    module_keys = [k for k in task if k not in reserved]
    if len(module_keys) != 1:
        return None
    return module_keys[0]


# ---------------------------------------------------------------------------
# Role tasks: structural gates
# ---------------------------------------------------------------------------


def test_role_tasks_file_is_flat_list() -> None:
    tasks = _load_tasks(TASKS_FILE)
    assert len(tasks) > 10


def test_every_task_referencing_activation_code_has_no_log() -> None:
    tasks = _load_tasks(TASKS_FILE)
    for task in tasks:
        text = yaml.dump(task)
        if "ssm_activation_code" in text:
            assert task.get("no_log") is True, (
                f"task {task.get('name')!r} references ssm_activation_code without "
                "no_log: true"
            )


def test_register_task_uses_command_with_creates_and_guarded_when() -> None:
    tasks = _load_tasks(TASKS_FILE)
    register_tasks = [
        t for t in tasks if isinstance(t.get("name"), str) and t["name"].startswith("Register the connector")
    ]
    assert len(register_tasks) == 1
    task = register_tasks[0]
    assert _module_key(task) == "ansible.builtin.command"
    assert "creates" in task["ansible.builtin.command"]
    assert "when" in task
    assert "default(false)" in task["when"]
    assert task.get("no_log") is True


def test_ssm_agent_install_uses_apt_deb() -> None:
    tasks = _load_tasks(TASKS_FILE)
    install_tasks = [
        t for t in tasks if isinstance(t.get("name"), str) and "Install SSM Agent" in t["name"]
    ]
    assert len(install_tasks) == 1
    task = install_tasks[0]
    assert _module_key(task) == "ansible.builtin.apt"
    assert "deb" in task["ansible.builtin.apt"]


def test_run_as_user_task_has_no_sudoers_side_effect() -> None:
    tasks = _load_tasks(TASKS_FILE)
    user_tasks = [
        t for t in tasks if isinstance(t.get("name"), str) and "run-as user" in t["name"]
    ]
    assert len(user_tasks) == 1
    task = user_tasks[0]
    assert _module_key(task) == "ansible.builtin.user"
    full_text = yaml.dump(task)
    assert "sudoers" not in full_text
    assert "NOPASSWD" not in full_text


def test_identity_uses_openssh_keypair_module() -> None:
    tasks = _load_tasks(TASKS_FILE)
    keypair_tasks = [t for t in tasks if _module_key(t) == "community.crypto.openssh_keypair"]
    assert len(keypair_tasks) == 1
    assert keypair_tasks[0]["community.crypto.openssh_keypair"]["type"] == "ed25519"


def test_registry_and_exposure_writes_use_copy_content() -> None:
    tasks = _load_tasks(TASKS_FILE)
    write_tasks = [
        t
        for t in tasks
        if isinstance(t.get("name"), str)
        and t["name"] in {"Write the connector's private registry.json", "Write the connector's exposure.json"}
    ]
    assert len(write_tasks) == 2
    for task in write_tasks:
        assert _module_key(task) == "ansible.builtin.copy"
        assert "content" in task["ansible.builtin.copy"]
        assert task["ansible.builtin.copy"].get("mode") == "0600"


def test_state_dir_and_ssh_dir_are_0700() -> None:
    tasks = _load_tasks(TASKS_FILE)
    dir_tasks = [
        t
        for t in tasks
        if _module_key(t) == "ansible.builtin.file"
        and t.get("ansible.builtin.file", {}).get("state") == "directory"
        and "connector_state_dir" in str(t.get("ansible.builtin.file", {}).get("path", ""))
    ]
    assert len(dir_tasks) >= 2
    for task in dir_tasks:
        assert task["ansible.builtin.file"].get("mode") == "0700"


def test_no_registered_variable_used_without_default_filter() -> None:
    """Every `.rc`/`.stdout`/`.stat.exists` access on a registered var in a
    `when:`/`that:`/Jinja template MUST go through `| default(`."""
    tasks = _load_tasks(TASKS_FILE)
    registered_names = {t["register"] for t in tasks if "register" in t}
    unsafe_pattern = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in registered_names) + r")\.(rc|stdout|stat\.exists)\b(?!\s*\|\s*default)"
    )
    text = TASKS_FILE.read_text()
    for match in unsafe_pattern.finditer(text):
        # Find the containing line to give a useful assertion message.
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        line = text[line_start:line_end]
        assert "default(" in line, f"unsafe registered-variable access: {line.strip()!r}"


def test_apt_install_task_does_not_use_shell_or_command() -> None:
    tasks = _load_tasks(TASKS_FILE)
    base_packages_task = next(
        t for t in tasks if isinstance(t.get("name"), str) and "Install base packages" in t["name"]
    )
    assert _module_key(base_packages_task) == "ansible.builtin.apt"


# ---------------------------------------------------------------------------
# defaults/main.yml and handlers/main.yml
# ---------------------------------------------------------------------------


def test_defaults_declare_the_contract_variables() -> None:
    defaults = yaml.safe_load(DEFAULTS_FILE.read_text())
    for key in (
        "connector_run_as_user",
        "connector_state_dir",
        "connector_install_dir",
        "remo_version",
        "remo_source",
        "ssm_region",
        "ssm_agent_deb_url",
        "connector_exposures",
        "connector_registry_json",
        "connector_exposed_hosts",
    ):
        assert key in defaults, f"defaults/main.yml is missing {key!r}"
    assert defaults["connector_run_as_user"] == "remo-connector"
    assert defaults["connector_state_dir"] == "/var/lib/remo-connector"
    assert defaults["connector_install_dir"] == "/opt/remo-connector"


def test_handler_restarts_amazon_ssm_agent() -> None:
    handlers = yaml.safe_load(HANDLERS_FILE.read_text())
    assert len(handlers) == 1
    assert handlers[0]["name"] == "Restart amazon-ssm-agent"
    assert _module_key(handlers[0]) == "ansible.builtin.systemd"


def test_agent_install_task_notifies_the_restart_handler() -> None:
    tasks = _load_tasks(TASKS_FILE)
    install_task = next(
        t for t in tasks if isinstance(t.get("name"), str) and "Install SSM Agent" in t["name"]
    )
    assert install_task.get("notify") == "Restart amazon-ssm-agent"


# ---------------------------------------------------------------------------
# Playbooks
# ---------------------------------------------------------------------------


def test_enroll_playbook_has_three_plays() -> None:
    plays = _load_playbook(ENROLL_PLAYBOOK)
    assert len(plays) == 3


def test_enroll_playbook_play3_is_become_false() -> None:
    plays = _load_playbook(ENROLL_PLAYBOOK)
    play3 = plays[2]
    assert play3.get("become") is False


def test_enroll_playbook_activation_code_assert_has_no_log() -> None:
    plays = _load_playbook(ENROLL_PLAYBOOK)
    play1_tasks = plays[0]["tasks"]
    code_asserts = [
        t
        for t in play1_tasks
        if _module_key(t) == "ansible.builtin.assert"
        and "ssm_activation_code" in yaml.dump(t)
    ]
    assert len(code_asserts) == 1
    assert code_asserts[0].get("no_log") is True


def test_enroll_playbook_play3_uses_authorized_key() -> None:
    plays = _load_playbook(ENROLL_PLAYBOOK)
    play3_tasks = plays[2]["tasks"]
    modules = {_module_key(t) for t in play3_tasks}
    assert "ansible.posix.authorized_key" in modules


def test_unenroll_playbook_guards_register_clear_on_registration_stat() -> None:
    plays = _load_playbook(UNENROLL_PLAYBOOK)
    connector_play = next(p for p in plays if "remo_connector_group" in str(p.get("hosts", "")))
    tasks = connector_play["tasks"]
    clear_tasks = [
        t for t in tasks if "amazon-ssm-agent -register -clear" in yaml.dump(t)
    ]
    assert len(clear_tasks) == 1
    task = clear_tasks[0]
    assert "when" in task
    assert "default(false)" in task["when"]


def test_unenroll_playbook_purge_is_guarded() -> None:
    plays = _load_playbook(UNENROLL_PLAYBOOK)
    connector_play = next(p for p in plays if "remo_connector_group" in str(p.get("hosts", "")))
    purge_tasks = [
        t
        for t in connector_play["tasks"]
        if "when" in t and "connector_purge" in str(t["when"])
    ]
    assert len(purge_tasks) >= 3
    for task in purge_tasks:
        assert "default(false)" in task["when"]


# ---------------------------------------------------------------------------
# Optional live syntax-check, gated on ansible-playbook being installed.
# ---------------------------------------------------------------------------

ANSIBLE_PLAYBOOK = shutil.which("ansible-playbook")


@pytest.mark.skipif(ANSIBLE_PLAYBOOK is None, reason="ansible-playbook not available in this sandbox")
def test_enroll_playbook_syntax_check() -> None:
    result = subprocess.run(
        [
            ANSIBLE_PLAYBOOK,
            "--syntax-check",
            "ssm_connector_enroll.yml",
            "-e", "connector_name=x",
            "-e", "ssm_activation_id=x",
            "-e", "ssm_region=eu-central-1",
            "-e", "ssm_activation_code=x",
            "-e", "remo_ssh_host=h",
            "-e", "remo_ssh_user=u",
            "-e", "remo_ssh_port=22",
        ],
        capture_output=True,
        text=True,
        cwd=ANSIBLE_DIR,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(ANSIBLE_PLAYBOOK is None, reason="ansible-playbook not available in this sandbox")
def test_unenroll_playbook_syntax_check() -> None:
    result = subprocess.run(
        [
            ANSIBLE_PLAYBOOK,
            "--syntax-check",
            "ssm_connector_unenroll.yml",
            "-e", "remo_ssh_host=h",
            "-e", "remo_ssh_user=u",
            "-e", "remo_ssh_port=22",
        ],
        capture_output=True,
        text=True,
        cwd=ANSIBLE_DIR,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Regression gates from the 025 review pass
# ---------------------------------------------------------------------------

SCAN_TASKS_FILE = ROLE_DIR / "tasks" / "scan_and_trust_host_key.yml"


@pytest.mark.parametrize("path", [ENROLL_PLAYBOOK, UNENROLL_PLAYBOOK])
def test_no_play_var_templates_itself(path: Path) -> None:
    """`x: "{{ x | default(..) }}"` as a play var is an Ansible recursive-loop
    error whenever `x` is not passed as an extra-var."""
    for play in _load_playbook(path):
        for key, value in (play.get("vars") or {}).items():
            assert not re.search(r"\{\{\s*" + re.escape(key) + r"\b", str(value)), (
                f"play var {key!r} in {path.name} references itself: {value!r}"
            )


def test_host_key_lookup_uses_bracketed_port_form() -> None:
    text = SCAN_TASKS_FILE.read_text()
    tasks = _load_tasks(SCAN_TASKS_FILE)
    lookup = next(t for t in tasks if "ssh-keygen" in yaml.dump(t))
    argv = lookup["ansible.builtin.command"]["argv"]
    assert argv[:3] == ["ssh-keygen", "-F", "{{ ssm_known_hosts_key }}"]
    assert "'[' ~ exposed_host.address ~ ']:'" in text
    assert "== 22" in text


def test_host_key_change_check_is_per_line_not_substring() -> None:
    """`ssh-keygen -F` interleaves '# Host … found' headers, so a multi-line
    keyscan block is never a substring of its output — the check must
    compare individual key lines (else every re-enrollment 'changes')."""
    tasks = _load_tasks(SCAN_TASKS_FILE)
    change = next(t for t in tasks if "has changed" in str(t.get("name", "")))
    assert "not in (ssm_known_hosts_lookup.stdout" not in str(change["when"])
    assert "ssm_scanned_key_lines" in str(change["when"])


def test_scan_tasks_registered_access_uses_default() -> None:
    tasks = _load_tasks(SCAN_TASKS_FILE)
    registered = {t["register"] for t in tasks if "register" in t}
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(n) for n in registered) + r")\.(rc|stdout|stdout_lines)\b"
    )
    for line in SCAN_TASKS_FILE.read_text().splitlines():
        if pattern.search(line) and not line.lstrip().startswith("#"):
            assert "default(" in line, f"unsafe registered-variable access: {line.strip()!r}"


def test_connector_state_write_preserves_enrolled_at() -> None:
    tasks = _load_tasks(TASKS_FILE)
    write = next(t for t in tasks if t.get("name") == "Write the connector's state record (connector.json)")
    content = write["ansible.builtin.copy"]["content"]
    assert "ansible_date_time" not in content, "a fresh timestamp makes every re-run 'changed'"
    assert "ssm_enrolled_at" in content
