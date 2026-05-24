"""Tests for Proxmox snapshot CLI commands (cli/providers/proxmox.py snapshot)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.proxmox import proxmox


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def stub_lookup(mocker):
    """Resolve `_lookup_proxmox_host` to a triple as if the registry had dev1."""
    return mocker.patch(
        "remo_cli.cli.providers.proxmox.providers_proxmox._lookup_proxmox_host",
        return_value=("lab1", "root", "100"),
    )


class TestSnapshotCreateCLI:
    def test_default_name(self, runner, mocker, stub_lookup):
        spy = mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox.snapshot_create",
            return_value=0,
        )
        result = runner.invoke(proxmox, ["snapshot", "create", "dev1"])
        assert result.exit_code == 0
        kwargs = spy.call_args.kwargs
        assert kwargs["snap_name"].startswith("remo-")
        assert kwargs["host"] == "lab1"
        assert kwargs["user"] == "root"
        assert kwargs["vmid"] == "100"

    def test_explicit_name_and_description(self, runner, mocker, stub_lookup):
        spy = mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox.snapshot_create",
            return_value=0,
        )
        result = runner.invoke(
            proxmox,
            ["snapshot", "create", "dev1", "--name", "pre-x", "--description", "before x"],
        )
        assert result.exit_code == 0
        kwargs = spy.call_args.kwargs
        assert kwargs["snap_name"] == "pre-x"
        assert kwargs["description"] == "before x"

    def test_invalid_name(self, runner, mocker, stub_lookup):
        spy = mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox.snapshot_create",
            return_value=0,
        )
        result = runner.invoke(proxmox, ["snapshot", "create", "dev1", "--name", "bad name!"])
        assert result.exit_code == 2
        spy.assert_not_called()

    def test_missing_registry_entry(self, runner, mocker):
        mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox._lookup_proxmox_host",
            return_value=("", "", ""),
        )
        spy = mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox.snapshot_create",
            return_value=0,
        )
        result = runner.invoke(proxmox, ["snapshot", "create", "ghost"])
        assert result.exit_code == 1
        spy.assert_not_called()
        assert "No Proxmox registry entry" in result.output


class TestSnapshotRestoreCLI:
    def test_yes_short_flag(self, runner, mocker, stub_lookup):
        spy = mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox.snapshot_restore",
            return_value=0,
        )
        result = runner.invoke(proxmox, ["snapshot", "restore", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is True

    def test_default_does_not_bypass(self, runner, mocker, stub_lookup):
        spy = mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox.snapshot_restore",
            return_value=0,
        )
        result = runner.invoke(proxmox, ["snapshot", "restore", "dev1", "pre-x"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is False
