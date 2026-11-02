"""The contract between this plugin's playbooks and remo's Ansible tree
(core/ansible_runner.run_playbook's ``playbook_dir``).

The playbooks run from remo's ansible/ directory but live here, so Ansible
loads *this* package's group_vars/ (playbook-adjacent) and never remo's. Each
play therefore pulls remo's shared defaults in with ``vars_files``, which sits
at play-vars precedence -- above group_vars -- so a name defined in both files
would silently take remo's value. These tests pin both halves.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from remo_cli.core.config import get_ansible_dir
from remo_provider_ec2.provider import PLAYBOOK_DIR

CORE_DEFAULTS = "{{ remo_core_ansible_dir }}/group_vars/all.yml"
PLAYBOOKS = sorted(p.name for p in PLAYBOOK_DIR.glob("aws_*.yml"))


def _plays(name: str) -> list[dict]:
    return yaml.safe_load((PLAYBOOK_DIR / name).read_text())


def test_ships_every_playbook_role_and_requirements() -> None:
    # The Ansible side keeps its aws_* names (they describe AWS APIs); only
    # the provider type became ec2 (#258).
    assert PLAYBOOKS == [
        "aws_configure.yml",
        "aws_resize.yml",
        "aws_site.yml",
        "aws_teardown.yml",
    ]
    assert (PLAYBOOK_DIR / "roles" / "aws_server" / "tasks" / "main.yml").is_file()
    reqs = yaml.safe_load((PLAYBOOK_DIR / "requirements.yml").read_text())
    assert [c["name"] for c in reqs["collections"]] == ["amazon.aws", "community.aws"]


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_every_play_loads_remos_shared_defaults(playbook: str) -> None:
    for play in _plays(playbook):
        assert CORE_DEFAULTS in (play.get("vars_files") or []), play.get("name")


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_no_relative_include_into_remos_tree(playbook: str) -> None:
    """`tasks/X.yml` would resolve next to *this* playbook, where it does not
    exist; remo's shared tasks are reached through remo_core_ansible_dir."""
    text = (PLAYBOOK_DIR / playbook).read_text()
    assert "include_tasks: tasks/" not in text
    assert "import_tasks: tasks/" not in text


def test_plugin_defaults_never_shadow_remos() -> None:
    ours = yaml.safe_load((PLAYBOOK_DIR / "group_vars" / "all.yml").read_text())
    remos = yaml.safe_load((get_ansible_dir() / "group_vars" / "all.yml").read_text())
    assert ours, "the plugin's own defaults went missing"
    assert all(key.startswith("aws_") for key in ours)
    assert not set(ours) & set(remos)


def test_ec2_is_gone_from_remos_tree() -> None:
    core = get_ansible_dir()
    assert not list(core.glob("aws_*.yml"))
    assert not (core / "roles" / "aws_server").exists()
    requirements = (core / "requirements.yml").read_text()
    assert "amazon.aws" not in requirements
    assert "community.aws" not in requirements
    remos = yaml.safe_load((core / "group_vars" / "all.yml").read_text())
    assert not [key for key in remos if key.startswith("aws_")]


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_syntax_check_from_remos_ansible_dir(playbook: str) -> None:
    """The real resolution path: cwd is remo's ansible/ (its ansible.cfg and
    roles_path), the playbook is addressed absolutely, and remo_core_ansible_dir
    is passed exactly as run_playbook passes it."""
    core = get_ansible_dir()
    ansible_playbook = Path(sys.executable).parent / "ansible-playbook"
    result = subprocess.run(
        [
            str(ansible_playbook),
            "--syntax-check",
            str(PLAYBOOK_DIR / playbook),
            "-e",
            f'{{"remo_core_ansible_dir": "{core}"}}',
        ],
        cwd=core,
        capture_output=True,
        text=True,
    )
    hint = ""
    if "couldn't resolve module/action" in result.stdout + result.stderr:
        hint = (
            "\n\nThe Galaxy collections are not installed. Run:\n"
            f"  ansible-galaxy collection install -r {core / 'requirements.yml'}\n"
            f"  ansible-galaxy collection install -r {PLAYBOOK_DIR / 'requirements.yml'}"
        )
    assert result.returncode == 0, result.stdout + result.stderr + hint
