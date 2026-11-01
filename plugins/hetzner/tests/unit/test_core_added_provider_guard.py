"""FR-012 guard: Hetzner lifecycle ops must reject added (``type="ssh"``) hosts.

Moved from remo's tests/unit/providers/test_added_provider_guard.py when Hetzner
became the out-of-tree `remo-provider-hetzner` plugin. A host registered with
``remo add`` has ``type="ssh"`` and no managed provider infrastructure; running
a Hetzner lifecycle operation (``upgrade``, ``resize``, ``tag``, or a mutating
``snapshot`` op) against such a name must fail with a clear
"manually-registered SSH host" message pointing at ``remo remove``.

``hetzner.destroy``: the guard lives in the shared core/lifecycle.run_destroy
template (018-provider-abstraction T038); ``teardown()`` performs destruction
only (R-A3). Its CLI-layer wiring is covered by
tests/cli/test_hetzner_snapshot_cli.py::TestDestroyCLI.

All provider I/O is mocked so the tests never touch the network; in practice
the guard exits before any of it runs.
"""

from __future__ import annotations

import pytest

import remo_provider_hetzner.provider as providers_hetzner
from remo_cli.core.errors import PreconditionError
from remo_cli.core.known_hosts import save_known_host
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
        "remo_provider_hetzner.provider._hetzner_api",
        side_effect=AssertionError("Hetzner API called"),
    )


LIFECYCLE_OPS = [
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
