"""Tests for remo_cli.core.fnox subprocess wrapper."""

import subprocess

import pytest

from remo_cli.core import fnox


def test_is_installed_true(mocker):
    mocker.patch("shutil.which", return_value="/usr/local/bin/fnox")
    assert fnox.is_installed() is True


def test_is_installed_false(mocker):
    mocker.patch("shutil.which", return_value=None)
    assert fnox.is_installed() is False


def test_get_success(mocker):
    mocker.patch("shutil.which", return_value="/usr/local/bin/fnox")
    completed = subprocess.CompletedProcess(
        args=["fnox", "get", "x"], returncode=0, stdout="secret-value\n", stderr=""
    )
    run_mock = mocker.patch("subprocess.run", return_value=completed)
    assert fnox.get("hetzner_api_token") == "secret-value"
    run_mock.assert_called_once()
    args = run_mock.call_args[0][0]
    assert args == ["fnox", "get", "hetzner_api_token"]


def test_get_strips_trailing_newline(mocker):
    mocker.patch("shutil.which", return_value="/usr/local/bin/fnox")
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="abc\n", stderr="")
    mocker.patch("subprocess.run", return_value=completed)
    assert fnox.get("a") == "abc"


def test_get_failure_raises(mocker):
    mocker.patch("shutil.which", return_value="/usr/local/bin/fnox")
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="no such key"
    )
    mocker.patch("subprocess.run", return_value=completed)
    with pytest.raises(fnox.FnoxError) as exc_info:
        fnox.get("missing_key")
    assert "exit code 1" in str(exc_info.value)
    assert "no such key" in str(exc_info.value)


def test_get_missing_binary_raises(mocker):
    mocker.patch("shutil.which", return_value=None)
    with pytest.raises(fnox.FnoxError) as exc_info:
        fnox.get("x")
    assert "not installed" in str(exc_info.value)


def test_get_os_error_raises(mocker):
    mocker.patch("shutil.which", return_value="/usr/local/bin/fnox")
    mocker.patch("subprocess.run", side_effect=OSError("EACCES"))
    with pytest.raises(fnox.FnoxError) as exc_info:
        fnox.get("x")
    assert "failed to invoke fnox" in str(exc_info.value)
