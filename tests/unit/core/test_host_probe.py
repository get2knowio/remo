"""Characterization + parsing of the pre-connect probe (spec 030, contracts/host-probe.md)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from remo_cli.core.ssh import (
    PROBE_COMMAND,
    HostToolsProbe,
    check_remote_version,
    probe_host_tools,
)
from remo_cli.models.host_info import HostInfo
from tests.unit.core.test_host_access_ssh_baseline import CASES

pytestmark = pytest.mark.usefixtures("ssm_provider")


@pytest.fixture(params=sorted(CASES))
def case(request):
    host, opts, target = CASES[request.param]
    return host, opts, target


@pytest.fixture(autouse=True)
def _no_tz(mocker, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")


def _run(mocker, **kw):
    return mocker.patch("remo_cli.core.ssh.subprocess.run", **kw)


def _res(rc=0, out="", err=""):
    return MagicMock(returncode=rc, stdout=out, stderr=err)


class TestArgvAndOutcomes:
    def test_argv_and_kwargs(self, mocker, monkeypatch, case):
        host, opts, target = case
        monkeypatch.delenv("TZ", raising=False)
        mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")
        run = _run(mocker, return_value=_res(out="1.0.0\n"))
        assert check_remote_version(host) == ("1.0.0", None)
        assert run.call_args.args[0] == [
            "ssh", *opts, "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
            target, PROBE_COMMAND,
        ]
        assert run.call_args.kwargs == {"capture_output": True, "text": True, "timeout": 15}

    def test_timeout(self, mocker, case):
        _run(mocker, side_effect=subprocess.TimeoutExpired("ssh", 15))
        assert check_remote_version(case[0]) == (None, "SSH timed out after 15s")

    def test_oserror(self, mocker, case):
        _run(mocker, side_effect=OSError("nope"))
        assert check_remote_version(case[0]) == (None, "SSH failed: nope")

    def test_255_with_stderr(self, mocker, case):
        _run(mocker, return_value=_res(255, "", "boom\n"))
        assert check_remote_version(case[0]) == (None, "boom")

    def test_255_without_stderr(self, mocker, case):
        _run(mocker, return_value=_res(255))
        assert check_remote_version(case[0]) == (
            None, "SSH connection failed (exit code 255)",
        )

    @pytest.mark.parametrize("rc,out", [(1, ""), (0, ""), (0, "  \n"), (1, "x\n")])
    def test_no_marker(self, mocker, case, rc, out):
        _run(mocker, return_value=_res(rc, out))
        assert check_remote_version(case[0]) == (None, None)


class TestSeparatorParsing:
    def test_version_and_info(self, mocker, case):
        _run(mocker, return_value=_res(
            out="5.1.0\n\n#remo-host-info\nhost_kind=local\nmenu_command=1\n"))
        probe = probe_host_tools(case[0])
        assert probe == HostToolsProbe("5.1.0", None, HostInfo("local", True, None))

    def test_no_separator_is_all_version(self, mocker, case):
        _run(mocker, return_value=_res(out="1.2.3\n"))
        probe = probe_host_tools(case[0])
        assert probe.version == "1.2.3"
        assert probe.info == HostInfo()

    def test_empty_info(self, mocker, case):
        _run(mocker, return_value=_res(out="1.2.3\n\n#remo-host-info\n"))
        probe = probe_host_tools(case[0])
        assert probe.version == "1.2.3"
        assert probe.info == HostInfo()

    def test_info_without_version_is_no_marker(self, mocker, case):
        _run(mocker, return_value=_res(out="\n#remo-host-info\nhost_kind=local\n"))
        assert probe_host_tools(case[0]) == HostToolsProbe()


class TestRealShell:
    """The probe string itself, run by a real shell against a temp HOME."""

    def _run(self, home):
        return subprocess.run(
            ["bash", "-c", PROBE_COMMAND], env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True,
        )

    def test_marker_and_info(self, tmp_path):
        (tmp_path / ".remo-version").write_text("5.0.0\n")
        d = tmp_path / ".local/state/remo"
        d.mkdir(parents=True)
        (d / "host-info").write_text("host_kind=remote\n")
        r = self._run(tmp_path)
        assert r.returncode == 0
        assert "#remo-host-info" in r.stdout and "host_kind=remote" in r.stdout

    def test_marker_without_info_exits_zero(self, tmp_path):
        (tmp_path / ".remo-version").write_text("5.0.0\n")
        assert self._run(tmp_path).returncode == 0

    def test_no_marker_exits_nonzero(self, tmp_path):
        r = self._run(tmp_path)
        assert r.returncode != 0 and r.stdout == ""


def _outside_single_quotes(command: str) -> str:
    """The parts of *command* the login shell itself parses (single-quoted text dropped)."""
    return "".join(part for i, part in enumerate(command.split("'")) if i % 2 == 0)


class TestLoginShellPortability:
    """The probe and the SSH menu command are parsed by the remote LOGIN shell,
    which on a Mac reached with `remo add` is often fish: no `{ …; }` grouping
    and no `${VAR:-default}` outside single quotes."""

    @pytest.mark.parametrize("name", ["probe", "menu"])
    def test_no_posix_only_constructs_reach_the_login_shell(self, name):
        from remo_cli.core.host_access import SSH_MENU_COMMAND

        parsed = _outside_single_quotes(PROBE_COMMAND if name == "probe" else SSH_MENU_COMMAND)
        assert "${" not in parsed
        assert "{ " not in parsed and "; }" not in parsed

    @pytest.mark.parametrize("shell", ["sh", "dash", "zsh"])
    def test_probe_means_the_same_under_other_shells(self, tmp_path, shell):
        import shutil

        exe = shutil.which(shell)
        if exe is None:
            pytest.skip(f"{shell} not available")
        (tmp_path / ".remo-version").write_text("5.0.0\n")
        run = subprocess.run(
            [exe, "-c", PROBE_COMMAND], env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True,
        )
        assert run.returncode == 0 and "#remo-host-info" in run.stdout
        (tmp_path / ".remo-version").unlink()
        run = subprocess.run(
            [exe, "-c", PROBE_COMMAND], env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True,
        )
        assert run.returncode != 0 and run.stdout == ""
