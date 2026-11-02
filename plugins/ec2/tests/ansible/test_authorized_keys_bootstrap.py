"""EC2 keeps the cloud-injected key bootstrap on (#121).

Moved from remo's tests/ansible/test_authorized_keys_bootstrap.py when the
AWS playbooks moved into this plugin (#258). EC2 injects the operator's key
into the AMI's default user only, so the `user_setup` role's first-contact bootstrap
(``user_setup_bootstrap_cloud_keys``, default true in remo core) must not be
disabled here: without it a first-contact provision cannot seed the remo user
and locks the operator out. The role's own scoping/merging is tested in core.
"""

from __future__ import annotations

import pytest
import yaml

from remo_provider_ec2.provider import PLAYBOOK_DIR

BOOTSTRAP_FLAG = "user_setup_bootstrap_cloud_keys"

SCOPED_ON_PLAYBOOKS = ["aws_configure.yml", "aws_site.yml"]


@pytest.mark.parametrize("playbook", SCOPED_ON_PLAYBOOKS)
def test_cloud_provider_keeps_the_bootstrap(playbook: str) -> None:
    plays = yaml.safe_load((PLAYBOOK_DIR / playbook).read_text())
    assert plays, f"{playbook} has no plays"
    for play in plays:
        assert BOOTSTRAP_FLAG not in (play.get("vars") or {}), (
            f"{playbook} must leave the bootstrap on: without it a first-contact "
            "provision cannot seed the remo user and locks the operator out"
        )
