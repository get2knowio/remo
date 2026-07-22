"""FR-012 guard: provider lifecycle ops must reject added (``type="ssh"``) hosts.

A host registered with ``remo add`` has ``type="ssh"`` and no managed provider
infrastructure. Running a provider lifecycle operation (``destroy``, resize via
``update``, or a mutating ``snapshot`` op) against such a name must fail with a
clear "manually-registered SSH host" message pointing at ``remo remove`` — never
an opaque error or a silent mis-target.

All provider I/O (subprocess/SSH/boto3/Hetzner API) is mocked so the tests never
touch the network; in practice the guard exits before any of it runs.
"""

from __future__ import annotations

import pytest

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
    ("incus.destroy", lambda: providers_incus.destroy(name=ADDED_NAME, auto_confirm=True)),
    ("incus.update", lambda: providers_incus.update(name=ADDED_NAME)),
    (
        "incus.snapshot_create",
        lambda: providers_incus.snapshot_create(
            container=ADDED_NAME, host="localhost", user="", snap_name="snap1"
        ),
    ),
    (
        "incus.snapshot_restore",
        lambda: providers_incus.snapshot_restore(
            container=ADDED_NAME, host="localhost", user="", snap_name="snap1",
            auto_confirm=True,
        ),
    ),
    (
        "incus.snapshot_delete",
        lambda: providers_incus.snapshot_delete(
            container=ADDED_NAME, host="localhost", user="", snap_name="snap1",
            auto_confirm=True,
        ),
    ),
    (
        "proxmox.destroy",
        lambda: providers_proxmox.destroy(name=ADDED_NAME, host="node1", auto_confirm=True),
    ),
    ("proxmox.update", lambda: providers_proxmox.update(name=ADDED_NAME, host="node1")),
    (
        "proxmox.snapshot_create",
        lambda: providers_proxmox.snapshot_create(
            container=ADDED_NAME, host="node1", user="root", vmid="100",
            snap_name="snap1",
        ),
    ),
    (
        "proxmox.snapshot_restore",
        lambda: providers_proxmox.snapshot_restore(
            container=ADDED_NAME, host="node1", user="root", vmid="100",
            snap_name="snap1", auto_confirm=True,
        ),
    ),
    (
        "proxmox.snapshot_delete",
        lambda: providers_proxmox.snapshot_delete(
            container=ADDED_NAME, host="node1", user="root", vmid="100",
            snap_name="snap1", auto_confirm=True,
        ),
    ),
    ("aws.destroy", lambda: providers_aws.destroy(name=ADDED_NAME, auto_confirm=True)),
    ("aws.update", lambda: providers_aws.update(name=ADDED_NAME)),
    (
        "aws.snapshot_create",
        lambda: providers_aws.snapshot_create(instance_name=ADDED_NAME, snap_name="snap1"),
    ),
    (
        "aws.snapshot_restore",
        lambda: providers_aws.snapshot_restore(
            instance_name=ADDED_NAME, snap_name="snap1", auto_confirm=True
        ),
    ),
    (
        "aws.snapshot_delete",
        lambda: providers_aws.snapshot_delete(
            instance_name=ADDED_NAME, snap_name="snap1", auto_confirm=True
        ),
    ),
    ("hetzner.destroy", lambda: providers_hetzner.destroy(name=ADDED_NAME, auto_confirm=True)),
    ("hetzner.update", lambda: providers_hetzner.update(name=ADDED_NAME)),
    (
        "hetzner.snapshot_create",
        lambda: providers_hetzner.snapshot_create(server_name=ADDED_NAME, snap_name="snap1"),
    ),
    (
        "hetzner.snapshot_restore",
        lambda: providers_hetzner.snapshot_restore(
            server_name=ADDED_NAME, snap_name="snap1", auto_confirm=True
        ),
    ),
    (
        "hetzner.snapshot_delete",
        lambda: providers_hetzner.snapshot_delete(
            server_name=ADDED_NAME, snap_name="snap1", auto_confirm=True
        ),
    ),
]


@pytest.mark.parametrize(
    "op", [op for _, op in LIFECYCLE_OPS], ids=[i for i, _ in LIFECYCLE_OPS]
)
def test_lifecycle_op_rejects_added_ssh_host(added_ssh_host, op):
    with pytest.raises(SystemExit) as exc:
        op()
    message = str(exc.value)
    assert "manually-registered SSH host" in message
    assert "remo remove" in message
    assert ADDED_NAME in message


# ---------------------------------------------------------------------------
# Shared helper (core/known_hosts.guard_not_added_ssh_host) — unit behavior
# ---------------------------------------------------------------------------


def test_guard_message_names_the_provider(added_ssh_host):
    with pytest.raises(SystemExit) as exc:
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
