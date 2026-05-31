"""Tests for Proxmox snapshot business-logic (providers/proxmox.py snapshot_*)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from remo_cli.models.snapshot import Snapshot, SnapshotStatus
from remo_cli.providers import proxmox as providers_proxmox


def _completed(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


@pytest.fixture
def patch_ssh(mocker):
    return mocker.patch(
        "remo_cli.providers.proxmox._ssh_run",
        autospec=True,
    )


# ---------------------------------------------------------------------------
# _parse_pct_conf_snapshots — pure parser, no mocking needed
# ---------------------------------------------------------------------------


_PCT_CONF_WITH_SNAPSHOTS = """\
arch: amd64
cores: 4
hostname: dev1
memory: 4096
rootfs: local-zfs:subvol-100-disk-0,size=20G

[pre-upgrade]
snaptime: 1748080530
description: before risky upgrade
arch: amd64
rootfs: local-zfs:subvol-100-disk-0,size=20G

[pre-experiment]
snaptime: 1748166900
arch: amd64
rootfs: local-zfs:subvol-100-disk-0,size=20G
"""


class TestParsePctConfSnapshots:
    def test_extracts_snapshots_with_metadata(self):
        result = providers_proxmox._parse_pct_conf_snapshots(  # noqa: SLF001
            _PCT_CONF_WITH_SNAPSHOTS, "dev1"
        )
        assert len(result) == 2
        first = result[0]
        assert first.name == "pre-upgrade"
        assert first.description == "before risky upgrade"
        assert first.status is SnapshotStatus.AVAILABLE
        assert first.instance_name == "dev1"
        assert first.size_bytes is None
        # snaptime: 1748080530 → 2025-05-24T11:55:30 UTC (give or take)
        assert first.created_at.tzinfo is not None

        second = result[1]
        assert second.name == "pre-experiment"
        assert second.description == ""

    def test_no_snapshots_returns_empty(self):
        result = providers_proxmox._parse_pct_conf_snapshots(  # noqa: SLF001
            "arch: amd64\nrootfs: local-zfs:foo,size=20G\n", "dev1"
        )
        assert result == []


# ---------------------------------------------------------------------------
# _detect_snapshot_capable_storage
# ---------------------------------------------------------------------------


class TestDetectStorage:
    def test_zfspool_is_supported(self, patch_ssh):
        patch_ssh.side_effect = [
            _completed(0, stdout="rootfs: local-zfs:subvol-100-disk-0,size=20G\n"),
            _completed(0, stdout="Name             Type     Status\nlocal-zfs        zfspool  active\n"),
        ]
        ok, kind = providers_proxmox._detect_snapshot_capable_storage(  # noqa: SLF001
            "lab1", "root", "100"
        )
        assert ok is True
        assert kind == "zfspool"

    def test_dir_is_not_supported(self, patch_ssh):
        patch_ssh.side_effect = [
            _completed(0, stdout="rootfs: local:100/vm-100-disk-0.raw,size=20G\n"),
            _completed(0, stdout="local            dir      active\n"),
        ]
        ok, kind = providers_proxmox._detect_snapshot_capable_storage(  # noqa: SLF001
            "lab1", "root", "100"
        )
        assert ok is False
        assert kind == "dir"

    def test_lvmthin_is_supported(self, patch_ssh):
        patch_ssh.side_effect = [
            _completed(0, stdout="rootfs: local-lvm:vm-100-disk-0,size=20G\n"),
            _completed(0, stdout="local-lvm        lvmthin  active\n"),
        ]
        ok, kind = providers_proxmox._detect_snapshot_capable_storage(  # noqa: SLF001
            "lab1", "root", "100"
        )
        assert ok is True
        assert kind == "lvmthin"

    def test_pct_config_failure(self, patch_ssh):
        patch_ssh.return_value = _completed(2, stderr="vm 999 does not exist")
        ok, kind = providers_proxmox._detect_snapshot_capable_storage(  # noqa: SLF001
            "lab1", "root", "999"
        )
        assert ok is False
        assert kind == ""


# ---------------------------------------------------------------------------
# snapshot_create
# ---------------------------------------------------------------------------


class TestSnapshotCreate:
    def test_create_reports_broker_reconciliation_and_vault_summary(self, mocker, capsys):
        mocker.patch("remo_cli.providers.proxmox.detect_timezone", return_value="")
        mocker.patch("remo_cli.providers.proxmox.get_current_version", return_value="unknown")
        mocker.patch("remo_cli.providers.proxmox.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.proxmox.remove_known_host")
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        mocker.patch("remo_cli.providers.proxmox._resolve_container_ip", return_value="10.0.0.6")
        mocker.patch("remo_cli.providers.proxmox.save_known_host")
        reconcile = mocker.patch("remo_cli.providers.proxmox.print_broker_reconciliation")

        rc = providers_proxmox.create(name="dev1", host="lab1")

        assert rc == 0
        reconcile.assert_called_once_with("Reconciling")
        out = capsys.readouterr().out
        assert "Vault sidecar available at: remo shell -p _remo-vault" in out

    def test_update_reports_broker_reconfiguration(self, mocker):
        mocker.patch("remo_cli.providers.proxmox.detect_timezone", return_value="")
        mocker.patch("remo_cli.providers.proxmox.get_current_version", return_value="unknown")
        mocker.patch("remo_cli.providers.proxmox.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.proxmox._lookup_proxmox_host", return_value=("lab1", "root", "100"))
        mocker.patch("remo_cli.providers.proxmox._resolve_container_ip", return_value="10.0.0.6")
        mocker.patch("remo_cli.providers.proxmox.save_known_host")
        reconcile = mocker.patch("remo_cli.providers.proxmox.print_broker_reconciliation")

        rc = providers_proxmox.update(name="dev1")

        assert rc == 0
        reconcile.assert_called_once_with("Reconfiguring")

    def test_unsupported_storage_rejected(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.proxmox._detect_snapshot_capable_storage",
            return_value=(False, "dir"),
        )
        rc = providers_proxmox.snapshot_create(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="pre-x",
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "'dir'" in err
        assert "does not support snapshots" in err
        # Should NOT have run pct snapshot
        patch_ssh.assert_not_called()

    def test_duplicate_name_rejected(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.proxmox._detect_snapshot_capable_storage",
            return_value=(True, "zfspool"),
        )
        existing = Snapshot(
            provider="proxmox",
            instance_name="dev1",
            name="pre-x",
            backend_id="pre-x",
            created_at=datetime.now(tz=timezone.utc),
            size_bytes=None,
            description="",
            status=SnapshotStatus.AVAILABLE,
        )
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[existing],
        )
        rc = providers_proxmox.snapshot_create(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="pre-x",
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err
        patch_ssh.assert_not_called()

    def test_happy_path(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.proxmox._detect_snapshot_capable_storage",
            return_value=(True, "zfspool"),
        )
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[],
        )
        patch_ssh.return_value = _completed(0)
        rc = providers_proxmox.snapshot_create(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="pre-x",
            description="before x",
        )
        assert rc == 0
        cmd = patch_ssh.call_args[0][2]
        assert "pct snapshot" in cmd
        assert "100" in cmd
        assert "pre-x" in cmd
        assert "--description" in cmd
        out = capsys.readouterr().out
        assert "Created snapshot 'pre-x'" in out


# ---------------------------------------------------------------------------
# snapshot_restore
# ---------------------------------------------------------------------------


def _existing_snap(name: str = "pre-x", status=SnapshotStatus.AVAILABLE) -> Snapshot:
    return Snapshot(
        provider="proxmox",
        instance_name="dev1",
        name=name,
        backend_id=name,
        created_at=datetime.now(tz=timezone.utc),
        size_bytes=None,
        description="",
        status=status,
    )


class TestSnapshotRestore:
    def test_missing_snapshot(self, mocker, capsys):
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[],
        )
        rc = providers_proxmox.snapshot_restore(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="ghost",
            auto_confirm=True,
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_confirm_decline(self, mocker, patch_ssh):
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[_existing_snap()],
        )
        mocker.patch("remo_cli.providers.proxmox.confirm", return_value=False)
        rc = providers_proxmox.snapshot_restore(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="pre-x",
        )
        assert rc == 1
        patch_ssh.assert_not_called()

    def test_running_container_restarted_after_rollback(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[_existing_snap()],
        )
        mocker.patch(
            "remo_cli.providers.proxmox._get_pct_status",
            return_value="running",
        )
        patch_ssh.return_value = _completed(0)
        rc = providers_proxmox.snapshot_restore(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="pre-x",
            auto_confirm=True,
        )
        assert rc == 0
        commands = [c.args[2] for c in patch_ssh.call_args_list]
        assert any("pct rollback" in c for c in commands)
        assert any("pct start" in c for c in commands)
        out = capsys.readouterr().out
        assert "Restored 'pre-x'" in out

    def test_stopped_container_no_restart(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[_existing_snap()],
        )
        mocker.patch(
            "remo_cli.providers.proxmox._get_pct_status",
            return_value="stopped",
        )
        patch_ssh.return_value = _completed(0)
        rc = providers_proxmox.snapshot_restore(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="pre-x",
            auto_confirm=True,
        )
        assert rc == 0
        commands = [c.args[2] for c in patch_ssh.call_args_list]
        assert any("pct rollback" in c for c in commands)
        assert not any("pct start" in c for c in commands)


class TestSnapshotDelete:
    def test_missing_snapshot(self, mocker, patch_ssh):
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid", return_value=[]
        )
        rc = providers_proxmox.snapshot_delete(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="ghost",
            auto_confirm=True,
        )
        assert rc == 1
        patch_ssh.assert_not_called()

    def test_confirm_decline(self, mocker, patch_ssh):
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[_existing_snap()],
        )
        mocker.patch("remo_cli.providers.proxmox.confirm", return_value=False)
        rc = providers_proxmox.snapshot_delete(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="pre-x",
            auto_confirm=False,
        )
        assert rc == 1
        patch_ssh.assert_not_called()

    def test_happy_path(self, mocker, patch_ssh):
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[_existing_snap()],
        )
        patch_ssh.return_value = _completed(0)
        rc = providers_proxmox.snapshot_delete(
            container="dev1",
            host="lab1",
            user="root",
            vmid="100",
            snap_name="pre-x",
            auto_confirm=True,
        )
        assert rc == 0
        cmd = patch_ssh.call_args.args[2]
        assert "pct delsnapshot" in cmd
        assert "pre-x" in cmd


# ---------------------------------------------------------------------------
# destroy integration (FR-020 — FR-023)
# ---------------------------------------------------------------------------


class TestDestroySnapshotCleanup:
    def test_no_snapshots_no_extra_prompt(self, mocker):
        mocker.patch(
            "remo_cli.providers.proxmox._lookup_proxmox_host",
            return_value=("lab1", "root", "100"),
        )
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[],
        )
        mocker.patch(
            "remo_cli.providers.proxmox.run_playbook", return_value=0
        )
        mock_confirm = mocker.patch(
            "remo_cli.providers.proxmox.confirm", return_value=True
        )
        spy = mocker.patch(
            "remo_cli.providers.proxmox.snapshot_delete", return_value=0
        )
        mocker.patch("remo_cli.providers.proxmox.remove_known_host")
        rc = providers_proxmox.destroy(name="dev1")
        assert rc == 0
        assert mock_confirm.call_count == 1
        spy.assert_not_called()

    def test_cleanup_accepted(self, mocker):
        mocker.patch(
            "remo_cli.providers.proxmox._lookup_proxmox_host",
            return_value=("lab1", "root", "100"),
        )
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[_existing_snap("a"), _existing_snap("b")],
        )
        mocker.patch(
            "remo_cli.providers.proxmox.run_playbook", return_value=0
        )
        mocker.patch("remo_cli.core.snapshot.confirm", return_value=True)
        mocker.patch("remo_cli.providers.proxmox.confirm", return_value=True)
        spy = mocker.patch(
            "remo_cli.providers.proxmox.snapshot_delete", return_value=0
        )
        mocker.patch("remo_cli.providers.proxmox.remove_known_host")
        rc = providers_proxmox.destroy(name="dev1")
        assert rc == 0
        assert spy.call_count == 2

    def test_cleanup_declined_warns(self, mocker, capsys):
        mocker.patch(
            "remo_cli.providers.proxmox._lookup_proxmox_host",
            return_value=("lab1", "root", "100"),
        )
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[_existing_snap()],
        )
        mocker.patch(
            "remo_cli.providers.proxmox.run_playbook", return_value=0
        )
        mocker.patch("remo_cli.core.snapshot.confirm", return_value=False)
        mocker.patch("remo_cli.providers.proxmox.confirm", return_value=True)
        spy = mocker.patch(
            "remo_cli.providers.proxmox.snapshot_delete", return_value=0
        )
        mocker.patch("remo_cli.providers.proxmox.remove_known_host")
        rc = providers_proxmox.destroy(name="dev1")
        assert rc == 0
        spy.assert_not_called()
        out = capsys.readouterr().out
        assert "Snapshots will remain on Proxmox" in out

    def test_auto_confirm_keeps(self, mocker, capsys):
        mocker.patch(
            "remo_cli.providers.proxmox._lookup_proxmox_host",
            return_value=("lab1", "root", "100"),
        )
        mocker.patch(
            "remo_cli.providers.proxmox._list_snapshots_for_vmid",
            return_value=[_existing_snap()],
        )
        mocker.patch(
            "remo_cli.providers.proxmox.run_playbook", return_value=0
        )
        spy = mocker.patch(
            "remo_cli.providers.proxmox.snapshot_delete", return_value=0
        )
        mock_confirm = mocker.patch("remo_cli.providers.proxmox.confirm")
        mocker.patch("remo_cli.providers.proxmox.remove_known_host")
        rc = providers_proxmox.destroy(name="dev1", auto_confirm=True)
        assert rc == 0
        mock_confirm.assert_not_called()
        spy.assert_not_called()
        out = capsys.readouterr().out
        assert "--yes is set" in out
