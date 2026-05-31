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
    def test_create_reports_broker_reconciliation_and_vault_summary(self, mocker, capsys):
        mocker.patch("remo_cli.providers.incus.detect_timezone", return_value="")
        mocker.patch("remo_cli.providers.incus.get_current_version", return_value="unknown")
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.incus.remove_known_host")
        mocker.patch("remo_cli.providers.incus.save_known_host")
        reconcile = mocker.patch("remo_cli.providers.incus.print_broker_reconciliation")

        rc = providers_incus.create(name="dev1")

        assert rc == 0
        reconcile.assert_called_once_with("Reconciling")
        out = capsys.readouterr().out
        assert "Vault sidecar available at: remo shell -p _remo-vault" in out

    def test_update_reports_broker_reconfiguration(self, mocker):
        mocker.patch("remo_cli.providers.incus.detect_timezone", return_value="")
        mocker.patch("remo_cli.providers.incus.get_current_version", return_value="unknown")
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.incus._resolve_container_ip", return_value="10.0.0.5")
        mocker.patch("remo_cli.providers.incus.save_known_host")
        reconcile = mocker.patch("remo_cli.providers.incus.print_broker_reconciliation")

        rc = providers_incus.update(name="dev1")

        assert rc == 0
        reconcile.assert_called_once_with("Reconfiguring")

    def test_happy_path_no_description(self, mocker, patch_ssh, capsys):
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
        )
        assert rc == 0
        # `incus snapshot create` was invoked. Only one SSH call.
        assert patch_ssh.call_count == 1
        cmd = patch_ssh.call_args[0][2]
        assert "incus snapshot create" in cmd
        assert "pre-x" in cmd
        # We must NOT pass --description as a CLI flag (incus doesn't accept it)
        assert "--description" not in cmd
        out = capsys.readouterr().out
        assert "Created snapshot 'pre-x'" in out

    def test_description_applied_via_patch(self, mocker, patch_ssh, capsys):
        """When description is provided, snapshot_create runs the create CLI
        then PATCHes the description via `incus query` (since the create
        subcommand doesn't accept --description)."""
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
            description="before risky upgrade",
        )
        assert rc == 0
        assert patch_ssh.call_count == 2
        cmds = [c.args[2] for c in patch_ssh.call_args_list]
        # First call: bare create (no --description)
        assert "incus snapshot create" in cmds[0]
        assert "--description" not in cmds[0]
        # Second call: PATCH via `incus query`
        assert "incus query" in cmds[1]
        assert "PATCH" in cmds[1]
        assert "/1.0/instances/dev1/snapshots/pre-x" in cmds[1]
        assert "before risky upgrade" in cmds[1]

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
        assert any("incus snapshot restore" in c for c in commands)
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
        assert any("incus snapshot restore" in c for c in commands)
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
        # Incus 6.x expects two positional args (container, snapshot), not
        # the deprecated "<container>/<snapshot>" combined form.
        assert "incus snapshot delete dev1 pre-x" in cmd


# ---------------------------------------------------------------------------
# destroy integration (FR-020 — FR-023)
# ---------------------------------------------------------------------------


class TestDestroySnapshotCleanup:
    def test_no_snapshots_no_extra_prompt(self, mocker):
        """FR-023: instance with no snapshots → no cleanup prompt."""
        mocker.patch(
            "remo_cli.providers.incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[],
        )
        mocker.patch(
            "remo_cli.providers.incus.run_playbook", return_value=0
        )
        mock_confirm = mocker.patch(
            "remo_cli.providers.incus.confirm", return_value=True
        )
        snapshot_delete_spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_delete", return_value=0
        )
        mocker.patch("remo_cli.providers.incus.remove_known_host")

        rc = providers_incus.destroy(name="dev1")
        assert rc == 0
        # Only the destroy-confirm prompt should be shown — no cleanup prompt.
        assert mock_confirm.call_count == 1
        snapshot_delete_spy.assert_not_called()

    def test_cleanup_accepted_deletes_each(self, mocker, capsys):
        """FR-021: user accepts cleanup → snapshot_delete called per snapshot."""
        mocker.patch(
            "remo_cli.providers.incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        snaps = [_existing_snap("a"), _existing_snap("b"), _existing_snap("c")]
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=snaps,
        )
        mocker.patch(
            "remo_cli.providers.incus.run_playbook", return_value=0
        )
        # Cleanup-confirm prompt lives in core.snapshot; destroy-confirm in
        # providers.incus.  Patch both.
        mocker.patch("remo_cli.core.snapshot.confirm", return_value=True)
        mocker.patch("remo_cli.providers.incus.confirm", return_value=True)
        spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_delete", return_value=0
        )
        mocker.patch("remo_cli.providers.incus.remove_known_host")

        rc = providers_incus.destroy(name="dev1")
        assert rc == 0
        assert spy.call_count == 3
        names_deleted = sorted(c.kwargs["snap_name"] for c in spy.call_args_list)
        assert names_deleted == ["a", "b", "c"]

    def test_cleanup_declined_warns_and_keeps(self, mocker, capsys):
        """FR-022: user declines cleanup → snapshot_delete NOT called +
        orphan-cost warning printed; instance still destroyed."""
        mocker.patch(
            "remo_cli.providers.incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[_existing_snap()],
        )
        mocker.patch(
            "remo_cli.providers.incus.run_playbook", return_value=0
        )
        mocker.patch("remo_cli.core.snapshot.confirm", return_value=False)
        mocker.patch("remo_cli.providers.incus.confirm", return_value=True)
        spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_delete", return_value=0
        )
        mocker.patch("remo_cli.providers.incus.remove_known_host")

        rc = providers_incus.destroy(name="dev1")
        assert rc == 0
        spy.assert_not_called()
        out = capsys.readouterr().out
        assert "Snapshots will remain on Incus" in out

    def test_auto_confirm_keeps_snapshots_with_warning(self, mocker, capsys):
        """auto_confirm bypasses prompts but defaults to KEEP snapshots
        (safer default — never silently destroy data)."""
        mocker.patch(
            "remo_cli.providers.incus._lookup_incus_host",
            return_value=("localhost", ""),
        )
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[_existing_snap()],
        )
        mocker.patch(
            "remo_cli.providers.incus.run_playbook", return_value=0
        )
        spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_delete", return_value=0
        )
        mock_confirm = mocker.patch("remo_cli.providers.incus.confirm")
        mocker.patch("remo_cli.providers.incus.remove_known_host")

        rc = providers_incus.destroy(name="dev1", auto_confirm=True)
        assert rc == 0
        # No prompts at all
        mock_confirm.assert_not_called()
        # Snapshots NOT deleted
        spy.assert_not_called()
        # User warned
        out = capsys.readouterr().out
        assert "--yes is set" in out
        assert "keeping the 1 snapshot(s)" in out
