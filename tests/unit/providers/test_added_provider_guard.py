"""FR-012 guard: provider lifecycle ops must reject added (``type="ssh"``) hosts.

A host registered with ``remo add`` has ``type="ssh"`` and no managed provider
infrastructure. Running a provider lifecycle operation (``destroy``,
``upgrade``, ``resize``, ``tag``, or a mutating ``snapshot`` op) against such
a name must fail with a
clear "manually-registered SSH host" message pointing at ``remo remove`` — never
an opaque error or a silent mis-target.

All provider I/O (subprocess/SSH/boto3/Hetzner API) is mocked so the tests never
touch the network; in practice the guard exits before any of it runs.
"""

from __future__ import annotations

import pytest

from remo_cli.core.errors import PreconditionError
from remo_cli.core.known_hosts import guard_not_added_ssh_host, save_known_host
from remo_cli.models.host import KnownHost
from remo_cli.providers import aws as providers_aws
from remo_cli.providers import hetzner as providers_hetzner
from remo_cli.providers import incus as providers_incus
from remo_cli.providers import proxmox as providers_proxmox


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


# Each entry: (id, callable performing a provider lifecycle op on ADDED_NAME).
LIFECYCLE_OPS = [
    # incus.destroy: the guard moved to the shared core/lifecycle.run_destroy
    # template (018-provider-abstraction T038); providers_incus.teardown()
    # itself performs destruction only (R-A3) and no longer guards. Generic
    # guard-ordering coverage lives in tests/unit/core/test_lifecycle.py;
    # Incus-specific wiring coverage lives at the CLI layer:
    # tests/unit/cli/providers/test_incus_snapshot.py::TestDestroyCLI.
    ("incus.upgrade", lambda: providers_incus.upgrade(name=ADDED_NAME)),
    ("incus.resize", lambda: providers_incus.resize(name=ADDED_NAME, volume_size="20")),
    ("incus.tag", lambda: providers_incus.tag(name=ADDED_NAME)),
    (
        "incus.snapshot_create",
        lambda: providers_incus.snapshot_create_legacy(
            container=ADDED_NAME, host="localhost", user="", snap_name="snap1"
        ),
    ),
    (
        "incus.snapshot_restore",
        lambda: providers_incus.snapshot_restore_legacy(
            container=ADDED_NAME, host="localhost", user="", snap_name="snap1",
            auto_confirm=True,
        ),
    ),
    (
        "incus.snapshot_delete",
        lambda: providers_incus.snapshot_delete_legacy(
            container=ADDED_NAME, host="localhost", user="", snap_name="snap1",
            auto_confirm=True,
        ),
    ),
    # proxmox.destroy: the guard moved to the shared core/lifecycle.run_destroy
    # template (018-provider-abstraction T038); providers_proxmox.teardown()
    # itself performs destruction only (R-A3) and no longer guards. Coverage
    # for the proxmox destroy guard now lives at the CLI layer:
    # tests/unit/cli/providers/test_proxmox_snapshot.py::TestDestroyCLI.
    ("proxmox.upgrade", lambda: providers_proxmox.upgrade(name=ADDED_NAME, host="node1")),
    ("proxmox.resize", lambda: providers_proxmox.resize(name=ADDED_NAME, host="node1", volume_size="20")),
    ("proxmox.tag", lambda: providers_proxmox.tag(name=ADDED_NAME, host="node1")),
    (
        "proxmox.snapshot_create",
        lambda: providers_proxmox.snapshot_create_legacy(
            container=ADDED_NAME, host="node1", user="root", vmid="100",
            snap_name="snap1",
        ),
    ),
    (
        "proxmox.snapshot_restore",
        lambda: providers_proxmox.snapshot_restore_legacy(
            container=ADDED_NAME, host="node1", user="root", vmid="100",
            snap_name="snap1", auto_confirm=True,
        ),
    ),
    (
        "proxmox.snapshot_delete",
        lambda: providers_proxmox.snapshot_delete_legacy(
            container=ADDED_NAME, host="node1", user="root", vmid="100",
            snap_name="snap1", auto_confirm=True,
        ),
    ),
    # aws.destroy: the guard moved to the shared core/lifecycle.run_destroy
    # template (018-provider-abstraction T038); providers_aws.teardown()
    # itself performs destruction only (R-A3) and no longer guards. Generic
    # guard-ordering coverage lives in tests/unit/core/test_lifecycle.py;
    # AWS-specific wiring coverage lives at the CLI layer:
    # tests/unit/cli/providers/test_aws_snapshot.py::TestDestroyCLI.
    ("aws.upgrade", lambda: providers_aws.upgrade(name=ADDED_NAME)),
    ("aws.resize", lambda: providers_aws.resize(name=ADDED_NAME, volume_size="20")),
    (
        "aws.snapshot_create",
        lambda: providers_aws.snapshot_create_legacy(
            instance_name=ADDED_NAME, snap_name="snap1"
        ),
    ),
    (
        "aws.snapshot_restore",
        lambda: providers_aws.snapshot_restore_legacy(
            instance_name=ADDED_NAME, snap_name="snap1", auto_confirm=True
        ),
    ),
    (
        "aws.snapshot_delete",
        lambda: providers_aws.snapshot_delete_legacy(
            instance_name=ADDED_NAME, snap_name="snap1", auto_confirm=True
        ),
    ),
    # hetzner.destroy: the guard moved to the shared core/lifecycle.run_destroy
    # template (018-provider-abstraction T038); providers_hetzner.teardown()
    # itself performs destruction only (R-A3) and no longer guards. Coverage
    # for the hetzner destroy guard now lives at the CLI layer:
    # tests/unit/cli/providers/test_hetzner_snapshot.py::TestDestroyCLI.
    ("hetzner.upgrade", lambda: providers_hetzner.upgrade(name=ADDED_NAME)),
    ("hetzner.resize", lambda: providers_hetzner.resize(name=ADDED_NAME, volume_size="20")),
    ("hetzner.tag", lambda: providers_hetzner.tag(name=ADDED_NAME)),
    (
        "hetzner.snapshot_create",
        lambda: providers_hetzner.snapshot_create_legacy(
            server_name=ADDED_NAME, snap_name="snap1"
        ),
    ),
    (
        "hetzner.snapshot_restore",
        lambda: providers_hetzner.snapshot_restore_legacy(
            server_name=ADDED_NAME, snap_name="snap1", auto_confirm=True
        ),
    ),
    (
        "hetzner.snapshot_delete",
        lambda: providers_hetzner.snapshot_delete_legacy(
            server_name=ADDED_NAME, snap_name="snap1", auto_confirm=True
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


# ---------------------------------------------------------------------------
# Shared helper (core/known_hosts.guard_not_added_ssh_host) — unit behavior
# ---------------------------------------------------------------------------


def test_guard_message_names_the_provider(added_ssh_host):
    with pytest.raises(PreconditionError) as exc:
        guard_not_added_ssh_host(ADDED_NAME, "aws")
    assert "no managed aws infrastructure" in str(exc.value)


def test_guard_noop_when_no_ssh_host(tmp_config_dir):
    # Empty registry — nothing to block.
    guard_not_added_ssh_host(ADDED_NAME, "incus")


def test_guard_noop_for_unrelated_name(added_ssh_host):
    # A different name is not the added host; guard must not fire.
    guard_not_added_ssh_host("someothervm", "incus")


def test_guard_allows_same_type_managed_container_sharing_name(added_ssh_host):
    # A legit incus container registered as "node/box" shares the short name
    # "box" with the added SSH host; the incus op legitimately targets it, so
    # the guard must NOT block it.
    save_known_host(
        KnownHost(
            type="incus",
            name=f"node1/{ADDED_NAME}",
            host=ADDED_NAME,
            user="remo",
            instance_id="",
            access_mode="direct",
        )
    )
    guard_not_added_ssh_host(ADDED_NAME, "incus")
