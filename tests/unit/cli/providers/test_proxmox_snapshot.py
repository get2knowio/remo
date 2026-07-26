"""Tests for the generated Proxmox snapshot CLI commands (cli/providers/factory.py
``snapshot`` subgroup, built from ``providers/proxmox_descriptor.py``).

These exercise CLI-layer wiring only (argument parsing, exit codes, output
formatting): the entry-based provider functions (``snapshot_create`` etc.)
are mocked at the module boundary, and a registry entry is seeded so the
``INSTANCE`` argument resolves (018-provider-abstraction factory contract).
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.factory import build_provider_group
from remo_cli.core.errors import OperationFailedError
from remo_cli.core.provider_registry import get_descriptor
from remo_cli.models.host import KnownHost
from tests.conftest import seed_registry

proxmox = build_provider_group(get_descriptor("proxmox"))


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def proxmox_entry(tmp_config_dir):
    """Seed a registry entry so `dev1` resolves for snapshot commands."""
    entry = KnownHost(
        type="proxmox", name="lab1/dev1", host="lab1", user="root", instance_id="100"
    )
    seed_registry(tmp_config_dir, [entry])
    return entry


class TestSnapshotCreateCLI:
    def test_default_name(self, runner, mocker, proxmox_entry):
        spy = mocker.patch("remo_cli.providers.proxmox.snapshot_create")
        result = runner.invoke(proxmox, ["snapshot", "create", "dev1"])
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert snap_name.startswith("remo-")
        assert entry.name == "lab1/dev1"
        assert entry.host == "lab1"
        assert entry.user == "root"
        assert entry.instance_id == "100"

    def test_explicit_name_and_description(self, runner, mocker, proxmox_entry):
        spy = mocker.patch("remo_cli.providers.proxmox.snapshot_create")
        result = runner.invoke(
            proxmox,
            ["snapshot", "create", "dev1", "--name", "pre-x", "--description", "before x"],
        )
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert snap_name == "pre-x"
        assert spy.call_args.kwargs["description"] == "before x"

    def test_invalid_name(self, runner, mocker, proxmox_entry):
        spy = mocker.patch("remo_cli.providers.proxmox.snapshot_create")
        result = runner.invoke(proxmox, ["snapshot", "create", "dev1", "--name", "bad name!"])
        assert result.exit_code == 2
        spy.assert_not_called()

    def test_missing_registry_entry(self, runner, mocker, tmp_config_dir):
        spy = mocker.patch("remo_cli.providers.proxmox.snapshot_create")
        result = runner.invoke(proxmox, ["snapshot", "create", "ghost"])
        assert result.exit_code == 1
        spy.assert_not_called()
        assert "No proxmox registry entry" in result.output


class TestSnapshotRestoreCLI:
    def test_yes_short_flag(self, runner, mocker, proxmox_entry):
        spy = mocker.patch("remo_cli.providers.proxmox.snapshot_restore")
        result = runner.invoke(proxmox, ["snapshot", "restore", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert entry.name == "lab1/dev1"
        assert snap_name == "pre-x"

    def test_declining_confirmation_aborts_with_exit_3(self, runner, mocker, proxmox_entry):
        spy = mocker.patch("remo_cli.providers.proxmox.snapshot_restore")
        result = runner.invoke(proxmox, ["snapshot", "restore", "dev1", "pre-x"])
        assert result.exit_code == 3
        assert "Aborted." in result.output
        spy.assert_not_called()


class TestSnapshotListCLI:
    def test_with_instance_renders_table(self, runner, mocker, proxmox_entry):
        from datetime import datetime, timezone

        from remo_cli.models.snapshot import Snapshot, SnapshotStatus

        mocker.patch(
            "remo_cli.providers.proxmox.snapshot_list",
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
        assert result.exit_code == 0, result.output
        assert "pre-x" in result.output
        assert "STATUS" not in result.output  # Proxmox: no status column

    def test_empty(self, runner, mocker, proxmox_entry):
        mocker.patch("remo_cli.providers.proxmox.snapshot_list", return_value=[])
        result = runner.invoke(proxmox, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0, result.output
        assert "No snapshots found for instance 'dev1'" in result.output

    def test_provider_failure(self, runner, mocker, proxmox_entry):
        mocker.patch(
            "remo_cli.providers.proxmox.snapshot_list",
            side_effect=OperationFailedError("ssh failed"),
        )
        result = runner.invoke(proxmox, ["snapshot", "list", "dev1"])
        assert result.exit_code == 1
        assert "ssh failed" in result.output


class TestDestroyCLI:
    """CLI-layer coverage of the shared destroy template wired through the
    Proxmox descriptor (018-provider-abstraction T038): factory._build_destroy
    -> core/lifecycle.run_destroy -> providers.proxmox.teardown/snapshot_list/
    snapshot_delete. Generic ordering/decline/removal-failure behavior is
    covered once, provider-agnostically, in tests/unit/core/test_lifecycle.py;
    this class only proves the Proxmox wiring (including the added-SSH-host
    guard) actually reaches that template.
    """

    def test_full_flow_confirms_and_tears_down(self, runner, mocker, proxmox_entry):
        mocker.patch("remo_cli.providers.proxmox.snapshot_list", return_value=[])
        run_playbook = mocker.patch(
            "remo_cli.providers.proxmox.run_playbook", return_value=0
        )
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=True)
        remove_known_host = mocker.patch("remo_cli.core.lifecycle.remove_known_host")

        result = runner.invoke(proxmox, ["destroy", "--name", "dev1"])

        assert result.exit_code == 0, result.output
        run_playbook.assert_called_once()
        assert run_playbook.call_args.args[0] == "proxmox_teardown.yml"
        remove_known_host.assert_called_once_with("proxmox", "lab1/dev1")

    def test_declining_confirmation_aborts_with_exit_3(self, runner, mocker, proxmox_entry):
        mocker.patch("remo_cli.providers.proxmox.snapshot_list", return_value=[])
        run_playbook = mocker.patch("remo_cli.providers.proxmox.run_playbook")
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=False)

        result = runner.invoke(proxmox, ["destroy", "--name", "dev1"])

        assert result.exit_code == 3
        assert "Aborted." in result.output
        run_playbook.assert_not_called()

    def test_added_ssh_host_guard_blocks(self, runner, tmp_config_dir):
        seed_registry(
            tmp_config_dir,
            [KnownHost(type="ssh", name="dev1", host="5.6.7.8", user="remo")],
        )

        result = runner.invoke(proxmox, ["destroy", "--name", "dev1", "-y"])

        assert result.exit_code == 1
        assert "manually-registered SSH host" in result.output
        assert "remo remove" in result.output

    def test_purge_flag_forwarded(self, runner, mocker, proxmox_entry):
        mocker.patch("remo_cli.providers.proxmox.snapshot_list", return_value=[])
        run_playbook = mocker.patch(
            "remo_cli.providers.proxmox.run_playbook", return_value=0
        )
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=True)
        mocker.patch("remo_cli.core.lifecycle.remove_known_host")

        result = runner.invoke(proxmox, ["destroy", "--name", "dev1", "--purge"])

        assert result.exit_code == 0, result.output
        assert "purge=true" in run_playbook.call_args.args[1]


class TestSnapshotDeleteCLI:
    def test_yes_bypasses(self, runner, mocker, proxmox_entry):
        spy = mocker.patch("remo_cli.providers.proxmox.snapshot_delete")
        result = runner.invoke(proxmox, ["snapshot", "delete", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert entry.name == "lab1/dev1"
        assert snap_name == "pre-x"

    def test_declining_confirmation_aborts_with_exit_3(self, runner, mocker, proxmox_entry):
        spy = mocker.patch("remo_cli.providers.proxmox.snapshot_delete")
        result = runner.invoke(proxmox, ["snapshot", "delete", "dev1", "pre-x"])
        assert result.exit_code == 3
        assert "Aborted." in result.output
        spy.assert_not_called()
