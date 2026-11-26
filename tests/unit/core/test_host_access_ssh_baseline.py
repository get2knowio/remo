"""Characterization of today's SSH argv/env (spec 029, Principle VI).

Written BEFORE ``core/host_access.py`` existed and kept green through the
refactor: every literal below is what ``shell_connect`` /
``check_remote_version`` / ``resume._ssh_prefix`` produced on unmodified code,
so any drift in the SSH path (FR-007, SC-006) fails here.
"""

from __future__ import annotations

import termios
from unittest.mock import MagicMock

import pytest

from remo_cli.models.host import KnownHost

pytestmark = pytest.mark.usefixtures("ssm_provider")

MUX = [
    "-o", "ControlMaster=auto",
    "-o", "ControlPath=~/.ssh/remo-%r@%h-%p",
    "-o", "ControlPersist=60s",
]
SSM_PROXY = (
    "ProxyCommand=aws ssm start-session --region us-west-2 --target %h"
    " --document-name AWS-StartSSHSession --parameters 'portNumber=%p'"
)
SSM_OPTS = [
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", SSM_PROXY,
]
SSH_EXTRA = ["-o", "Port=2222", "-o", "IdentityFile=/k/id", "-o", "IdentitiesOnly=yes"]

#: name -> (host, options placed after the multiplex block, target)
CASES = {
    "incus": (KnownHost("incus", "box/dc", "10.0.0.5", "remo"), [], "remo@10.0.0.5"),
    "proxmox": (
        KnownHost("proxmox", "pve/dev", "10.0.0.6", "remo", instance_id="101", region="root"),
        [],
        "remo@10.0.0.6",
    ),
    "ssh": (
        KnownHost(
            "ssh", "laptop", "h.example", "paul",
            instance_id="2222", access_mode="direct", region="/k/id",
        ),
        SSH_EXTRA,
        "paul@h.example",
    ),
    "cloudvm": (
        KnownHost(
            "cloudvm", "devbox", "3.14.15.92", "remo",
            instance_id="i-0abc", access_mode="ssm", region="us-west-2",
        ),
        SSM_OPTS,
        "remo@i-0abc",
    ),
}

PL = "~/.local/bin/project-launch --project A"


@pytest.fixture(params=sorted(CASES))
def case(request):
    host, opts, target = CASES[request.param]
    # The ssh/ssm option blocks differ in *position* relative to the mux block
    # only in the version check; everything else is mux + opts.
    return request.param, host, opts, target


@pytest.fixture
def run(mocker, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")
    mocker.patch("remo_cli.core.ssh.termios.tcgetattr", side_effect=termios.error)
    mocker.patch("remo_cli.core.ssh.sys.stdin", MagicMock(fileno=lambda: 0))
    mocker.patch("remo_cli.core.ssh.reset_terminal")
    mocker.patch("remo_cli.core.ssh.shutil.which", return_value=None)
    return mocker.patch("remo_cli.core.ssh.subprocess.run")


def _connect(host, **kw):
    from remo_cli.core.ssh import shell_connect

    tunnels = kw.pop("tunnels", [])
    shell_connect(host, tunnels, True, **kw)


class TestShellConnectArgv:
    def test_bare_menu(self, run, case):
        _, host, opts, target = case
        _connect(host)
        assert run.call_args.args[0] == ["ssh", *MUX, *opts, target]
        assert "env" not in run.call_args.kwargs

    def test_project(self, run, case):
        _, host, opts, target = case
        _connect(host, project="A")
        assert run.call_args.args[0] == ["ssh", *MUX, *opts, "-tt", target, PL]

    def test_project_exec(self, run, case):
        _, host, opts, target = case
        _connect(host, project="A", exec_cmd="git status")
        assert run.call_args.args[0] == [
            "ssh", *MUX, *opts, "-tt", target, f"{PL} --exec 'git status'",
        ]

    def test_project_detach_has_no_tty(self, run, case):
        _, host, opts, target = case
        _connect(host, project="A", detach=True, exec_cmd="make")
        assert run.call_args.args[0] == [
            "ssh", *MUX, *opts, target, f"{PL} --detach --exec make",
        ]

    def test_menu_with_tab_key(self, run, case):
        _, host, opts, target = case
        _connect(host, tab_key="0" * 32)
        assert run.call_args.args[0] == [
            "ssh", *MUX, *opts, "-o", "SendEnv=REMO_TAB_KEY", target,
        ]
        assert run.call_args.kwargs["env"]["REMO_TAB_KEY"] == "0" * 32

    def test_project_exec_with_tab_key(self, run, case):
        _, host, opts, target = case
        _connect(host, project="A", exec_cmd="ls", tab_key="f" * 32)
        assert run.call_args.args[0] == [
            "ssh", *MUX, *opts, "-o", "SendEnv=REMO_TAB_KEY", "-tt", target,
            f"{PL} --exec ls",
        ]
        assert run.call_args.kwargs["env"]["REMO_TAB_KEY"] == "f" * 32

    def test_tunnel(self, run, case):
        _, host, opts, target = case
        _connect(host, tunnels=["8080:80"], project="A")
        assert run.call_args.args[0] == [
            "ssh", *MUX, *opts, "-L", "8080:localhost:80", "-tt", target, PL,
        ]


class TestCheckRemoteVersionArgv:
    def test_argv_and_kwargs(self, mocker, monkeypatch, case):
        _, host, opts, target = case
        monkeypatch.delenv("TZ", raising=False)
        mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")
        run = mocker.patch(
            "remo_cli.core.ssh.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="1.0.0\n", stderr=""),
        )
        from remo_cli.core.ssh import check_remote_version

        assert check_remote_version(host) == ("1.0.0", None)
        assert run.call_args.args[0] == [
            "ssh", *opts, "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
            target, "cat ~/.remo-version 2>/dev/null",
        ]
        assert run.call_args.kwargs == {
            "capture_output": True, "text": True, "timeout": 15,
        }

    def test_exit_255_is_an_ssh_error(self, mocker, case):
        _, host, _, _ = case
        mocker.patch(
            "remo_cli.core.ssh.subprocess.run",
            return_value=MagicMock(returncode=255, stdout="", stderr="boom"),
        )
        from remo_cli.core.ssh import check_remote_version

        assert check_remote_version(host) == (None, "boom")


class TestResumeArgv:
    @pytest.fixture(autouse=True)
    def _no_tz(self, mocker, monkeypatch):
        monkeypatch.delenv("TZ", raising=False)
        mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")

    def test_prefix(self, case):
        _, host, opts, target = case
        from remo_cli.core import resume

        assert resume._ssh_prefix(host) == [
            "ssh", "-o", "BatchMode=yes", *MUX, *opts, target,
        ]

    def test_lookup_and_liveness_pass_the_prefix(self, mocker, case):
        _, host, opts, target = case
        from remo_cli.core import resume

        prefix = ["ssh", "-o", "BatchMode=yes", *MUX, *opts, target]
        lookup = mocker.patch("remo_cli.core.resume.lookup_tab", return_value=None)
        listing = mocker.patch("remo_cli.core.resume.list_sessions", return_value=[])
        resume.run_lookup(host, "k" * 32)
        resume.is_project_live(host, "A")
        assert lookup.call_args.args[0] == prefix
        assert listing.call_args.args[0] == prefix
