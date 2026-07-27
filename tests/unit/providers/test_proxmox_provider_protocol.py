"""Tests for the entry-based Provider Protocol wrappers on providers/proxmox.py.

Covers `update_entry` and the four public `snapshot_*(entry, ...)` functions
added for contracts/provider-protocol.md Part A (spec 018, T015). These wrap
the legacy rc-returning functions (`update`, `snapshot_create_legacy`,
`snapshot_restore_legacy`, `snapshot_delete_legacy`, `_list_snapshots_for_vmid`)
and convert failure into `OperationFailedError` per R-A1, resolving all
Proxmox name-format knowledge (host/container split, vmid, node user from
`region`) from the entry per R-A2.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remo_cli.core.errors import OperationFailedError
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot, SnapshotStatus
from remo_cli.providers import proxmox as providers_proxmox


def _entry(**overrides: object) -> KnownHost:
    fields: dict[str, object] = dict(
        type="proxmox",
        name="lab1/dev1",
        host="dev1.local",
        user="remo",
        instance_id="100",
        access_mode="direct",
        region="root",
    )
    fields.update(overrides)
    return KnownHost(**fields)  # type: ignore[arg-type]


def _snap(name: str = "pre-x") -> Snapshot:
    return Snapshot(
        provider="proxmox",
        instance_name="dev1",
        name=name,
        backend_id=name,
        created_at=datetime.now(tz=timezone.utc),
        size_bytes=None,
        description="",
        status=SnapshotStatus.AVAILABLE,
    )


class TestUpdateEntry:
    def test_success_returns_none(self, mocker):
        spy = mocker.patch("remo_cli.providers.proxmox.update", return_value=None)
        result = providers_proxmox.update_entry(_entry(), verbose=True)
        assert result is None
        spy.assert_called_once_with(
            name="dev1", host="lab1", user="root", verbose=True, apply_marker=False
        )

    def test_failure_raises_operation_failed_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.proxmox.update",
            side_effect=OperationFailedError("Failed to update tools on 'dev1' (playbook rc=1)."),
        )
        with pytest.raises(OperationFailedError):
            providers_proxmox.update_entry(_entry())


class TestSnapshotCreateEntry:
    def test_success_returns_none(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.proxmox.snapshot_create_legacy", return_value=0
        )
        result = providers_proxmox.snapshot_create(
            _entry(), "snap1", description="before x"
        )
        assert result is None
        spy.assert_called_once_with(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="snap1",
            description="before x",
        )

    def test_failure_raises_operation_failed_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.proxmox.snapshot_create_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError):
            providers_proxmox.snapshot_create(_entry(), "snap1")


class TestSnapshotRestoreEntry:
    def test_success_returns_none_and_auto_confirms(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.proxmox.snapshot_restore_legacy", return_value=0
        )
        result = providers_proxmox.snapshot_restore(_entry(), "snap1")
        assert result is None
        spy.assert_called_once_with(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="snap1",
            auto_confirm=True,
        )

    def test_failure_raises_operation_failed_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.proxmox.snapshot_restore_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError):
            providers_proxmox.snapshot_restore(_entry(), "snap1")


class TestSnapshotDeleteEntry:
    def test_success_returns_none_and_auto_confirms(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.proxmox.snapshot_delete_legacy", return_value=0
        )
        result = providers_proxmox.snapshot_delete(_entry(), "snap1")
        assert result is None
        spy.assert_called_once_with(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="snap1",
            auto_confirm=True,
        )

    def test_failure_raises_operation_failed_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.proxmox.snapshot_delete_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError):
            providers_proxmox.snapshot_delete(_entry(), "snap1")


class TestSnapshotListEntry:
    def test_success_returns_list(self, mocker):
        snaps = [_snap("a"), _snap("b")]
        spy = mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=snaps,
        )
        result = providers_proxmox.snapshot_list(_entry())
        assert result == snaps
        spy.assert_called_once_with("lab1", "root", "100", "dev1")

    def test_runtime_error_raises_operation_failed_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            side_effect=OperationFailedError("ssh failed"),
        )
        with pytest.raises(OperationFailedError):
            providers_proxmox.snapshot_list(_entry())
