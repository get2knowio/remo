"""Tests for AWS snapshot CLI commands (cli/providers/aws.py snapshot)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.aws import aws


@pytest.fixture
def runner():
    return CliRunner()


class TestSnapshotCreateCLI:
    def test_default_name(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_create", return_value=0
        )
        result = runner.invoke(aws, ["snapshot", "create", "dev1"])
        assert result.exit_code == 0
        kwargs = spy.call_args.kwargs
        assert kwargs["snap_name"].startswith("remo-")
        assert kwargs["instance_name"] == "dev1"

    def test_explicit_name_and_description_and_region(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_create", return_value=0
        )
        result = runner.invoke(
            aws,
            ["snapshot", "create", "dev1", "--name", "pre-x", "--description", "x", "--region", "us-west-2"],
        )
        assert result.exit_code == 0
        kwargs = spy.call_args.kwargs
        assert kwargs["snap_name"] == "pre-x"
        assert kwargs["description"] == "x"
        assert kwargs["region"] == "us-west-2"

    def test_invalid_name(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_create", return_value=0
        )
        result = runner.invoke(aws, ["snapshot", "create", "dev1", "--name", "bad name!"])
        assert result.exit_code == 2
        spy.assert_not_called()


class TestSnapshotRestoreCLI:
    def test_yes_short_flag(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_restore", return_value=0
        )
        result = runner.invoke(aws, ["snapshot", "restore", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is True

    def test_default_does_not_bypass(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_restore", return_value=0
        )
        result = runner.invoke(aws, ["snapshot", "restore", "dev1", "pre-x"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is False


class TestSnapshotListCLI:
    def test_with_instance_renders_table_with_status(self, runner, mocker):
        from datetime import datetime, timezone

        from remo_cli.models.snapshot import Snapshot, SnapshotStatus

        mocker.patch(
            "remo_cli.providers.aws.snapshot_list",
            return_value=[
                Snapshot(
                    provider="aws",
                    instance_name="dev1",
                    name="pre-x",
                    backend_id="snap-1",
                    created_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
                    size_bytes=20 * 1024**3,
                    description="",
                    status=SnapshotStatus.PENDING,
                )
            ],
        )
        result = runner.invoke(aws, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0
        assert "pre-x" in result.output
        assert "STATUS" in result.output  # AWS gets the status column
        assert "pending" in result.output

    def test_empty(self, runner, mocker):
        mocker.patch("remo_cli.providers.aws.snapshot_list", return_value=[])
        result = runner.invoke(aws, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0
        assert "No snapshots found for instance 'dev1'" in result.output

    def test_provider_failure(self, runner, mocker):
        mocker.patch(
            "remo_cli.providers.aws.snapshot_list",
            side_effect=RuntimeError("No AWS EC2 instance found"),
        )
        result = runner.invoke(aws, ["snapshot", "list", "dev1"])
        assert result.exit_code == 1
        assert "No AWS EC2 instance found" in result.output


class TestSnapshotDeleteCLI:
    def test_yes_bypasses(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_delete", return_value=0
        )
        result = runner.invoke(aws, ["snapshot", "delete", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is True

    def test_default_does_not_bypass(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.aws.snapshot_delete", return_value=0
        )
        result = runner.invoke(aws, ["snapshot", "delete", "dev1", "pre-x"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is False
