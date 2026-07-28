"""Tests for the Protocol Part A entry-based wrappers in providers/incus.py.

Covers ``update_entry`` and the four public ``snapshot_*`` functions that
take a resolved :class:`KnownHost` entry (contracts/provider-protocol.md
Part A) rather than the legacy rc-returning, multi-kwarg functions the CLI
layer still uses. These wrappers must never return an exit code -- success
returns ``None``/``list[Snapshot]``, failure raises
:class:`OperationFailedError` (R-A1).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remo_cli.core.errors import OperationFailedError
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot, SnapshotStatus
from remo_cli.providers import incus as providers_incus


def _entry(name: str = "lab1/dev1", instance_id: str = "root") -> KnownHost:
    return KnownHost(
        type="incus",
        name=name,
        host="dev1",
        user="remo",
        instance_id=instance_id,
        access_mode="direct",
    )


# ---------------------------------------------------------------------------
# update_entry
# ---------------------------------------------------------------------------


class TestUpdateEntry:
    def test_success_returns_none_and_parses_name(self, mocker):
        spy = mocker.patch("remo_cli.providers.incus.upgrade", return_value=0)
        result = providers_incus.update_entry(_entry(), verbose=True)
        assert result is None
        spy.assert_called_once_with(
            name="dev1", host="lab1", host_user="root", verbose=True
        )

    def test_localhost_entry_parses_host_and_container(self, mocker):
        spy = mocker.patch("remo_cli.providers.incus.upgrade", return_value=0)
        providers_incus.update_entry(_entry(name="localhost/dev2", instance_id=""))
        spy.assert_called_once_with(
            name="dev2", host="localhost", host_user="", verbose=False
        )

    def test_underlying_failure_propagates(self, mocker):
        """``upgrade`` now raises directly; ``update_entry`` is a thin
        pass-through with no rc-checking left to do."""
        mocker.patch(
            "remo_cli.providers.incus.upgrade",
            side_effect=OperationFailedError("Failed to configure tools on container 'dev1' (playbook rc=1)."),
        )
        with pytest.raises(OperationFailedError, match="Failed to configure tools"):
            providers_incus.update_entry(_entry())


# ---------------------------------------------------------------------------
# snapshot_create
# ---------------------------------------------------------------------------


class TestEntrySnapshotCreate:
    def test_success_returns_none(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_create_legacy", return_value=0
        )
        result = providers_incus.snapshot_create(
            _entry(), "snap1", description="before x"
        )
        assert result is None
        spy.assert_called_once_with(
            container="dev1",
            host="lab1",
            user="root",
            snap_name="snap1",
            description="before x",
        )

    def test_failure_raises_operation_failed(self, mocker):
        mocker.patch(
            "remo_cli.providers.incus.snapshot_create_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError, match="Failed to create snapshot"):
            providers_incus.snapshot_create(_entry(), "snap1")


# ---------------------------------------------------------------------------
# snapshot_restore
# ---------------------------------------------------------------------------


class TestEntrySnapshotRestore:
    def test_success_returns_none_and_always_confirms(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_restore_legacy", return_value=0
        )
        result = providers_incus.snapshot_restore(_entry(), "snap1")
        assert result is None
        spy.assert_called_once_with(
            container="dev1",
            host="lab1",
            user="root",
            snap_name="snap1",
            auto_confirm=True,
        )

    def test_failure_raises_operation_failed(self, mocker):
        mocker.patch(
            "remo_cli.providers.incus.snapshot_restore_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError, match="Failed to restore snapshot"):
            providers_incus.snapshot_restore(_entry(), "snap1")


# ---------------------------------------------------------------------------
# snapshot_delete
# ---------------------------------------------------------------------------


class TestEntrySnapshotDelete:
    def test_success_returns_none_and_always_confirms(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_delete_legacy", return_value=0
        )
        result = providers_incus.snapshot_delete(_entry(), "snap1")
        assert result is None
        spy.assert_called_once_with(
            container="dev1",
            host="lab1",
            user="root",
            snap_name="snap1",
            auto_confirm=True,
        )

    def test_failure_raises_operation_failed(self, mocker):
        mocker.patch(
            "remo_cli.providers.incus.snapshot_delete_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError, match="Failed to delete snapshot"):
            providers_incus.snapshot_delete(_entry(), "snap1")


# ---------------------------------------------------------------------------
# snapshot_list
# ---------------------------------------------------------------------------


def _snap(name: str = "snap1") -> Snapshot:
    return Snapshot(
        provider="incus",
        instance_name="dev1",
        name=name,
        backend_id=f"dev1/{name}",
        created_at=datetime.now(tz=timezone.utc),
        size_bytes=None,
        description="",
        status=SnapshotStatus.AVAILABLE,
    )


class TestEntrySnapshotList:
    def test_success_returns_snapshot_list(self, mocker):
        snaps = [_snap()]
        spy = mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=snaps,
        )
        result = providers_incus.snapshot_list(_entry())
        assert result == snaps
        spy.assert_called_once_with(host="lab1", container="dev1", user="root")

    def test_provider_failure_raises_operation_failed(self, mocker):
        # _list_snapshots_for_container itself raises OperationFailedError
        # directly now; snapshot_list is a thin pass-through.
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            side_effect=OperationFailedError("incus query failed (rc=255): boom"),
        )
        with pytest.raises(OperationFailedError, match="incus query failed"):
            providers_incus.snapshot_list(_entry())
