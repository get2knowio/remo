"""Tests for Hetzner snapshot CLI commands (cli/providers/hetzner.py snapshot)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.hetzner import hetzner


@pytest.fixture
def runner():
    return CliRunner()


class TestSnapshotCreateCLI:
    def test_create_help_mentions_managed_vault_reconciliation(self, runner):
        result = runner.invoke(hetzner, ["create", "--help"])
        assert result.exit_code == 0
        assert "managed broker sidecar" in result.output
        assert "_remo-vault" in result.output
        assert "still reconcile" in result.output

    def test_default_name(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_create", return_value=0
        )
        result = runner.invoke(hetzner, ["snapshot", "create", "dev1"])
        assert result.exit_code == 0
        kwargs = spy.call_args.kwargs
        assert kwargs["snap_name"].startswith("remo-")
        assert kwargs["server_name"] == "dev1"

    def test_explicit_name_and_description(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_create", return_value=0
        )
        result = runner.invoke(
            hetzner,
            ["snapshot", "create", "dev1", "--name", "pre-x", "--description", "x"],
        )
        assert result.exit_code == 0
        kwargs = spy.call_args.kwargs
        assert kwargs["snap_name"] == "pre-x"
        assert kwargs["description"] == "x"

    def test_invalid_name(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_create", return_value=0
        )
        result = runner.invoke(
            hetzner, ["snapshot", "create", "dev1", "--name", "bad name!"]
        )
        assert result.exit_code == 2
        spy.assert_not_called()


class TestSnapshotRestoreCLI:
    def test_yes_short_flag(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_restore", return_value=0
        )
        result = runner.invoke(hetzner, ["snapshot", "restore", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is True

    def test_default_does_not_bypass(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_restore", return_value=0
        )
        result = runner.invoke(hetzner, ["snapshot", "restore", "dev1", "pre-x"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is False


class TestSnapshotListCLI:
    def test_with_instance_renders_table_with_status(self, runner, mocker):
        from datetime import datetime, timezone

        from remo_cli.models.snapshot import Snapshot, SnapshotStatus

        mocker.patch(
            "remo_cli.providers.hetzner.snapshot_list",
            return_value=[
                Snapshot(
                    provider="hetzner",
                    instance_name="dev1",
                    name="pre-x",
                    backend_id="100",
                    created_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
                    size_bytes=20 * 1024**3,
                    description="",
                    status=SnapshotStatus.AVAILABLE,
                )
            ],
        )
        result = runner.invoke(hetzner, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0
        assert "pre-x" in result.output
        # Hetzner gets the STATUS column (async creation)
        assert "STATUS" in result.output

    def test_empty(self, runner, mocker):
        mocker.patch(
            "remo_cli.providers.hetzner.snapshot_list", return_value=[]
        )
        result = runner.invoke(hetzner, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0
        assert "No snapshots found for instance 'dev1'" in result.output

    def test_provider_failure(self, runner, mocker):
        mocker.patch(
            "remo_cli.providers.hetzner.snapshot_list",
            side_effect=RuntimeError("No Hetzner server found named 'dev1'"),
        )
        result = runner.invoke(hetzner, ["snapshot", "list", "dev1"])
        assert result.exit_code == 1
        assert "No Hetzner server found" in result.output


class TestSnapshotDeleteCLI:
    def test_yes_bypasses(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_delete", return_value=0
        )
        result = runner.invoke(hetzner, ["snapshot", "delete", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is True

    def test_default_does_not_bypass(self, runner, mocker):
        spy = mocker.patch(
            "remo_cli.providers.hetzner.snapshot_delete", return_value=0
        )
        result = runner.invoke(hetzner, ["snapshot", "delete", "dev1", "pre-x"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is False
