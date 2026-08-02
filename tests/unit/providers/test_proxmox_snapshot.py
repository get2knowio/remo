"""Tests for Proxmox snapshot business-logic (providers/proxmox.py snapshot_*)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.models.host import KnownHost
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
    def test_unsupported_storage_rejected(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.proxmox._detect_snapshot_capable_storage",
            return_value=(False, "dir"),
        )
        rc = providers_proxmox.snapshot_create_legacy(
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
        rc = providers_proxmox.snapshot_create_legacy(
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
        rc = providers_proxmox.snapshot_create_legacy(
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
        rc = providers_proxmox.snapshot_restore_legacy(
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
        rc = providers_proxmox.snapshot_restore_legacy(
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
        rc = providers_proxmox.snapshot_restore_legacy(
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
        rc = providers_proxmox.snapshot_restore_legacy(
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
        rc = providers_proxmox.snapshot_delete_legacy(
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
        rc = providers_proxmox.snapshot_delete_legacy(
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
        rc = providers_proxmox.snapshot_delete_legacy(
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
# teardown (018-provider-abstraction T038 — destroy template)
#
# Guard, snapshot pre-cleanup, confirmation, and registry removal moved to
# the shared ``core.lifecycle.run_destroy`` template; that generic ordering
# is covered once, provider-agnostically, in tests/unit/core/test_lifecycle.py.
# This class only proves ``teardown()`` -- the provider-specific step -- does
# the right Proxmox-flavored thing: derives node/vmid/user from the entry,
# builds the expected extra_vars, and translates a nonzero rc / an
# undetermined host into the right typed error (R-A3).
# ---------------------------------------------------------------------------


class TestTeardown:
    def test_builds_expected_extra_vars(self, mocker):
        entry = KnownHost(
            type="proxmox", name="lab1/dev1", host="lab1", user="remo",
            instance_id="100", region="root",
        )
        run_playbook = mocker.patch(
            "remo_cli.providers.proxmox.run_playbook", return_value=0
        )
        providers_proxmox.teardown(entry, purge=True)
        args, kwargs = run_playbook.call_args
        assert args[0] == "proxmox_teardown.yml"
        extra_vars = args[1]
        assert "-e" in extra_vars and "container_name=dev1" in extra_vars
        assert "purge=true" in extra_vars
        assert "container_vmid=100" in extra_vars
        assert "-i" in extra_vars and "lab1," in extra_vars
        assert "target_hosts=all" in extra_vars
        assert "proxmox_host_user=root" in extra_vars

    def test_omits_vmid_when_unknown(self, mocker):
        entry = KnownHost(
            type="proxmox", name="lab1/dev1", host="lab1", user="remo",
            instance_id="", region="root",
        )
        run_playbook = mocker.patch(
            "remo_cli.providers.proxmox.run_playbook", return_value=0
        )
        providers_proxmox.teardown(entry)
        extra_vars = run_playbook.call_args.args[1]
        assert not any(v.startswith("container_vmid=") for v in extra_vars)
        assert "purge=false" in extra_vars

    def test_empty_region_leaves_the_node_login_to_ssh_config(self, mocker):
        """#106: an empty region means no `--host-user` was ever recorded, so
        the node login is whatever ssh_config picks — which is exactly how
        create reached the node. Forcing root here would connect as someone
        create never used."""
        entry = KnownHost(
            type="proxmox", name="lab1/dev1", host="lab1", user="remo",
            instance_id="100", region="",
        )
        run_playbook = mocker.patch(
            "remo_cli.providers.proxmox.run_playbook", return_value=0
        )
        providers_proxmox.teardown(entry)
        extra_vars = run_playbook.call_args.args[1]
        assert not any(v.startswith("proxmox_host_user=") for v in extra_vars)

    def test_recorded_region_is_passed_through_as_the_node_login(self, mocker):
        entry = KnownHost(
            type="proxmox", name="lab1/dev1", host="lab1", user="remo",
            instance_id="100", region="paul",
        )
        run_playbook = mocker.patch(
            "remo_cli.providers.proxmox.run_playbook", return_value=0
        )
        providers_proxmox.teardown(entry)
        assert "proxmox_host_user=paul" in run_playbook.call_args.args[1]

    def test_nonzero_rc_raises_operation_failed_error(self, mocker):
        entry = KnownHost(
            type="proxmox", name="lab1/dev1", host="lab1", user="remo",
            instance_id="100", region="root",
        )
        mocker.patch("remo_cli.providers.proxmox.run_playbook", return_value=1)
        with pytest.raises(OperationFailedError, match="rc=1"):
            providers_proxmox.teardown(entry)

    def test_undetermined_host_raises_precondition_error(self, mocker):
        # An entry whose name carries no "host/container" separator (e.g. a
        # bare stub built without --host) leaves teardown unable to locate
        # the Proxmox node.
        entry = KnownHost(type="proxmox", name="dev1", host="", user="remo")
        run_playbook = mocker.patch("remo_cli.providers.proxmox.run_playbook")
        with pytest.raises(PreconditionError, match="could not be determined"):
            providers_proxmox.teardown(entry)
        run_playbook.assert_not_called()

    def test_ignores_forwarded_host_and_user_kwargs(self, mocker):
        # The CLI factory forwards the destroy command's --host/--user
        # destroy-options through to teardown() alongside --purge; they're
        # already baked into *entry* by the CLI's entry-resolution step, so
        # teardown must accept-and-ignore them rather than crash (R-A2).
        entry = KnownHost(
            type="proxmox", name="lab1/dev1", host="lab1", user="remo",
            instance_id="100", region="root",
        )
        mocker.patch("remo_cli.providers.proxmox.run_playbook", return_value=0)
        providers_proxmox.teardown(entry, host="ignored-host", user="ignored-user")
