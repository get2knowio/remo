"""Tests for the Protocol Part A entry-based wrappers in providers/hetzner.py.

Covers ``update_entry`` and the four public ``snapshot_*`` functions that
take a resolved :class:`KnownHost` entry (contracts/provider-protocol.md
Part A) rather than the legacy rc-returning, multi-kwarg functions the CLI
layer still uses. Hetzner is FLAT (name_format), so ``entry.name`` is the
server name directly -- no host/container parsing. These wrappers must
never return an exit code -- success returns ``None``/``list[Snapshot]``,
failure raises :class:`OperationFailedError` (R-A1).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot, SnapshotStatus
from remo_cli.providers import hetzner as providers_hetzner


def _entry(name: str = "dev1") -> KnownHost:
    return KnownHost(type="hetzner", name=name, host="5.6.7.8", user="remo")


# ---------------------------------------------------------------------------
# update_entry
# ---------------------------------------------------------------------------


class TestUpdateEntry:
    def test_success_returns_none(self, mocker):
        spy = mocker.patch("remo_cli.providers.hetzner.upgrade", return_value=None)
        result = providers_hetzner.update_entry(_entry(), verbose=True)
        assert result is None
        spy.assert_called_once_with(name="dev1", verbose=True)

    def test_default_verbose_false(self, mocker):
        spy = mocker.patch("remo_cli.providers.hetzner.upgrade", return_value=None)
        providers_hetzner.update_entry(_entry())
        spy.assert_called_once_with(name="dev1", verbose=False)

    def test_update_failure_propagates(self, mocker):
        mocker.patch(
            "remo_cli.providers.hetzner.upgrade",
            side_effect=OperationFailedError(
                "Failed to update tools on 'dev1' (playbook rc=1)."
            ),
        )
        with pytest.raises(OperationFailedError, match="Failed to update tools"):
            providers_hetzner.update_entry(_entry())


# ---------------------------------------------------------------------------
# snapshot_create
# ---------------------------------------------------------------------------


class TestEntrySnapshotCreate:
    def test_success_returns_none(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_create_legacy", return_value=0
        )
        result = providers_hetzner.snapshot_create(
            _entry(), "snap1", description="before x"
        )
        assert result is None
        spy.assert_called_once_with(
            server_name="dev1", snap_name="snap1", description="before x"
        )

    def test_default_description_empty(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_create_legacy", return_value=0
        )
        providers_hetzner.snapshot_create(_entry(), "snap1")
        spy.assert_called_once_with(
            server_name="dev1", snap_name="snap1", description=""
        )

    def test_failure_raises_operation_failed(self, mocker):
        mocker.patch(
            "remo_cli.providers.hetzner.snapshot_create_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError, match="Failed to create snapshot"):
            providers_hetzner.snapshot_create(_entry(), "snap1")


# ---------------------------------------------------------------------------
# snapshot_restore
# ---------------------------------------------------------------------------


class TestEntrySnapshotRestore:
    def test_success_returns_none_and_always_confirms(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_restore_legacy", return_value=0
        )
        result = providers_hetzner.snapshot_restore(_entry(), "snap1")
        assert result is None
        spy.assert_called_once_with(
            server_name="dev1", snap_name="snap1", auto_confirm=True
        )

    def test_failure_raises_operation_failed(self, mocker):
        mocker.patch(
            "remo_cli.providers.hetzner.snapshot_restore_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError, match="Failed to restore snapshot"):
            providers_hetzner.snapshot_restore(_entry(), "snap1")


# ---------------------------------------------------------------------------
# snapshot_delete
# ---------------------------------------------------------------------------


class TestEntrySnapshotDelete:
    def test_success_returns_none_and_always_confirms(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_delete_legacy", return_value=0
        )
        result = providers_hetzner.snapshot_delete(_entry(), "snap1")
        assert result is None
        spy.assert_called_once_with(
            server_name="dev1", snap_name="snap1", auto_confirm=True
        )

    def test_failure_raises_operation_failed(self, mocker):
        mocker.patch(
            "remo_cli.providers.hetzner.snapshot_delete_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError, match="Failed to delete snapshot"):
            providers_hetzner.snapshot_delete(_entry(), "snap1")


# ---------------------------------------------------------------------------
# snapshot_list
# ---------------------------------------------------------------------------


def _snap(name: str = "snap1") -> Snapshot:
    return Snapshot(
        provider="hetzner",
        instance_name="dev1",
        name=name,
        backend_id="100",
        created_at=datetime.now(tz=timezone.utc),
        size_bytes=None,
        description="",
        status=SnapshotStatus.AVAILABLE,
    )


class TestEntrySnapshotList:
    def test_success_returns_snapshot_list(self, mocker):
        snaps = [_snap()]
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_list_legacy", return_value=snaps
        )
        result = providers_hetzner.snapshot_list(_entry())
        assert result == snaps
        spy.assert_called_once_with(server_name="dev1")

    def test_provider_failure_propagates_precondition_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.hetzner.snapshot_list_legacy",
            side_effect=PreconditionError("No Hetzner server found named 'dev1'."),
        )
        with pytest.raises(PreconditionError, match="No Hetzner server found"):
            providers_hetzner.snapshot_list(_entry())
