"""Tests for the entry-based Provider Protocol wrappers on providers/aws.py.

Covers `update_entry` and the four public `snapshot_*(entry, ...)` functions
added for contracts/provider-protocol.md Part A (spec 018, T016). These wrap
the legacy rc-returning functions (`update`, `snapshot_create_legacy`,
`snapshot_restore_legacy`, `snapshot_delete_legacy`, `snapshot_list_legacy`)
and convert failure into `OperationFailedError` per R-A1. AWS is FLAT
(name_format): `entry.name` is the instance name directly and `entry.region`
carries the region, so no host-prefix parsing is needed here (R-A2).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from remo_cli.core.errors import OperationFailedError
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot, SnapshotStatus
from remo_cli.providers import aws as providers_aws


def _entry(**overrides: object) -> KnownHost:
    fields: dict[str, object] = dict(
        type="aws",
        name="dev1",
        host="1.2.3.4",
        user="remo",
        instance_id="i-0123456789abcdef0",
        access_mode="ssm",
        region="us-west-2",
    )
    fields.update(overrides)
    return KnownHost(**fields)  # type: ignore[arg-type]


def _snap(name: str = "pre-x") -> Snapshot:
    return Snapshot(
        provider="aws",
        instance_name="dev1",
        name=name,
        backend_id=f"snap-{name}",
        created_at=datetime.now(tz=timezone.utc),
        size_bytes=0,
        description="",
        status=SnapshotStatus.AVAILABLE,
    )


class TestUpdateEntry:
    def test_success_returns_none(self, mocker):
        spy = mocker.patch("remo_cli.providers.aws.update", return_value=None)
        result = providers_aws.update_entry(_entry(), verbose=True)
        assert result is None
        spy.assert_called_once_with(name="dev1", verbose=True)

    def test_failure_propagates_operation_failed_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.aws.update",
            side_effect=OperationFailedError("playbook rc=1"),
        )
        with pytest.raises(OperationFailedError):
            providers_aws.update_entry(_entry())


class TestSnapshotCreateEntry:
    def test_success_returns_none(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_create_legacy", return_value=0
        )
        result = providers_aws.snapshot_create(
            _entry(), "snap1", description="before x"
        )
        assert result is None
        spy.assert_called_once_with(
            instance_name="dev1",
            snap_name="snap1",
            description="before x",
            region="us-west-2",
        )

    def test_failure_raises_operation_failed_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.aws.snapshot_create_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError):
            providers_aws.snapshot_create(_entry(), "snap1")


class TestSnapshotRestoreEntry:
    def test_success_returns_none_and_auto_confirms(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_restore_legacy", return_value=0
        )
        result = providers_aws.snapshot_restore(_entry(), "snap1")
        assert result is None
        spy.assert_called_once_with(
            instance_name="dev1",
            snap_name="snap1",
            region="us-west-2",
            auto_confirm=True,
        )

    def test_failure_raises_operation_failed_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.aws.snapshot_restore_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError):
            providers_aws.snapshot_restore(_entry(), "snap1")


class TestSnapshotDeleteEntry:
    def test_success_returns_none_and_auto_confirms(self, mocker):
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_delete_legacy", return_value=0
        )
        result = providers_aws.snapshot_delete(_entry(), "snap1")
        assert result is None
        spy.assert_called_once_with(
            instance_name="dev1",
            snap_name="snap1",
            region="us-west-2",
            auto_confirm=True,
        )

    def test_failure_raises_operation_failed_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.aws.snapshot_delete_legacy", return_value=1
        )
        with pytest.raises(OperationFailedError):
            providers_aws.snapshot_delete(_entry(), "snap1")


class TestSnapshotListEntry:
    def test_success_returns_list(self, mocker):
        snaps = [_snap("a"), _snap("b")]
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_list_legacy", return_value=snaps
        )
        result = providers_aws.snapshot_list(_entry())
        assert result == snaps
        spy.assert_called_once_with(instance_name="dev1", region="us-west-2")

    def test_runtime_error_raises_operation_failed_error(self, mocker):
        mocker.patch(
            "remo_cli.providers.aws.snapshot_list_legacy",
            side_effect=RuntimeError("no instance found"),
        )
        with pytest.raises(OperationFailedError):
            providers_aws.snapshot_list(_entry())
