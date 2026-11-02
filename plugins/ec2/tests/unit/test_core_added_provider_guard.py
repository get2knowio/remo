"""FR-012 guard: EC2 lifecycle ops must reject added (``type="ssh"``) hosts.

Moved from remo's tests/unit/providers/test_added_provider_guard.py when AWS
became the out-of-tree `remo-provider-ec2` plugin (#258). A host registered
with ``remo add`` has ``type="ssh"`` and no managed provider infrastructure;
running an EC2 lifecycle operation (``upgrade``, ``resize``, or a mutating
``snapshot`` op) against such a name must fail with a clear
"manually-registered SSH host" message pointing at ``remo remove``.

``ec2.destroy``: the guard lives in the shared core/lifecycle.run_destroy
template (018-provider-abstraction T038); ``teardown()`` performs destruction
only (R-A3). Its CLI-layer wiring is covered by
tests/cli/test_ec2_snapshot_cli.py::TestDestroyCLI.

All provider I/O (subprocess/boto3) is mocked so the tests never touch the
network; in practice the guard exits before any of it runs.
"""

from __future__ import annotations

import pytest

import remo_provider_ec2.provider as providers_ec2
from remo_cli.core.errors import PreconditionError
from remo_cli.core.known_hosts import guard_added_ssh_host_only, save_known_host
from remo_cli.models.host import KnownHost

ADDED_NAME = "box"


@pytest.fixture
def added_ssh_host(tmp_config_dir):
    """Register a single ``type="ssh"`` host named ``box`` in a temp registry."""
    save_known_host(
        KnownHost(
            type="ssh",
            name=ADDED_NAME,
            host="1.2.3.4",
            user="remo",
            instance_id="22",
            access_mode="direct",
        )
    )
    return ADDED_NAME


@pytest.fixture(autouse=True)
def _no_network(mocker):
    """Belt-and-suspenders: block real I/O in case a guard ever regresses."""
    mocker.patch("subprocess.run", side_effect=AssertionError("subprocess.run called"))
    mocker.patch(
        "remo_provider_ec2.provider._boto3_session",
        side_effect=AssertionError("boto3 session created"),
    )


LIFECYCLE_OPS = [
    ("ec2.upgrade", lambda: providers_ec2.upgrade(name=ADDED_NAME)),
    ("ec2.resize", lambda: providers_ec2.resize(name=ADDED_NAME, volume_size="20")),
    (
        "ec2.snapshot_create",
        lambda: providers_ec2.snapshot_create_legacy(
            instance_name=ADDED_NAME, snap_name="snap1"
        ),
    ),
    (
        "ec2.snapshot_restore",
        lambda: providers_ec2.snapshot_restore_legacy(
            instance_name=ADDED_NAME, snap_name="snap1", auto_confirm=True
        ),
    ),
    (
        "ec2.snapshot_delete",
        lambda: providers_ec2.snapshot_delete_legacy(
            instance_name=ADDED_NAME, snap_name="snap1", auto_confirm=True
        ),
    ),
]


@pytest.mark.parametrize(
    "op", [op for _, op in LIFECYCLE_OPS], ids=[i for i, _ in LIFECYCLE_OPS]
)
def test_lifecycle_op_rejects_added_ssh_host(added_ssh_host, op):
    with pytest.raises(PreconditionError) as exc:
        op()
    message = str(exc.value)
    assert "manually-registered SSH host" in message
    assert "remo remove" in message
    assert ADDED_NAME in message


def test_configure_guard_names_ec2s_own_upgrade_verb(tmp_config_dir):
    save_known_host(
        KnownHost(
            type="ec2",
            name="web1",
            host="1.2.3.4",
            user="remo",
            instance_id="",
            access_mode="direct",
        )
    )
    with pytest.raises(PreconditionError, match="remo ec2 upgrade web1"):
        guard_added_ssh_host_only("web1")
