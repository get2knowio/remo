"""Tests for the generated Incus snapshot CLI commands (cli/providers/factory.py
``snapshot`` subgroup, built from ``providers/incus_descriptor.py``).

These exercise CLI-layer wiring only (argument parsing, exit codes, output
formatting): the entry-based provider functions (``snapshot_create`` etc.)
are mocked at the module boundary, and a registry entry is seeded so the
``INSTANCE`` argument resolves (018-provider-abstraction factory contract).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.factory import build_provider_group
from remo_cli.core.errors import OperationFailedError
from remo_cli.core.provider_registry import get_descriptor
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot, SnapshotStatus
from tests.conftest import seed_registry

incus = build_provider_group(get_descriptor("incus"))


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def incus_entry(tmp_config_dir):
    """Seed a registry entry so `dev1` resolves for snapshot commands."""
    entry = KnownHost(type="incus", name="nuc/dev1", host="localhost", user="remo")
    seed_registry(tmp_config_dir, [entry])
    return entry


def _existing_snap(name: str = "pre-x", status: SnapshotStatus = SnapshotStatus.AVAILABLE) -> Snapshot:
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


# ---------------------------------------------------------------------------
# Click-level parsing & dispatch
# ---------------------------------------------------------------------------


class TestSnapshotCreateCLI:
    def test_default_name_factory_generates_remo_prefix(self, runner, mocker, incus_entry):
        spy = mocker.patch("remo_cli.providers.incus.snapshot_create")
        result = runner.invoke(incus, ["snapshot", "create", "dev1"])
        assert result.exit_code == 0, result.output
        spy.assert_called_once()
        entry, snap_name = spy.call_args.args
        assert entry.name == "nuc/dev1"
        assert snap_name.startswith("remo-"), snap_name
        assert spy.call_args.kwargs["description"] == ""

    def test_explicit_name_and_description(self, runner, mocker, incus_entry):
        spy = mocker.patch("remo_cli.providers.incus.snapshot_create")
        result = runner.invoke(
            incus,
            ["snapshot", "create", "dev1", "--name", "pre-x", "--description", "before x"],
        )
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert snap_name == "pre-x"
        assert spy.call_args.kwargs["description"] == "before x"

    def test_invalid_name_rejected_with_exit_2(self, runner, mocker, incus_entry):
        spy = mocker.patch("remo_cli.providers.incus.snapshot_create")
        result = runner.invoke(incus, ["snapshot", "create", "dev1", "--name", "bad name!"])
        assert result.exit_code == 2
        spy.assert_not_called()

    def test_missing_registry_entry(self, runner, mocker, tmp_config_dir):
        spy = mocker.patch("remo_cli.providers.incus.snapshot_create")
        result = runner.invoke(incus, ["snapshot", "create", "ghost"])
        assert result.exit_code == 1
        spy.assert_not_called()
        assert "No incus registry entry" in result.output


class TestSnapshotRestoreCLI:
    def test_yes_short_flag_bypasses(self, runner, mocker, incus_entry):
        spy = mocker.patch("remo_cli.providers.incus.snapshot_restore")
        result = runner.invoke(incus, ["snapshot", "restore", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert entry.name == "nuc/dev1"
        assert snap_name == "pre-x"

    def test_yes_long_flag_bypasses(self, runner, mocker, incus_entry):
        spy = mocker.patch("remo_cli.providers.incus.snapshot_restore")
        result = runner.invoke(incus, ["snapshot", "restore", "dev1", "pre-x", "--yes"])
        assert result.exit_code == 0, result.output
        spy.assert_called_once()

    def test_declining_confirmation_aborts_with_exit_3(self, runner, mocker, incus_entry):
        """CLI-layer confirmation prompt (no --yes, no stdin input -> declined)."""
        spy = mocker.patch("remo_cli.providers.incus.snapshot_restore")
        result = runner.invoke(incus, ["snapshot", "restore", "dev1", "pre-x"])
        assert result.exit_code == 3
        assert "Aborted." in result.output
        spy.assert_not_called()

    def test_propagates_provider_failure_exits_1(self, runner, mocker, incus_entry):
        mocker.patch(
            "remo_cli.providers.incus.snapshot_restore",
            side_effect=OperationFailedError("restore failed"),
        )
        result = runner.invoke(incus, ["snapshot", "restore", "dev1", "pre-x", "-y"])
        assert result.exit_code == 1
        assert "restore failed" in result.output


# ---------------------------------------------------------------------------
# snapshot list
# ---------------------------------------------------------------------------


class TestSnapshotListCLI:
    def test_with_instance_renders_table(self, runner, mocker, incus_entry):
        from datetime import datetime, timezone

        from remo_cli.models.snapshot import Snapshot, SnapshotStatus

        mocker.patch(
            "remo_cli.providers.incus.snapshot_list",
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
        assert result.exit_code == 0, result.output
        assert "INSTANCE" in result.output
        assert "pre-x" in result.output
        # Incus list: status column omitted
        assert "STATUS" not in result.output

    def test_empty_snapshots(self, runner, mocker, incus_entry):
        mocker.patch("remo_cli.providers.incus.snapshot_list", return_value=[])
        result = runner.invoke(incus, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0, result.output
        assert "No snapshots found for instance 'dev1'" in result.output

    def test_provider_failure_exits_1(self, runner, mocker, incus_entry):
        mocker.patch(
            "remo_cli.providers.incus.snapshot_list",
            side_effect=OperationFailedError("Host key verification failed."),
        )
        result = runner.invoke(incus, ["snapshot", "list", "dev1"])
        assert result.exit_code == 1
        assert "Host key verification failed" in result.output


class TestDestroyCLI:
    """CLI-layer coverage of the shared destroy template wired through the
    Incus descriptor (018-provider-abstraction T038): factory._build_destroy
    -> core/lifecycle.run_destroy -> providers.incus.teardown/snapshot_list/
    snapshot_delete. Generic ordering/decline/removal-failure behavior is
    covered once, provider-agnostically, in tests/unit/core/test_lifecycle.py;
    this class only proves the Incus wiring (including the added-SSH-host
    guard and the container_name/preserve_data extra_vars) actually reaches
    that template.
    """

    def test_full_flow_confirms_and_tears_down(self, runner, mocker, incus_entry):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container", return_value=[]
        )
        run_playbook = mocker.patch(
            "remo_cli.providers.incus.run_playbook", return_value=0
        )
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=True)
        remove_known_host = mocker.patch("remo_cli.core.lifecycle.remove_known_host")

        result = runner.invoke(incus, ["destroy", "--name", "dev1"])

        assert result.exit_code == 0, result.output
        run_playbook.assert_called_once()
        assert run_playbook.call_args.args[0] == "incus_teardown.yml"
        extra_vars = run_playbook.call_args.args[1]
        assert "container_name=dev1" in extra_vars
        assert "preserve_data=true" in extra_vars  # --remove-storage not set
        remove_known_host.assert_called_once_with("incus", "nuc/dev1")

    def test_remove_storage_flag_sets_preserve_data_false(self, runner, mocker, incus_entry):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container", return_value=[]
        )
        run_playbook = mocker.patch(
            "remo_cli.providers.incus.run_playbook", return_value=0
        )
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=True)
        mocker.patch("remo_cli.core.lifecycle.remove_known_host")

        result = runner.invoke(
            incus, ["destroy", "--name", "dev1", "--remove-storage", "-y"]
        )

        assert result.exit_code == 0, result.output
        extra_vars = run_playbook.call_args.args[1]
        assert "preserve_data=false" in extra_vars

    def test_declining_confirmation_aborts_with_exit_3(self, runner, mocker, incus_entry):
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container", return_value=[]
        )
        run_playbook = mocker.patch("remo_cli.providers.incus.run_playbook")
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=False)

        result = runner.invoke(incus, ["destroy", "--name", "dev1"])

        assert result.exit_code == 3
        assert "Aborted." in result.output
        run_playbook.assert_not_called()

    def test_added_ssh_host_guard_blocks(self, runner, tmp_config_dir):
        seed_registry(
            tmp_config_dir,
            [KnownHost(type="ssh", name="dev1", host="5.6.7.8", user="remo")],
        )

        result = runner.invoke(incus, ["destroy", "--name", "dev1", "-y"])

        assert result.exit_code == 1
        assert "manually-registered SSH host" in result.output
        assert "remo remove" in result.output

    def test_playbook_failure_still_removes_from_registry(self, runner, mocker, incus_entry):
        from remo_cli.core.known_hosts import get_known_hosts

        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container", return_value=[]
        )
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=1)
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=True)

        result = runner.invoke(incus, ["destroy", "--name", "dev1"])

        assert result.exit_code == 1
        assert "Failed to destroy Incus container 'dev1'" in result.output
        # Best-effort registry removal still runs even though teardown failed.
        assert get_known_hosts(type_filter="incus") == []


class TestDestroySnapshotCleanupCLI:
    """FR-020 through FR-023: destroy surfaces existing snapshots and offers
    cleanup before the destructive prompt. Exercised via the full CLI path
    (``remo incus destroy``) since ``providers.incus.destroy()`` was deleted
    (018-provider-abstraction T038) -- the cleanup hook
    (``core.snapshot.handle_destroy_snapshot_cleanup``) is invoked by the
    shared ``core.lifecycle.run_destroy`` template now, not provider code.
    """

    def test_no_snapshots_no_extra_prompt(self, runner, mocker, incus_entry):
        """FR-023: instance with no snapshots -> no cleanup prompt."""
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container", return_value=[]
        )
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        mock_confirm = mocker.patch("remo_cli.core.lifecycle.confirm", return_value=True)
        spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_delete_legacy", return_value=0
        )

        result = runner.invoke(incus, ["destroy", "--name", "dev1"])

        assert result.exit_code == 0, result.output
        # Only the destroy-confirm prompt should be shown -- no cleanup prompt.
        assert mock_confirm.call_count == 1
        spy.assert_not_called()

    def test_cleanup_accepted_deletes_each(self, runner, mocker, incus_entry):
        """FR-021: user accepts cleanup -> snapshot_delete called per snapshot."""
        snaps = [_existing_snap("a"), _existing_snap("b"), _existing_snap("c")]
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=snaps,
        )
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        # Cleanup-confirm prompt lives in core.snapshot; destroy-confirm in
        # core.lifecycle. Patch both.
        mocker.patch("remo_cli.core.snapshot.confirm", return_value=True)
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=True)
        spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_delete_legacy", return_value=0
        )

        result = runner.invoke(incus, ["destroy", "--name", "dev1"])

        assert result.exit_code == 0, result.output
        assert spy.call_count == 3
        names_deleted = sorted(c.kwargs["snap_name"] for c in spy.call_args_list)
        assert names_deleted == ["a", "b", "c"]

    def test_cleanup_declined_warns_and_keeps(self, runner, mocker, incus_entry):
        """FR-022: user declines cleanup -> snapshot_delete NOT called +
        orphan-cost warning printed; instance still destroyed."""
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[_existing_snap()],
        )
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        mocker.patch("remo_cli.core.snapshot.confirm", return_value=False)
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=True)
        spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_delete_legacy", return_value=0
        )

        result = runner.invoke(incus, ["destroy", "--name", "dev1"])

        assert result.exit_code == 0, result.output
        spy.assert_not_called()
        assert "Snapshots will remain on Incus" in result.output

    def test_auto_confirm_keeps_snapshots_with_warning(self, runner, mocker, incus_entry):
        """auto_confirm bypasses prompts but defaults to KEEP snapshots
        (safer default -- never silently destroy data)."""
        mocker.patch(
            "remo_cli.providers.incus._list_snapshots_for_container",
            return_value=[_existing_snap()],
        )
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        spy = mocker.patch(
            "remo_cli.providers.incus.snapshot_delete_legacy", return_value=0
        )
        mock_confirm = mocker.patch("remo_cli.core.lifecycle.confirm")

        result = runner.invoke(incus, ["destroy", "--name", "dev1", "-y"])

        assert result.exit_code == 0, result.output
        # No destroy-confirm prompt at all
        mock_confirm.assert_not_called()
        # Snapshots NOT deleted
        spy.assert_not_called()
        # User warned
        assert "--yes is set" in result.output
        assert "keeping the 1 snapshot(s)" in result.output


class TestSnapshotDeleteCLI:
    def test_yes_bypasses(self, runner, mocker, incus_entry):
        spy = mocker.patch("remo_cli.providers.incus.snapshot_delete")
        result = runner.invoke(incus, ["snapshot", "delete", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert entry.name == "nuc/dev1"
        assert snap_name == "pre-x"

    def test_declining_confirmation_aborts_with_exit_3(self, runner, mocker, incus_entry):
        spy = mocker.patch("remo_cli.providers.incus.snapshot_delete")
        result = runner.invoke(incus, ["snapshot", "delete", "dev1", "pre-x"])
        assert result.exit_code == 3
        assert "Aborted." in result.output
        spy.assert_not_called()
