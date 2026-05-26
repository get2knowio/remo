"""US1 T045: assert `remo incus add-node` is idempotent and writes 0600 nodes.yml."""

import os
import stat
import subprocess

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.incus import incus


def _fake_ssh_ok(host, user, command):  # noqa: ARG001
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="OK\n", stderr="")


def test_add_node_writes_nodes_yml_0600(tmp_config_dir, mocker):
    mocker.patch("remo_cli.providers.incus._ssh_run_on_incus_host", side_effect=_fake_ssh_ok)

    runner = CliRunner()
    result = runner.invoke(
        incus,
        [
            "add-node",
            "ws-01",
            "--host", "192.168.4.10",
            "--ssh-user", "incusadmin",
            "--admin-sa-fnox-key", "incus_ws_01_admin_sa",
        ],
    )
    assert result.exit_code == 0, result.output
    path = tmp_config_dir / "nodes.yml"
    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_add_node_idempotent_same_fields(tmp_config_dir, mocker):
    mocker.patch("remo_cli.providers.incus._ssh_run_on_incus_host", side_effect=_fake_ssh_ok)
    runner = CliRunner()
    args = [
        "add-node",
        "ws-01",
        "--host", "192.168.4.10",
        "--ssh-user", "incusadmin",
        "--admin-sa-fnox-key", "incus_ws_01_admin_sa",
    ]
    r1 = runner.invoke(incus, args)
    assert r1.exit_code == 0
    r2 = runner.invoke(incus, args)
    assert r2.exit_code == 0


def test_add_node_conflict_exits_6(tmp_config_dir, mocker):
    mocker.patch("remo_cli.providers.incus._ssh_run_on_incus_host", side_effect=_fake_ssh_ok)
    runner = CliRunner()
    r1 = runner.invoke(
        incus,
        ["add-node", "ws-01", "--host", "1.1.1.1",
         "--ssh-user", "incusadmin",
         "--admin-sa-fnox-key", "incus_ws_01_admin_sa"],
    )
    assert r1.exit_code == 0
    r2 = runner.invoke(
        incus,
        ["add-node", "ws-01", "--host", "2.2.2.2",
         "--ssh-user", "incusadmin",
         "--admin-sa-fnox-key", "incus_ws_01_admin_sa"],
    )
    assert r2.exit_code == 6


def test_add_node_ssh_failure_exits_nonzero(tmp_config_dir, mocker):
    fail = subprocess.CompletedProcess(args=[], returncode=255, stdout="", stderr="ssh: connect failed")
    mocker.patch("remo_cli.providers.incus._ssh_run_on_incus_host", return_value=fail)
    runner = CliRunner()
    r = runner.invoke(
        incus,
        ["add-node", "ws-01", "--host", "1.1.1.1",
         "--ssh-user", "incusadmin",
         "--admin-sa-fnox-key", "incus_ws_01_admin_sa"],
    )
    assert r.exit_code != 0


def test_add_node_helper_install_does_not_touch_helper_file(tmp_config_dir, mocker):
    """Regression for finding 10: pre-creating an empty
    /usr/local/libexec/remo-broker-tokens shadowed the role's copy task
    (force: false), silently breaking per-developer token management.
    The helper-install SSH command must NOT touch/chmod that file — only
    the parent dir + per-developer token dir."""
    captured: list[str] = []

    def _capture(host, user, command):  # noqa: ARG001
        captured.append(command)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="OK\n", stderr="")

    mocker.patch("remo_cli.providers.incus._ssh_run_on_incus_host", side_effect=_capture)
    runner = CliRunner()
    r = runner.invoke(
        incus,
        ["add-node", "ws-01", "--host", "1.1.1.1",
         "--ssh-user", "incusadmin",
         "--admin-sa-fnox-key", "incus_ws_01_admin_sa"],
    )
    assert r.exit_code == 0, r.output
    assert captured, "expected helper-install SSH command"
    cmd = captured[0]
    assert "touch /usr/local/libexec/remo-broker-tokens" not in cmd
    assert "chmod 0755 /usr/local/libexec/remo-broker-tokens" not in cmd
    assert "install -d -m 0755 /usr/local/libexec" in cmd
    assert "/var/lib/remo-broker/instance-tokens/" in cmd
    assert "echo OK" in cmd
