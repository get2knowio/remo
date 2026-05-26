"""Phase 3: bootstrap-token push helper for Proxmox LXC containers."""

from __future__ import annotations

import subprocess

import pytest

from remo_cli.providers import proxmox


def _ok_proc() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_push_uses_ssh_and_stdin(mocker):
    run_mock = mocker.patch("subprocess.run", return_value=_ok_proc())

    proxmox._push_bootstrap_token_to_container(  # noqa: SLF001
        host="prox-host",
        host_user="root",
        vmid="200",
        token="SECRET_VALUE",
    )

    run_mock.assert_called_once()
    cmd = run_mock.call_args[0][0]
    assert cmd[0] == "ssh"
    assert "root@prox-host" in cmd
    inner = cmd[-1]
    # Token MUST be on stdin, not anywhere in argv.
    assert "SECRET_VALUE" not in " ".join(cmd)
    assert run_mock.call_args.kwargs.get("input") == "SECRET_VALUE"
    assert "pct exec 200 --" in inner
    assert "install -D -m 0400 -o root -g root /dev/stdin" in inner
    assert "/etc/remo-broker/bootstrap-token" in inner


def test_push_propagates_failure(mocker):
    proc = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="vmid not found"
    )
    mocker.patch("subprocess.run", return_value=proc)
    with pytest.raises(RuntimeError, match="failed to push"):
        proxmox._push_bootstrap_token_to_container(  # noqa: SLF001
            host="prox-host", host_user="root", vmid="200", token="t",
        )


def test_push_rejects_empty_token():
    with pytest.raises(ValueError, match="bootstrap token must be non-empty"):
        proxmox._push_bootstrap_token_to_container(  # noqa: SLF001
            host="prox-host", host_user="root", vmid="200", token=""
        )


def test_push_rejects_empty_vmid():
    with pytest.raises(ValueError, match="vmid must be non-empty"):
        proxmox._push_bootstrap_token_to_container(  # noqa: SLF001
            host="prox-host", host_user="root", vmid="", token="t"
        )
