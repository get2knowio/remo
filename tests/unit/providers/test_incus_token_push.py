"""Phase 3 / US3: bootstrap-token push helper for Incus containers."""

from __future__ import annotations

import subprocess

import pytest

from remo_cli.providers import incus


def _ok_proc() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_push_to_localhost_uses_bash_and_stdin(mocker):
    run_mock = mocker.patch("subprocess.run", return_value=_ok_proc())

    incus._push_bootstrap_token_to_container(  # noqa: SLF001
        host="localhost",
        host_user="",
        container="lxc-1",
        token="SECRET_VALUE",
    )

    run_mock.assert_called_once()
    cmd = run_mock.call_args[0][0]
    assert cmd[0] == "bash"
    assert cmd[1] == "-c"
    inner = cmd[2]
    # Token MUST be on stdin, not anywhere in argv.
    assert "SECRET_VALUE" not in " ".join(cmd)
    assert run_mock.call_args.kwargs.get("input") == "SECRET_VALUE"
    assert "incus exec lxc-1 --" in inner
    assert "install" in inner
    assert "-m 0400" in inner
    assert "/dev/stdin" in inner
    assert "/etc/remo-broker/bootstrap-token" in inner


def test_push_to_remote_uses_ssh_and_stdin(mocker):
    run_mock = mocker.patch("subprocess.run", return_value=_ok_proc())

    incus._push_bootstrap_token_to_container(  # noqa: SLF001
        host="incus-host",
        host_user="ubuntu",
        container="lxc-1",
        token="SECRET_VALUE",
    )

    run_mock.assert_called_once()
    cmd = run_mock.call_args[0][0]
    assert cmd[0] == "ssh"
    assert "ubuntu@incus-host" in cmd
    inner = cmd[-1]
    assert "SECRET_VALUE" not in " ".join(cmd)
    assert run_mock.call_args.kwargs.get("input") == "SECRET_VALUE"
    assert "incus exec lxc-1 --" in inner
    assert "install -D -m 0400 -o root -g root /dev/stdin" in inner
    assert "/etc/remo-broker/bootstrap-token" in inner


def test_push_propagates_failure(mocker):
    proc = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="container not found"
    )
    mocker.patch("subprocess.run", return_value=proc)
    with pytest.raises(RuntimeError, match="failed to push"):
        incus._push_bootstrap_token_to_container(  # noqa: SLF001
            host="localhost",
            host_user="",
            container="lxc-1",
            token="t",
        )


def test_push_rejects_empty_token():
    with pytest.raises(ValueError, match="bootstrap token must be non-empty"):
        incus._push_bootstrap_token_to_container(  # noqa: SLF001
            host="localhost", host_user="", container="lxc-1", token=""
        )


def test_push_rejects_empty_container():
    with pytest.raises(ValueError, match="container must be non-empty"):
        incus._push_bootstrap_token_to_container(  # noqa: SLF001
            host="localhost", host_user="", container="", token="t"
        )
