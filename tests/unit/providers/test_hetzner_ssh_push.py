"""US1 T041: assert the bootstrap token is pushed via SSH stdin (never argv)."""

import subprocess

import pytest

from remo_cli.providers import hetzner


def test_push_pipes_token_on_stdin(mocker):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    run_mock = mocker.patch("subprocess.run", return_value=completed)

    hetzner._push_bootstrap_token("1.2.3.4", "SECRET_TOKEN_VALUE")  # noqa: SLF001

    run_mock.assert_called_once()
    call_args = run_mock.call_args
    ssh_argv = call_args[0][0]
    # Token must be on stdin, not in argv
    assert "SECRET_TOKEN_VALUE" not in " ".join(ssh_argv)
    assert call_args.kwargs.get("input") == "SECRET_TOKEN_VALUE"
    # The remote command must use stdin install
    remote_cmd = ssh_argv[-1]
    assert "install" in remote_cmd
    assert "-m 0400" in remote_cmd
    assert "/dev/stdin" in remote_cmd
    assert "/etc/remo-broker/bootstrap-token" in remote_cmd


def test_push_uses_root_user_by_default(mocker):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    run_mock = mocker.patch("subprocess.run", return_value=completed)

    hetzner._push_bootstrap_token("1.2.3.4", "x")  # noqa: SLF001

    ssh_argv = run_mock.call_args[0][0]
    # SSH target is root@1.2.3.4
    assert any("root@1.2.3.4" == a for a in ssh_argv)


def test_push_propagates_ssh_failure(mocker):
    completed = subprocess.CompletedProcess(
        args=[], returncode=255, stdout="", stderr="connection refused"
    )
    mocker.patch("subprocess.run", return_value=completed)

    with pytest.raises(RuntimeError, match="failed to push"):
        hetzner._push_bootstrap_token("1.2.3.4", "x")  # noqa: SLF001


def test_push_rejects_empty_token():
    with pytest.raises(ValueError, match="bootstrap token must be non-empty"):
        hetzner._push_bootstrap_token("1.2.3.4", "")  # noqa: SLF001


def test_push_rejects_empty_ip():
    with pytest.raises(ValueError, match="server_ip must be non-empty"):
        hetzner._push_bootstrap_token("", "token")  # noqa: SLF001
