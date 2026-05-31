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
    def test_create_help_mentions_managed_vault_reconciliation(self, runner):
        result = runner.invoke(proxmox, ["create", "--help"])
        assert result.exit_code == 0
        assert "managed broker sidecar" in result.output
        assert "_remo-vault" in result.output
        assert "still reconcile" in result.output

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


class TestSnapshotListCLI:
    def test_with_instance_renders_table(self, runner, mocker, stub_lookup):
        from datetime import datetime, timezone

        from remo_cli.models.snapshot import Snapshot, SnapshotStatus

        mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox._list_snapshots_for_vmid",
            return_value=[
                Snapshot(
                    provider="proxmox",
                    instance_name="dev1",
                    name="pre-x",
                    backend_id="pre-x",
                    created_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
                    size_bytes=None,
                    description="",
                    status=SnapshotStatus.AVAILABLE,
                )
            ],
        )
        result = runner.invoke(proxmox, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0
        assert "pre-x" in result.output
        assert "STATUS" not in result.output  # Proxmox: no status column

    def test_empty(self, runner, mocker, stub_lookup):
        mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox._list_snapshots_for_vmid",
            return_value=[],
        )
        result = runner.invoke(proxmox, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0
        assert "No snapshots found for instance 'dev1'" in result.output

    def test_provider_failure(self, runner, mocker, stub_lookup):
        mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox._list_snapshots_for_vmid",
            side_effect=RuntimeError("ssh failed"),
        )
        result = runner.invoke(proxmox, ["snapshot", "list", "dev1"])
        assert result.exit_code == 1


class TestSnapshotDeleteCLI:
    def test_yes_bypasses(self, runner, mocker, stub_lookup):
        spy = mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox.snapshot_delete",
            return_value=0,
        )
        result = runner.invoke(proxmox, ["snapshot", "delete", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is True

    def test_default_does_not_bypass(self, runner, mocker, stub_lookup):
        spy = mocker.patch(
            "remo_cli.cli.providers.proxmox.providers_proxmox.snapshot_delete",
            return_value=0,
        )
        result = runner.invoke(proxmox, ["snapshot", "delete", "dev1", "pre-x"])
        assert result.exit_code == 0
        assert spy.call_args.kwargs["auto_confirm"] is False
