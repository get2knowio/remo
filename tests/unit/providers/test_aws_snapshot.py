"""Tests for AWS snapshot business-logic (providers/aws.py snapshot_*)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from remo_cli.models.snapshot import Snapshot, SnapshotStatus
from remo_cli.providers import aws as providers_aws


def _instance_describe_response(
    instance_id: str = "i-abc",
    state: str = "running",
    az: str = "us-east-1a",
    volume_id: str = "vol-old",
) -> dict:
    return {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": instance_id,
                        "RootDeviceName": "/dev/sda1",
                        "Placement": {"AvailabilityZone": az},
                        "State": {"Name": state},
                        "BlockDeviceMappings": [
                            {
                                "DeviceName": "/dev/sda1",
                                "Ebs": {"VolumeId": volume_id},
                            }
                        ],
                    }
                ]
            }
        ]
    }


def _volume_describe_response(
    volume_id: str = "vol-old",
    size_gib: int = 20,
    state: str = "in-use",
    volume_type: str = "gp3",
) -> dict:
    return {
        "Volumes": [
            {
                "VolumeId": volume_id,
                "Size": size_gib,
                "State": state,
                "VolumeType": volume_type,
            }
        ]
    }


def _snapshot_describe_response(
    snap_id: str = "snap-1",
    snap_name: str = "pre-x",
    state: str = "completed",
    size_gib: int = 20,
    description: str = "",
) -> dict:
    return {
        "Snapshots": [
            {
                "SnapshotId": snap_id,
                "VolumeSize": size_gib,
                "State": state,
                "StartTime": datetime(2026, 5, 24, 10, 15, 30, tzinfo=timezone.utc),
                "Description": description,
                "Tags": [
                    {"Key": "remo", "Value": "true"},
                    {"Key": "remo-snapshot-name", "Value": snap_name},
                    {"Key": "remo-instance", "Value": "dev1"},
                ],
            }
        ]
    }


@pytest.fixture
def ec2(mocker):
    """Stub `_boto3_session(...).client('ec2')` to return a MagicMock and
    `get_aws_region` to be a no-op."""
    mocker.patch("remo_cli.providers.aws.get_aws_region", return_value="us-east-1")
    ec2_client = MagicMock()
    session = MagicMock()
    session.client.return_value = ec2_client
    mocker.patch("remo_cli.providers.aws._boto3_session", return_value=session)
    return ec2_client


# ---------------------------------------------------------------------------
# _list_snapshots_for_volume
# ---------------------------------------------------------------------------


class TestListSnapshotsForVolume:
    def test_maps_pending_status(self, ec2):
        ec2.describe_snapshots.return_value = _snapshot_describe_response(state="pending")
        result = providers_aws._list_snapshots_for_volume(  # noqa: SLF001
            ec2, "vol-old", "dev1"
        )
        assert len(result) == 1
        assert result[0].status is SnapshotStatus.PENDING

    def test_maps_completed_to_available(self, ec2):
        ec2.describe_snapshots.return_value = _snapshot_describe_response(state="completed")
        result = providers_aws._list_snapshots_for_volume(  # noqa: SLF001
            ec2, "vol-old", "dev1"
        )
        assert result[0].status is SnapshotStatus.AVAILABLE
        # Filter scoped to volume-id + remo=true tag
        kwargs = ec2.describe_snapshots.call_args.kwargs
        filters = {f["Name"]: f["Values"] for f in kwargs["Filters"]}
        assert filters["volume-id"] == ["vol-old"]
        assert filters["tag:remo"] == ["true"]
        assert kwargs["OwnerIds"] == ["self"]

    def test_user_facing_name_from_tag(self, ec2):
        ec2.describe_snapshots.return_value = _snapshot_describe_response(snap_name="pre-x")
        result = providers_aws._list_snapshots_for_volume(  # noqa: SLF001
            ec2, "vol-old", "dev1"
        )
        assert result[0].name == "pre-x"


# ---------------------------------------------------------------------------
# snapshot_create
# ---------------------------------------------------------------------------


class TestSnapshotCreate:
    def test_create_reports_broker_reconciliation_and_vault_summary(self, mocker, capsys):
        mocker.patch("remo_cli.providers.aws.require_session_manager_plugin")
        mocker.patch(
            "remo_cli.providers.aws.select_ssm_instance_profile",
            return_value="remo-profile",
        )
        mocker.patch("remo_cli.providers.aws.detect_timezone", return_value="")
        mocker.patch("remo_cli.providers.aws.get_current_version", return_value="unknown")
        mocker.patch("remo_cli.providers.aws.run_playbook", return_value=0)
        mocker.patch(
            "remo_cli.providers.aws._get_running_instance",
            return_value={"InstanceId": "i-abc", "PublicIpAddress": "1.2.3.4"},
        )
        mocker.patch("remo_cli.providers.aws.save_known_host")
        reconcile = mocker.patch("remo_cli.providers.aws.print_broker_reconciliation")

        rc = providers_aws.create(name="dev1", region="us-west-2")

        assert rc == 0
        reconcile.assert_called_once_with("Reconciling")
        out = capsys.readouterr().out
        assert "Vault:    remo shell -p _remo-vault" in out

    def test_update_reports_broker_reconfiguration(self, mocker):
        mocker.patch("remo_cli.providers.aws.detect_timezone", return_value="")
        mocker.patch("remo_cli.providers.aws.get_current_version", return_value="unknown")
        mocker.patch("remo_cli.providers.aws.get_aws_region", return_value="us-west-2")
        mocker.patch(
            "remo_cli.providers.aws._get_running_instance",
            return_value={"InstanceId": "i-abc", "PublicIpAddress": "1.2.3.4"},
        )
        mocker.patch("remo_cli.providers.aws.save_known_host")
        mocker.patch("remo_cli.providers.aws.run_playbook", return_value=0)
        reconcile = mocker.patch("remo_cli.providers.aws.print_broker_reconciliation")

        rc = providers_aws.update(name="dev1")

        assert rc == 0
        reconcile.assert_called_once_with("Reconfiguring")

    def test_happy_path_async_hint(self, mocker, ec2, capsys):
        mocker.patch(
            "remo_cli.providers.aws._get_running_instance",
            return_value={"InstanceId": "i-abc"},
        )
        ec2.describe_instances.return_value = _instance_describe_response()
        ec2.describe_volumes.return_value = _volume_describe_response()
        ec2.describe_snapshots.return_value = {"Snapshots": []}
        ec2.create_snapshot.return_value = {"SnapshotId": "snap-new"}

        rc = providers_aws.snapshot_create(
            instance_name="dev1",
            snap_name="pre-x",
            description="before x",
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "will take several minutes" in out
        kwargs = ec2.create_snapshot.call_args.kwargs
        assert kwargs["VolumeId"] == "vol-old"
        # Tags include remo + name + instance
        tags = {t["Key"]: t["Value"] for t in kwargs["TagSpecifications"][0]["Tags"]}
        assert tags["remo"] == "true"
        assert tags["remo-snapshot-name"] == "pre-x"
        assert tags["remo-instance"] == "dev1"

    def test_no_running_instance_returns_1(self, mocker, ec2, capsys):
        mocker.patch(
            "remo_cli.providers.aws._get_running_instance", return_value=None
        )
        rc = providers_aws.snapshot_create(
            instance_name="dev1", snap_name="pre-x"
        )
        assert rc == 1
        ec2.create_snapshot.assert_not_called()
        err = capsys.readouterr().err
        assert "No running AWS EC2 instance" in err

    def test_duplicate_name(self, mocker, ec2, capsys):
        mocker.patch(
            "remo_cli.providers.aws._get_running_instance",
            return_value={"InstanceId": "i-abc"},
        )
        ec2.describe_instances.return_value = _instance_describe_response()
        ec2.describe_volumes.return_value = _volume_describe_response()
        ec2.describe_snapshots.return_value = _snapshot_describe_response(snap_name="pre-x")
        rc = providers_aws.snapshot_create(
            instance_name="dev1", snap_name="pre-x"
        )
        assert rc == 1
        ec2.create_snapshot.assert_not_called()
        err = capsys.readouterr().err
        assert "already exists" in err


# ---------------------------------------------------------------------------
# snapshot_restore
# ---------------------------------------------------------------------------


def _setup_restore_mocks(
    ec2,
    mocker,
    snapshot_state: str = "completed",
    cur_size: int = 20,
    snap_size: int = 20,
):
    """Wire ec2 mocks for a restore. Volume-state probes resolve quickly."""
    ec2.describe_instances.return_value = _instance_describe_response()
    ec2.describe_volumes.return_value = _volume_describe_response(size_gib=cur_size)
    ec2.describe_snapshots.return_value = _snapshot_describe_response(
        state=snapshot_state, size_gib=snap_size
    )

    # First describe_instances after stop_instances should report "stopped";
    # the original describe_instances call returned "running". Use side_effect
    # so the polling helpers see the right states.
    # The functions call describe_instances multiple times; switch to a generator.
    states_after_stop = ["stopping", "stopped"]
    states_after_start = ["pending", "running"]

    def desc_instances(InstanceIds=None, Filters=None):
        # Filters path is the initial lookup
        if Filters:
            return _instance_describe_response()
        # Polling path
        if desc_instances.queue:
            state = desc_instances.queue.pop(0)
            return _instance_describe_response(state=state)
        return _instance_describe_response(state="running")

    desc_instances.queue = states_after_stop + states_after_start
    ec2.describe_instances.side_effect = desc_instances

    vol_states = {
        "vol-old": ["detaching", "available"],
        "vol-new": ["creating", "available", "attaching", "in-use"],
    }

    def desc_volumes(VolumeIds):
        vid = VolumeIds[0]
        if vid in vol_states and vol_states[vid]:
            return {"Volumes": [{"VolumeId": vid, "State": vol_states[vid].pop(0), "Size": cur_size, "VolumeType": "gp3"}]}
        return _volume_describe_response(volume_id=vid)

    ec2.describe_volumes.side_effect = desc_volumes

    ec2.create_volume.return_value = {"VolumeId": "vol-new"}
    # Speed up polling
    mocker.patch("remo_cli.providers.aws.time.sleep", return_value=None)


class TestSnapshotRestore:
    def test_pending_snapshot_fails_fast(self, ec2, mocker, capsys):
        _setup_restore_mocks(ec2, mocker, snapshot_state="pending")
        rc = providers_aws.snapshot_restore(
            instance_name="dev1", snap_name="pre-x", auto_confirm=True
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "still pending" in err
        ec2.stop_instances.assert_not_called()

    def test_missing_snapshot(self, ec2, mocker, capsys):
        _setup_restore_mocks(ec2, mocker)
        ec2.describe_snapshots.return_value = {"Snapshots": []}
        rc = providers_aws.snapshot_restore(
            instance_name="dev1", snap_name="ghost", auto_confirm=True
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_confirm_decline(self, ec2, mocker):
        _setup_restore_mocks(ec2, mocker)
        mocker.patch("remo_cli.providers.aws.confirm", return_value=False)
        rc = providers_aws.snapshot_restore(
            instance_name="dev1", snap_name="pre-x", auto_confirm=False
        )
        assert rc == 1
        ec2.stop_instances.assert_not_called()

    def test_happy_path_volume_swap(self, ec2, mocker, capsys):
        _setup_restore_mocks(ec2, mocker)
        rc = providers_aws.snapshot_restore(
            instance_name="dev1", snap_name="pre-x", auto_confirm=True
        )
        assert rc == 0
        # Verify the swap sequence happened
        ec2.stop_instances.assert_called_once()
        ec2.detach_volume.assert_called_once_with(VolumeId="vol-old")
        ec2.create_volume.assert_called_once()
        ec2.attach_volume.assert_called_once()
        ec2.start_instances.assert_called_once()
        # Orphan tag applied to old volume (FR-030)
        ec2.create_tags.assert_called_once()
        tag_kwargs = ec2.create_tags.call_args.kwargs
        assert tag_kwargs["Resources"] == ["vol-old"]
        keys = {t["Key"] for t in tag_kwargs["Tags"]}
        assert "remo-restore-orphan" in keys
        out = capsys.readouterr().out
        assert "Restored 'pre-x'" in out
        assert "remo-restore-orphan" in out  # user told how to clean up

    def test_larger_current_volume_resize2fs_hint(self, ec2, mocker, capsys):
        # Snapshot recorded at 20 GiB, current volume already grown to 50 GiB.
        _setup_restore_mocks(ec2, mocker, cur_size=50, snap_size=20)
        rc = providers_aws.snapshot_restore(
            instance_name="dev1", snap_name="pre-x", auto_confirm=True
        )
        assert rc == 0
        # create_volume sized to the larger of (snap, current) = 50
        cv_kwargs = ec2.create_volume.call_args.kwargs
        assert cv_kwargs["Size"] == 50
        out = capsys.readouterr().out
        assert "resize2fs" in out


# ---------------------------------------------------------------------------
# snapshot_delete
# ---------------------------------------------------------------------------


class TestSnapshotDelete:
    def test_pending_snapshot_fails_fast(self, ec2, mocker, capsys):
        ec2.describe_instances.return_value = _instance_describe_response()
        ec2.describe_volumes.return_value = _volume_describe_response()
        ec2.describe_snapshots.return_value = _snapshot_describe_response(state="pending")
        rc = providers_aws.snapshot_delete(
            instance_name="dev1", snap_name="pre-x", auto_confirm=True
        )
        assert rc == 1
        ec2.delete_snapshot.assert_not_called()

    def test_missing_snapshot(self, ec2, mocker, capsys):
        ec2.describe_instances.return_value = _instance_describe_response()
        ec2.describe_volumes.return_value = _volume_describe_response()
        ec2.describe_snapshots.return_value = {"Snapshots": []}
        rc = providers_aws.snapshot_delete(
            instance_name="dev1", snap_name="ghost", auto_confirm=True
        )
        assert rc == 1
        ec2.delete_snapshot.assert_not_called()

    def test_confirm_decline(self, ec2, mocker):
        ec2.describe_instances.return_value = _instance_describe_response()
        ec2.describe_volumes.return_value = _volume_describe_response()
        ec2.describe_snapshots.return_value = _snapshot_describe_response()
        mocker.patch("remo_cli.providers.aws.confirm", return_value=False)
        rc = providers_aws.snapshot_delete(
            instance_name="dev1", snap_name="pre-x", auto_confirm=False
        )
        assert rc == 1
        ec2.delete_snapshot.assert_not_called()

    def test_happy_path(self, ec2, mocker, capsys):
        ec2.describe_instances.return_value = _instance_describe_response()
        ec2.describe_volumes.return_value = _volume_describe_response()
        ec2.describe_snapshots.return_value = _snapshot_describe_response(snap_id="snap-1")
        rc = providers_aws.snapshot_delete(
            instance_name="dev1", snap_name="pre-x", auto_confirm=True
        )
        assert rc == 0
        ec2.delete_snapshot.assert_called_once_with(SnapshotId="snap-1")


# ---------------------------------------------------------------------------
# destroy integration (FR-020 — FR-023)
# ---------------------------------------------------------------------------


def _aws_snap(name: str = "pre-x") -> Snapshot:
    return Snapshot(
        provider="aws",
        instance_name="dev1",
        name=name,
        backend_id=f"snap-{name}",
        created_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
        size_bytes=20 * 1024**3,
        description="",
        status=SnapshotStatus.AVAILABLE,
    )


class TestDestroySnapshotCleanup:
    def test_no_snapshots_no_extra_prompt(self, mocker):
        mocker.patch(
            "remo_cli.providers.aws.snapshot_list", return_value=[]
        )
        mocker.patch(
            "remo_cli.providers.aws.run_playbook", return_value=0
        )
        mock_confirm = mocker.patch(
            "remo_cli.providers.aws.confirm", return_value=True
        )
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_delete", return_value=0
        )
        mocker.patch("remo_cli.providers.aws.remove_known_host")
        rc = providers_aws.destroy(name="dev1")
        assert rc == 0
        assert mock_confirm.call_count == 1
        spy.assert_not_called()

    def test_cleanup_accepted(self, mocker):
        mocker.patch(
            "remo_cli.providers.aws.snapshot_list",
            return_value=[_aws_snap("a"), _aws_snap("b")],
        )
        mocker.patch(
            "remo_cli.providers.aws.run_playbook", return_value=0
        )
        mocker.patch("remo_cli.core.snapshot.confirm", return_value=True)
        mocker.patch("remo_cli.providers.aws.confirm", return_value=True)
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_delete", return_value=0
        )
        mocker.patch("remo_cli.providers.aws.remove_known_host")
        rc = providers_aws.destroy(name="dev1")
        assert rc == 0
        assert spy.call_count == 2

    def test_cleanup_declined_warns(self, mocker, capsys):
        mocker.patch(
            "remo_cli.providers.aws.snapshot_list",
            return_value=[_aws_snap()],
        )
        mocker.patch(
            "remo_cli.providers.aws.run_playbook", return_value=0
        )
        mocker.patch("remo_cli.core.snapshot.confirm", return_value=False)
        mocker.patch("remo_cli.providers.aws.confirm", return_value=True)
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_delete", return_value=0
        )
        mocker.patch("remo_cli.providers.aws.remove_known_host")
        rc = providers_aws.destroy(name="dev1")
        assert rc == 0
        spy.assert_not_called()
        out = capsys.readouterr().out
        assert "Snapshots will remain on AWS" in out

    def test_auto_confirm_keeps(self, mocker, capsys):
        mocker.patch(
            "remo_cli.providers.aws.snapshot_list",
            return_value=[_aws_snap()],
        )
        mocker.patch(
            "remo_cli.providers.aws.run_playbook", return_value=0
        )
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_delete", return_value=0
        )
        mock_confirm = mocker.patch("remo_cli.providers.aws.confirm")
        mocker.patch("remo_cli.providers.aws.remove_known_host")
        rc = providers_aws.destroy(name="dev1", auto_confirm=True)
        assert rc == 0
        mock_confirm.assert_not_called()
        spy.assert_not_called()
        out = capsys.readouterr().out
        assert "--yes is set" in out
