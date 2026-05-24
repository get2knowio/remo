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
