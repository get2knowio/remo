"""EC2's half of the apt-sources repair ordering coverage (#120).

Moved from remo's tests/ansible/test_apt_sources_repair.py when the AWS
playbooks moved into this plugin (#258). The repair task itself
(``tasks/repair_apt_sources.yml``) stays in remo core and its behavior is tested
there; what this plugin owns is that its own configure/site playbooks include
it — now through ``{{ remo_core_ansible_dir }}``, since the task no longer sits
next to the playbook — in a privileged play, *ahead of* `Update apt cache`.
A repair that runs after the thing it unwedges is no repair at all.
"""

from __future__ import annotations

import pytest
import yaml

from remo_provider_ec2.provider import PLAYBOOK_DIR

PLAYBOOKS = ["aws_configure.yml", "aws_site.yml"]

REPAIR_INCLUDE = "{{ remo_core_ansible_dir }}/tasks/repair_apt_sources.yml"


@pytest.mark.parametrize("playbook", PLAYBOOKS)
def test_repair_runs_before_update_apt_cache(playbook: str) -> None:
    plays = yaml.safe_load((PLAYBOOK_DIR / playbook).read_text())
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
def test_repair_play_is_privileged(playbook: str) -> None:
    """Rewriting /etc/apt/sources.list.d requires root."""
    plays = yaml.safe_load((PLAYBOOK_DIR / playbook).read_text())
    for play in plays:
        pre_tasks = play.get("pre_tasks") or []
        if any(t.get("ansible.builtin.include_tasks") == REPAIR_INCLUDE for t in pre_tasks):
            assert play.get("become") is True, f"{playbook}: repair play is not become: true"
