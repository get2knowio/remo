"""Tests for Incus snapshot business-logic (providers/incus.py snapshot_*)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from remo_cli.models.snapshot import Snapshot, SnapshotStatus
from remo_cli.providers import incus as providers_incus


def _completed(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


@pytest.fixture
def patch_ssh(mocker):
    """Patch the per-host SSH helper. Returns the mock so tests can set
    side effects per call."""
    return mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host",
        autospec=True,
    )


# ---------------------------------------------------------------------------
# _list_snapshots_for_container
# ---------------------------------------------------------------------------


class TestListSnapshots:
    def test_parses_json_into_snapshots(self, patch_ssh):
        patch_ssh.return_value = _completed(
            0,
            stdout=json.dumps(
                [
                    {
                        "name": "dev1/pre-upgrade",
                        "created_at": "2026-05-24T10:15:30Z",
                        "size": 1288490188,
                        "description": "before risky upgrade",
                    },
                ]
            ),
        )
        result = providers_incus._list_snapshots_for_container(  # noqa: SLF001
            host="localhost", container="dev1", user=""
        )
        assert len(result) == 1
        snap = result[0]
        assert snap.provider == "incus"
        assert snap.instance_name == "dev1"
        assert snap.name == "pre-upgrade"
        assert snap.size_bytes == 1288490188
        assert snap.description == "before risky upgrade"
        assert snap.status is SnapshotStatus.AVAILABLE

    def test_empty_list_returns_empty(self, patch_ssh):
        patch_ssh.return_value = _completed(0, stdout="[]")
        result = providers_incus._list_snapshots_for_container(  # noqa: SLF001
            host="localhost", container="dev1", user=""
        )
        assert result == []

    def test_provider_failure_raises(self, patch_ssh):
        patch_ssh.return_value = _completed(255, stderr="Host key verification failed.")
        with pytest.raises(RuntimeError, match="incus query failed"):
            providers_incus._list_snapshots_for_container(  # noqa: SLF001
                host="lab1", container="dev1", user="root"
            )


# ---------------------------------------------------------------------------
# snapshot_create
# ---------------------------------------------------------------------------


class TestSnapshotCreate:
    def test_happy_path(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[],
        )
        patch_ssh.return_value = _completed(0)
        rc = providers_incus.snapshot_create(
            container="dev1",
            host="localhost",
            user="",
            snap_name="pre-x",
            description="before x",
        )
        assert rc == 0
        # incus snapshot create was actually invoked
        cmd = patch_ssh.call_args[0][2]
        assert "incus snapshot create" in cmd
        assert "pre-x" in cmd
        assert "--description" in cmd
        out = capsys.readouterr().out
        assert "Created snapshot 'pre-x'" in out

    def test_duplicate_name_rejected(self, mocker, patch_ssh, capsys):
        existing = Snapshot(
            provider="incus",
            instance_name="dev1",
            name="pre-x",
            backend_id="dev1/pre-x",
            created_at=datetime.now(tz=timezone.utc),
            size_bytes=None,
            description="",
            status=SnapshotStatus.AVAILABLE,
        )
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[existing],
        )
        rc = providers_incus.snapshot_create(
            container="dev1",
            host="localhost",
            user="",
            snap_name="pre-x",
        )
        assert rc == 1
        # Provider call should NOT have been made
        patch_ssh.assert_not_called()
        err = capsys.readouterr().err
        assert "already exists" in err

    def test_provider_failure_returns_1(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[],
        )
        patch_ssh.return_value = _completed(1, stderr="Error: container 'dev1' not found")
        rc = providers_incus.snapshot_create(
            container="dev1",
            host="localhost",
            user="",
            snap_name="pre-x",
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "incus snapshot create failed" in err


# ---------------------------------------------------------------------------
# snapshot_restore
# ---------------------------------------------------------------------------


def _existing_snap(name: str = "pre-x", status=SnapshotStatus.AVAILABLE) -> Snapshot:
    return Snapshot(
        provider="incus",
        instance_name="dev1",
        name=name,
        backend_id=f"dev1/{name}",
        created_at=datetime.now(tz=timezone.utc),
        size_bytes=None,
        description="",
        status=status,
    )


class TestSnapshotRestore:
    def test_missing_snapshot(self, mocker, capsys):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[],
        )
        rc = providers_incus.snapshot_restore(
            container="dev1",
            host="localhost",
            user="",
            snap_name="nonexistent",
            auto_confirm=True,
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_confirm_decline_no_mutation(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[_existing_snap()],
        )
        mocker.patch("remo_cli.providers.incus.confirm", return_value=False)
        rc = providers_incus.snapshot_restore(
            container="dev1",
            host="localhost",
            user="",
            snap_name="pre-x",
            auto_confirm=False,
        )
        assert rc == 1
        # No incus restore / stop / start calls
        patch_ssh.assert_not_called()

    def test_bypass_with_auto_confirm_stop_restore_start(
        self, mocker, patch_ssh, capsys
    ):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[_existing_snap()],
        )
        mocker.patch(
            "remo_cli.providers.incus._get_container_status",
            return_value="Running",
        )
        # All three SSH calls (stop, restore, start) succeed
        patch_ssh.return_value = _completed(0)
        rc = providers_incus.snapshot_restore(
            container="dev1",
            host="localhost",
            user="",
            snap_name="pre-x",
            auto_confirm=True,
        )
        assert rc == 0
        # Verify the call sequence
        commands = [call.args[2] for call in patch_ssh.call_args_list]
        assert any("incus stop" in c for c in commands)
        assert any("incus restore" in c for c in commands)
        assert any("incus start" in c for c in commands)
        out = capsys.readouterr().out
        assert "Restored 'pre-x'" in out
        assert "remo shell dev1" in out

    def test_stopped_container_no_stop_no_start(
        self, mocker, patch_ssh, capsys
    ):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[_existing_snap()],
        )
        mocker.patch(
            "remo_cli.providers.incus._get_container_status",
            return_value="Stopped",
        )
        patch_ssh.return_value = _completed(0)
        rc = providers_incus.snapshot_restore(
            container="dev1",
            host="localhost",
            user="",
            snap_name="pre-x",
            auto_confirm=True,
        )
        assert rc == 0
        commands = [call.args[2] for call in patch_ssh.call_args_list]
        # Restore was called; stop/start were NOT called
        assert any("incus restore" in c for c in commands)
        assert not any("incus stop " in c for c in commands)
        assert not any("incus start " in c for c in commands)


# ---------------------------------------------------------------------------
# snapshot_delete
# ---------------------------------------------------------------------------


class TestSnapshotDelete:
    def test_missing_snapshot(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[],
        )
        rc = providers_incus.snapshot_delete(
            container="dev1",
            host="localhost",
            user="",
            snap_name="ghost",
            auto_confirm=True,
        )
        assert rc == 1
        patch_ssh.assert_not_called()
        err = capsys.readouterr().err
        assert "not found" in err

    def test_confirm_decline_no_mutation(self, mocker, patch_ssh):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[_existing_snap()],
        )
        mocker.patch("remo_cli.providers.incus.confirm", return_value=False)
        rc = providers_incus.snapshot_delete(
            container="dev1",
            host="localhost",
            user="",
            snap_name="pre-x",
            auto_confirm=False,
        )
        assert rc == 1
        patch_ssh.assert_not_called()

    def test_happy_path(self, mocker, patch_ssh, capsys):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[_existing_snap()],
        )
        patch_ssh.return_value = _completed(0)
        rc = providers_incus.snapshot_delete(
            container="dev1",
            host="localhost",
            user="",
            snap_name="pre-x",
            auto_confirm=True,
        )
        assert rc == 0
        cmd = patch_ssh.call_args.args[2]
        assert "incus snapshot delete" in cmd
        assert "pre-x" in cmd
