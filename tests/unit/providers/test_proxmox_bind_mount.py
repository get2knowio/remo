"""US1 T044: assert the Proxmox bind-mount uses `pct set -mp0 ...,ro=1`."""

import subprocess

import pytest

from remo_cli.providers import proxmox


def test_bind_mount_pct_set_ro(mocker):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    run_mock = mocker.patch(
        "remo_cli.providers.proxmox._ssh_run", return_value=completed
    )

    proxmox._bind_mount_token(  # noqa: SLF001
        host="10.0.0.42",
        user="root",
        vmid="200",
        token_path="/var/lib/remo-broker/instance-tokens/alice/200",
    )

    run_mock.assert_called_once()
    cmd = run_mock.call_args[0][2]
    assert "pct set" in cmd
    assert "-mp0" in cmd
    assert "ro=1" in cmd
    assert "mp=/etc/remo-broker/bootstrap-token" in cmd


def test_bind_mount_idempotent_on_already_exists(mocker):
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="parameter already exists"
    )
    mocker.patch("remo_cli.providers.proxmox._ssh_run", return_value=completed)
    proxmox._bind_mount_token(  # noqa: SLF001
        host="h", user="root", vmid="1", token_path="/tmp/t"
    )


def test_bind_mount_propagates_failures(mocker):
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="connection refused"
    )
    mocker.patch("remo_cli.providers.proxmox._ssh_run", return_value=completed)
    with pytest.raises(RuntimeError, match="pct set"):
        proxmox._bind_mount_token(  # noqa: SLF001
            host="h", user="root", vmid="1", token_path="/tmp/t"
        )
