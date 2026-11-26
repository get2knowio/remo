"""core/host_access.py — SSH vs local transport selection (spec 029)."""

from __future__ import annotations

import os

import pytest

from remo_cli.core.errors import PreconditionError
from remo_cli.core.host_access import (
    LocalAccess,
    SshAccess,
    get_host_access,
)
from remo_cli.models.host import KnownHost

BASH = "/opt/homebrew/bin/bash"


@pytest.fixture
def local_host():
    return KnownHost("local", "local", "localhost", "paul", access_mode="direct")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def setup_done(home):
    state = home / ".local" / "state" / "remo"
    state.mkdir(parents=True)
    (state / "local.env").write_text(f"REMO_BASH={BASH}\n")
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "project-menu").write_text("#!/bin/bash\n")
    return home


class TestSelection:
    @pytest.mark.parametrize("type_", ["incus", "proxmox", "ssh", "anything-else"])
    def test_every_other_type_is_ssh(self, type_):
        host = KnownHost(type_, "x", "h", "u")
        assert isinstance(get_host_access(host), SshAccess)

    def test_local_type_is_local(self, local_host):
        assert isinstance(get_host_access(local_host), LocalAccess)


class TestLocalAccess:
    def test_menu_argv(self, local_host, setup_done):
        access = get_host_access(local_host)
        assert access.interactive_argv(local_host, None, tty=False) == [
            BASH, "-c", 'exec "$HOME/.local/bin/project-menu"',
        ]

    def test_remote_cmd_argv(self, local_host, setup_done):
        cmd = "~/.local/bin/project-launch --project A"
        assert get_host_access(local_host).interactive_argv(
            local_host, cmd, tty=True
        ) == [BASH, "-c", cmd]

    def test_env_prefixes_path_and_tab_key(self, local_host, setup_done, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        env = get_host_access(local_host).env(local_host, tab_key="a" * 32)
        assert env["PATH"] == f"{setup_done}/.local/bin{os.pathsep}/usr/bin"
        assert env["REMO_TAB_KEY"] == "a" * 32

    def test_env_without_tab_key_has_none(self, local_host, setup_done, monkeypatch):
        monkeypatch.delenv("REMO_TAB_KEY", raising=False)
        env = get_host_access(local_host).env(local_host, tab_key=None)
        assert "REMO_TAB_KEY" not in env

    def test_no_tunnels(self, local_host):
        assert get_host_access(local_host).supports_tunnels() is False

    def test_prefix_joins_words_like_ssh(self, local_host, setup_done):
        import subprocess

        prefix = get_host_access(local_host).command_prefix(local_host, batch=True)
        assert prefix == [BASH, "-c", 'eval "$*"', "remo-local"]
        # Run the same shape with the system bash: PATH-prefix word + argv.
        out = subprocess.run(
            ["bash", "-c", 'eval "$*"', "remo-local",
             'FOO="$HOME/x:$FOO"', "printf", "%s", "a   b"],
            capture_output=True, text=True,
        )
        assert out.returncode == 0

    def test_probe_argv(self, local_host, setup_done):
        assert get_host_access(local_host).probe_argv(local_host, "cat ~/.remo-version") == [
            BASH, "-c", "cat ~/.remo-version",
        ]

    @pytest.mark.parametrize("missing", ["env", "menu"])
    def test_setup_incomplete_is_a_precondition_error(self, local_host, setup_done, missing):
        target = (
            setup_done / ".local/state/remo/local.env"
            if missing == "env"
            else setup_done / ".local/bin/project-menu"
        )
        target.unlink()
        access = get_host_access(local_host)
        with pytest.raises(PreconditionError, match="remo local setup"):
            access.interactive_argv(local_host, None, tty=False)
        with pytest.raises(PreconditionError, match="remo local setup"):
            access.command_prefix(local_host, batch=True)

    def test_env_file_without_bash_key(self, local_host, setup_done):
        (setup_done / ".local/state/remo/local.env").write_text("OTHER=1\n")
        with pytest.raises(PreconditionError):
            get_host_access(local_host).command_prefix(local_host, batch=True)


class TestSshAccess:
    def test_env_is_none_without_tab_key(self):
        host = KnownHost("incus", "b", "h", "u")
        assert SshAccess().env(host, tab_key=None) is None

    def test_supports_tunnels(self):
        assert SshAccess().supports_tunnels() is True

    def test_unbatched_prefix_has_no_batchmode(self):
        host = KnownHost("incus", "b", "h", "u")
        assert "BatchMode=yes" not in SshAccess().command_prefix(host, batch=False)
