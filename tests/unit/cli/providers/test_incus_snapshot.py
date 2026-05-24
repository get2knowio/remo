"""Tests for Incus snapshot CLI commands (cli/providers/incus.py snapshot)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.incus import incus


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Click-level parsing & dispatch
# ---------------------------------------------------------------------------


class TestSnapshotCreateCLI:
    def test_default_name_factory_generates_remo_prefix(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        spy = mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus.snapshot_create",
            return_value=0,
        )
        result = runner.invoke(incus, ["snapshot", "create", "dev1"])
        assert result.exit_code == 0
        spy.assert_called_once()
        kwargs = spy.call_args.kwargs
        assert kwargs["snap_name"].startswith("remo-"), kwargs["snap_name"]
        assert kwargs["description"] == ""

    def test_explicit_name_and_description(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        spy = mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus.snapshot_create",
            return_value=0,
        )
        result = runner.invoke(
            incus,
            ["snapshot", "create", "dev1", "--name", "pre-x", "--description", "before x"],
        )
        assert result.exit_code == 0
        kwargs = spy.call_args.kwargs
        assert kwargs["snap_name"] == "pre-x"
        assert kwargs["description"] == "before x"

    def test_invalid_name_rejected_with_exit_2(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        spy = mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus.snapshot_create",
            return_value=0,
        )
        result = runner.invoke(incus, ["snapshot", "create", "dev1", "--name", "bad name!"])
        assert result.exit_code == 2
        spy.assert_not_called()


class TestSnapshotRestoreCLI:
    def test_yes_short_flag_bypasses(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        spy = mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus.snapshot_restore",
            return_value=0,
        )
        result = runner.invoke(incus, ["snapshot", "restore", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is True

    def test_yes_long_flag_bypasses(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        spy = mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus.snapshot_restore",
            return_value=0,
        )
        result = runner.invoke(incus, ["snapshot", "restore", "dev1", "pre-x", "--yes"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is True

    def test_default_does_not_bypass(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        spy = mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus.snapshot_restore",
            return_value=0,
        )
        result = runner.invoke(incus, ["snapshot", "restore", "dev1", "pre-x"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is False

    def test_propagates_provider_exit_code(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus.snapshot_restore",
            return_value=1,
        )
        result = runner.invoke(incus, ["snapshot", "restore", "dev1", "pre-x", "-y"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# snapshot list
# ---------------------------------------------------------------------------


class TestSnapshotListCLI:
    def test_with_instance_renders_table(self, runner, mocker):
        from datetime import datetime, timezone

        from remo_cli.models.snapshot import Snapshot, SnapshotStatus

        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._list_snapshots_for_container",
            return_value=[
                Snapshot(
                    provider="incus",
                    instance_name="dev1",
                    name="pre-x",
                    backend_id="dev1/pre-x",
                    created_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
                    size_bytes=int(1.2 * 1024**3),
                    description="before x",
                    status=SnapshotStatus.AVAILABLE,
                )
            ],
        )
        result = runner.invoke(incus, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0
        assert "INSTANCE" in result.output
        assert "pre-x" in result.output
        # Incus list: status column omitted
        assert "STATUS" not in result.output

    def test_empty_snapshots(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._list_snapshots_for_container",
            return_value=[],
        )
        result = runner.invoke(incus, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0
        assert "No snapshots found for instance 'dev1'" in result.output

    def test_provider_failure_exits_1(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("lab1", "root"),
        )
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._list_snapshots_for_container",
            side_effect=RuntimeError("Host key verification failed."),
        )
        result = runner.invoke(incus, ["snapshot", "list", "dev1"])
        assert result.exit_code == 1
        assert "Host key verification failed" in result.output


class TestSnapshotDeleteCLI:
    def test_yes_bypasses(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        spy = mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus.snapshot_delete",
            return_value=0,
        )
        result = runner.invoke(incus, ["snapshot", "delete", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is True

    def test_default_does_not_bypass(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        spy = mocker.patch(
            "remo_cli.cli.providers.incus.providers_incus.snapshot_delete",
            return_value=0,
        )
        result = runner.invoke(incus, ["snapshot", "delete", "dev1", "pre-x"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is False
