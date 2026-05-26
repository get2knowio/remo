"""US1 T043: assert the Incus bind-mount uses `lxc config device add ... readonly=true`."""

import subprocess

import pytest

from remo_cli.providers import incus


def test_bind_mount_lxc_config_device_add(mocker):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    run_mock = mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", return_value=completed
    )

    incus._bind_mount_token(  # noqa: SLF001
        host="192.168.4.10",
        user="incusadmin",
        instance="lxc-1",
        token_path="/var/lib/remo-broker/instance-tokens/alice/lxc-1",
    )

    run_mock.assert_called_once()
    cmd = run_mock.call_args[0][2]
    assert "lxc config device add" in cmd
    assert "remo-broker-token" in cmd
    assert "disk" in cmd
    assert "readonly=true" in cmd
    assert "/etc/remo-broker/bootstrap-token" in cmd


def test_bind_mount_idempotent_on_already_exists(mocker):
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="Error: device already exists"
    )
    mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", return_value=completed
    )
    # Should not raise.
    incus._bind_mount_token(  # noqa: SLF001
        host="h", user="u", instance="lxc-1", token_path="/tmp/t"
    )


def test_bind_mount_propagates_other_failures(mocker):
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="permission denied"
    )
    mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", return_value=completed
    )
    with pytest.raises(RuntimeError, match="lxc config device add"):
        incus._bind_mount_token(  # noqa: SLF001
            host="h", user="u", instance="lxc-1", token_path="/tmp/t"
        )
