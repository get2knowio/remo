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
