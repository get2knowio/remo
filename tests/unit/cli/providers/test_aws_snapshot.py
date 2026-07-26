"""Tests for the generated AWS snapshot CLI commands (cli/providers/factory.py
``snapshot`` subgroup, built from ``providers/aws_descriptor.py``).

These exercise CLI-layer wiring only (argument parsing, exit codes, output
formatting): the entry-based provider functions (``snapshot_create`` etc.)
are mocked at the module boundary, and a registry entry is seeded so the
``INSTANCE`` argument resolves (018-provider-abstraction factory contract).
Note the module-boundary mock here never touches ``boto3``, so the ``aws``
extra need not be installed to run these tests.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.factory import build_provider_group
from remo_cli.core.errors import OperationFailedError
from remo_cli.core.provider_registry import get_descriptor
from remo_cli.models.host import KnownHost
from tests.conftest import seed_registry

aws = build_provider_group(get_descriptor("aws"))


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def aws_entry(tmp_config_dir):
    """Seed a registry entry so `dev1` resolves for snapshot commands."""
    entry = KnownHost(
        type="aws",
        name="dev1",
        host="203.0.113.7",
        user="remo",
        instance_id="i-0abc123def",
        access_mode="ssm",
        region="us-east-1",
    )
    seed_registry(tmp_config_dir, [entry])
    return entry


class TestSnapshotCreateCLI:
    def test_default_name(self, runner, mocker, aws_entry):
        spy = mocker.patch("remo_cli.providers.aws.snapshot_create")
        result = runner.invoke(aws, ["snapshot", "create", "dev1"])
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert snap_name.startswith("remo-")
        assert entry.name == "dev1"

    def test_explicit_name_and_description_and_region(self, runner, mocker, aws_entry):
        spy = mocker.patch("remo_cli.providers.aws.snapshot_create")
        result = runner.invoke(
            aws,
            [
                "snapshot",
                "create",
                "dev1",
                "--name",
                "pre-x",
                "--description",
                "x",
                "--region",
                "us-west-2",
            ],
        )
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert snap_name == "pre-x"
        assert spy.call_args.kwargs["description"] == "x"
        # --region overrides the registry entry's region before dispatch.
        assert entry.region == "us-west-2"

    def test_invalid_name(self, runner, mocker, aws_entry):
        spy = mocker.patch("remo_cli.providers.aws.snapshot_create")
        result = runner.invoke(aws, ["snapshot", "create", "dev1", "--name", "bad name!"])
        assert result.exit_code == 2
        spy.assert_not_called()


class TestSnapshotRestoreCLI:
    def test_yes_short_flag(self, runner, mocker, aws_entry):
        spy = mocker.patch("remo_cli.providers.aws.snapshot_restore")
        result = runner.invoke(aws, ["snapshot", "restore", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert entry.name == "dev1"
        assert snap_name == "pre-x"

    def test_declining_confirmation_aborts_with_exit_3(self, runner, mocker, aws_entry):
        spy = mocker.patch("remo_cli.providers.aws.snapshot_restore")
        result = runner.invoke(aws, ["snapshot", "restore", "dev1", "pre-x"])
        assert result.exit_code == 3
        assert "Aborted." in result.output
        spy.assert_not_called()


class TestSnapshotListCLI:
    def test_with_instance_renders_table_with_status(self, runner, mocker, aws_entry):
        from datetime import datetime, timezone

        from remo_cli.models.snapshot import Snapshot, SnapshotStatus

        mocker.patch(
            "remo_cli.providers.aws.snapshot_list",
            return_value=[
                Snapshot(
                    provider="aws",
                    instance_name="dev1",
                    name="pre-x",
                    backend_id="snap-1",
                    created_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
                    size_bytes=20 * 1024**3,
                    description="",
                    status=SnapshotStatus.PENDING,
                )
            ],
        )
        result = runner.invoke(aws, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0, result.output
        assert "pre-x" in result.output
        assert "STATUS" in result.output  # AWS gets the status column
        assert "pending" in result.output

    def test_empty(self, runner, mocker, aws_entry):
        mocker.patch("remo_cli.providers.aws.snapshot_list", return_value=[])
        result = runner.invoke(aws, ["snapshot", "list", "dev1"])
        assert result.exit_code == 0, result.output
        assert "No snapshots found for instance 'dev1'" in result.output

    def test_provider_failure(self, runner, mocker, aws_entry):
        mocker.patch(
            "remo_cli.providers.aws.snapshot_list",
            side_effect=OperationFailedError("No AWS EC2 instance found"),
        )
        result = runner.invoke(aws, ["snapshot", "list", "dev1"])
        assert result.exit_code == 1
        assert "No AWS EC2 instance found" in result.output


class TestDestroyCLI:
    """CLI-layer coverage of the shared destroy template wired through the
    AWS descriptor (018-provider-abstraction T038): factory._build_destroy ->
    core/lifecycle.run_destroy -> providers.aws.teardown/snapshot_list/
    snapshot_delete. Generic ordering/decline/removal-failure behavior is
    covered once, provider-agnostically, in tests/unit/core/test_lifecycle.py;
    this class only proves the AWS wiring (including the added-SSH-host
    guard) actually reaches that template.
    """

    def test_full_flow_confirms_and_tears_down(self, runner, mocker, aws_entry):
        mocker.patch("remo_cli.providers.aws.snapshot_list_legacy", return_value=[])
        run_playbook = mocker.patch(
            "remo_cli.providers.aws.run_playbook", return_value=0
        )
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=True)
        remove_known_host = mocker.patch("remo_cli.core.lifecycle.remove_known_host")

        result = runner.invoke(aws, ["destroy", "--name", "dev1"])

        assert result.exit_code == 0, result.output
        run_playbook.assert_called_once()
        assert run_playbook.call_args.args[0] == "aws_teardown.yml"
        remove_known_host.assert_called_once_with("aws", "dev1")

    def test_declining_confirmation_aborts_with_exit_3(self, runner, mocker, aws_entry):
        mocker.patch("remo_cli.providers.aws.snapshot_list_legacy", return_value=[])
        run_playbook = mocker.patch("remo_cli.providers.aws.run_playbook")
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=False)

        result = runner.invoke(aws, ["destroy", "--name", "dev1"])

        assert result.exit_code == 3
        assert "Aborted." in result.output
        run_playbook.assert_not_called()

    def test_added_ssh_host_guard_blocks(self, runner, tmp_config_dir):
        seed_registry(
            tmp_config_dir,
            [KnownHost(type="ssh", name="dev1", host="5.6.7.8", user="remo")],
        )

        result = runner.invoke(aws, ["destroy", "--name", "dev1", "-y"])

        assert result.exit_code == 1
        assert "manually-registered SSH host" in result.output
        assert "remo remove" in result.output

    def test_remove_storage_flag_forwarded(self, runner, mocker, aws_entry):
        mocker.patch("remo_cli.providers.aws.snapshot_list_legacy", return_value=[])
        run_playbook = mocker.patch(
            "remo_cli.providers.aws.run_playbook", return_value=0
        )

        result = runner.invoke(
            aws, ["destroy", "--name", "dev1", "-y", "--remove-storage"]
        )

        assert result.exit_code == 0, result.output
        extra_vars = run_playbook.call_args.args[1]
        assert "remove_storage=true" in extra_vars


class TestSnapshotDeleteCLI:
    def test_yes_bypasses(self, runner, mocker, aws_entry):
        spy = mocker.patch("remo_cli.providers.aws.snapshot_delete")
        result = runner.invoke(aws, ["snapshot", "delete", "dev1", "pre-x", "-y"])
        assert result.exit_code == 0, result.output
        entry, snap_name = spy.call_args.args
        assert entry.name == "dev1"
        assert snap_name == "pre-x"

    def test_declining_confirmation_aborts_with_exit_3(self, runner, mocker, aws_entry):
        spy = mocker.patch("remo_cli.providers.aws.snapshot_delete")
        result = runner.invoke(aws, ["snapshot", "delete", "dev1", "pre-x"])
        assert result.exit_code == 3
        assert "Aborted." in result.output
        spy.assert_not_called()
