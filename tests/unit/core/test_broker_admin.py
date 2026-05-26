"""Tests for the admin-socket SSH bridge."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from remo_cli.core import broker_admin


def _fake_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    p = MagicMock()
    p.stdout = stdout
    p.stderr = stderr
    p.returncode = returncode
    return p


def test_rotate_bootstrap_ok(mocker):
    captured: dict[str, list[str]] = {}

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_proc(stdout='{"ok":true,"backend_auth":"ok"}\n')

    mocker.patch("subprocess.run", side_effect=_run)
    broker_admin.rotate_bootstrap(ssh_host="1.2.3.4", ssh_user="root")

    cmd = captured["cmd"]
    assert cmd[0] == "ssh"
    assert "root@1.2.3.4" in cmd
    remote = cmd[-1]
    assert "sudo python3 -c" in remote
    assert broker_admin.ADMIN_SOCKET_PATH in remote
    assert '"op":"rotate-bootstrap"' in remote


def test_rotate_bootstrap_surfaces_broker_error(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=_fake_proc(
            stdout=json.dumps(
                {"ok": False, "error": "bootstrap_error", "message": "fnox parse"}
            )
        ),
    )
    with pytest.raises(broker_admin.BrokerAdminError) as exc:
        broker_admin.rotate_bootstrap(ssh_host="h", ssh_user="root")
    assert "bootstrap_error" in str(exc.value)
    assert "fnox parse" in str(exc.value)


def test_rotate_bootstrap_ssh_transport_failure(mocker):
    mocker.patch(
        "subprocess.run",
        return_value=_fake_proc(stderr="Permission denied", returncode=255),
    )
    with pytest.raises(broker_admin.BrokerAdminError) as exc:
        broker_admin.rotate_bootstrap(ssh_host="h", ssh_user="root")
    assert "rc=255" in str(exc.value)
    assert "Permission denied" in str(exc.value)


def test_rotate_bootstrap_empty_response(mocker):
    mocker.patch("subprocess.run", return_value=_fake_proc(stdout=""))
    with pytest.raises(broker_admin.BrokerAdminError) as exc:
        broker_admin.rotate_bootstrap(ssh_host="h", ssh_user="root")
    assert "no response" in str(exc.value)


def test_rotate_bootstrap_garbage_response(mocker):
    mocker.patch("subprocess.run", return_value=_fake_proc(stdout="not json"))
    with pytest.raises(broker_admin.BrokerAdminError) as exc:
        broker_admin.rotate_bootstrap(ssh_host="h", ssh_user="root")
    assert "non-JSON" in str(exc.value)


def test_default_ssh_options_are_batchmode_and_timeout(mocker):
    captured: dict = {}

    def _run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_proc(stdout='{"ok":true}\n')

    mocker.patch("subprocess.run", side_effect=_run)
    broker_admin.rotate_bootstrap(ssh_host="h", ssh_user="root")
    cmd = captured["cmd"]
    assert "BatchMode=yes" in " ".join(cmd)
    assert "ConnectTimeout=10" in " ".join(cmd)
