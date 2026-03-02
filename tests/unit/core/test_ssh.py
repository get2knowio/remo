"""Unit tests for remo.core.ssh module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from remo_cli.models.host import KnownHost


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hetzner_host():
    """A basic Hetzner host (no SSM, no instance_id)."""
    return KnownHost(
        type="hetzner",
        name="webserver",
        host="5.6.7.8",
        user="remo",
    )


@pytest.fixture
def ssm_host():
    """An AWS host using SSM access mode."""
    return KnownHost(
        type="aws",
        name="devbox",
        host="3.14.15.92",
        user="remo",
        instance_id="i-0abc123def",
        access_mode="ssm",
        region="us-west-2",
    )


@pytest.fixture
def _suppress_tz(monkeypatch):
    """Remove TZ env var and mock detect_timezone to return '' so
    build_ssh_opts does not append SendEnv=TZ to every call."""
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr("remo_cli.core.ssh.detect_timezone", lambda: "")


# ---------------------------------------------------------------------------
# build_ssh_opts
# ---------------------------------------------------------------------------


class TestBuildSshOpts:
    """Tests for build_ssh_opts()."""

    @pytest.mark.usefixtures("_suppress_tz")
    def test_hetzner_basic(self, hetzner_host, mocker):
        """A plain Hetzner host returns user@host with no ProxyCommand."""
        mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")

        from remo_cli.core.ssh import build_ssh_opts

        opts, target = build_ssh_opts(hetzner_host)

        assert target == "remo@5.6.7.8"
        # No ProxyCommand option should be present
        combined = " ".join(opts)
        assert "ProxyCommand" not in combined

    @pytest.mark.usefixtures("_suppress_tz")
    def test_ssm_host_has_proxy_command(self, ssm_host, mocker):
        """An SSM host returns user@instance_id and contains aws ssm ProxyCommand."""
        mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")

        from remo_cli.core.ssh import build_ssh_opts

        opts, target = build_ssh_opts(ssm_host)

        assert target == "remo@i-0abc123def"
        combined = " ".join(opts)
        assert "ProxyCommand=" in combined
        assert "aws ssm start-session" in combined
        assert "--region us-west-2" in combined
        assert "StrictHostKeyChecking=no" in combined
        assert "UserKnownHostsFile=/dev/null" in combined

    @pytest.mark.usefixtures("_suppress_tz")
    def test_multiplex_adds_control_opts(self, hetzner_host, mocker):
        """When multiplex=True, ControlMaster/ControlPath/ControlPersist are included."""
        mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")

        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(hetzner_host, multiplex=True)

        combined = " ".join(opts)
        assert "ControlMaster=auto" in combined
        assert "ControlPath=~/.ssh/remo-%r@%h-%p" in combined
        assert "ControlPersist=60s" in combined

    @pytest.mark.usefixtures("_suppress_tz")
    def test_multiplex_false_no_control_opts(self, hetzner_host, mocker):
        """Without multiplex, no ControlMaster options appear."""
        mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")

        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(hetzner_host, multiplex=False)

        combined = " ".join(opts)
        assert "ControlMaster" not in combined

    @pytest.mark.usefixtures("_suppress_tz")
    def test_aws_profile_in_proxy_command(self, ssm_host, monkeypatch, mocker):
        """When AWS_PROFILE is set, the ProxyCommand is wrapped with env prefix."""
        monkeypatch.setenv("AWS_PROFILE", "myprofile")
        mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")

        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(ssm_host)

        combined = " ".join(opts)
        assert "AWS_PROFILE=myprofile" in combined
        assert "env AWS_ACCESS_KEY_ID=" in combined

    @pytest.mark.usefixtures("_suppress_tz")
    def test_no_aws_profile_no_env_prefix(self, ssm_host, monkeypatch, mocker):
        """Without AWS_PROFILE, no env wrapper in ProxyCommand."""
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")

        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(ssm_host)

        combined = " ".join(opts)
        assert "env AWS_ACCESS_KEY_ID=" not in combined
        assert "aws ssm start-session" in combined

    def test_timezone_forwarded_when_detected(self, hetzner_host, monkeypatch, mocker):
        """When detect_timezone returns a value, SendEnv=TZ is added."""
        monkeypatch.delenv("TZ", raising=False)
        mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="America/New_York")
        mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")

        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(hetzner_host)

        combined = " ".join(opts)
        assert "SendEnv=TZ" in combined


# ---------------------------------------------------------------------------
# detect_timezone
# ---------------------------------------------------------------------------


class TestDetectTimezone:
    """Tests for detect_timezone()."""

    def test_tz_env_valid_iana(self, monkeypatch, mocker):
        """TZ env var with a slash is accepted."""
        monkeypatch.setenv("TZ", "America/Chicago")
        # Avoid side effects from subprocess calls
        mocker.patch("subprocess.run", side_effect=FileNotFoundError)

        from remo_cli.core.ssh import detect_timezone

        assert detect_timezone() == "America/Chicago"

    def test_tz_env_utc_is_rejected(self, monkeypatch, mocker):
        """TZ=UTC is skipped because it doesn't contain a slash."""
        monkeypatch.setenv("TZ", "UTC")
        # timedatectl not found, /etc/timezone does not exist, etc.
        mocker.patch("subprocess.run", side_effect=FileNotFoundError)
        mocker.patch("remo_cli.core.ssh.Path")
        mock_path_cls = mocker.patch("remo_cli.core.ssh.Path")
        mock_etc_tz = MagicMock()
        mock_etc_tz.is_file.return_value = False
        mock_etc_lt = MagicMock()
        mock_etc_lt.is_symlink.return_value = False

        def path_side_effect(arg):
            if arg == "/etc/timezone":
                return mock_etc_tz
            if arg == "/etc/localtime":
                return mock_etc_lt
            return MagicMock()

        mock_path_cls.side_effect = path_side_effect

        from remo_cli.core.ssh import detect_timezone

        result = detect_timezone()
        # Should not return "UTC", should be empty string
        assert result != "UTC"

    def test_falls_back_to_timedatectl(self, monkeypatch, mocker):
        """When TZ is not set, timedatectl is tried."""
        monkeypatch.delenv("TZ", raising=False)

        mock_run = mocker.patch("subprocess.run")
        timedatectl_result = MagicMock()
        timedatectl_result.stdout = "Europe/Berlin\n"
        mock_run.return_value = timedatectl_result

        from remo_cli.core.ssh import detect_timezone

        assert detect_timezone() == "Europe/Berlin"

    def test_falls_back_to_etc_timezone(self, monkeypatch, mocker):
        """When timedatectl fails, /etc/timezone is read."""
        monkeypatch.delenv("TZ", raising=False)

        # timedatectl not found
        mocker.patch("subprocess.run", side_effect=FileNotFoundError)

        mock_path_cls = mocker.patch("remo_cli.core.ssh.Path")
        mock_etc_tz = MagicMock()
        mock_etc_tz.is_file.return_value = True
        mock_etc_tz.read_text.return_value = "Asia/Tokyo\n"

        mock_etc_lt = MagicMock()
        mock_etc_lt.is_symlink.return_value = False

        def path_side_effect(arg):
            if arg == "/etc/timezone":
                return mock_etc_tz
            if arg == "/etc/localtime":
                return mock_etc_lt
            return MagicMock()

        mock_path_cls.side_effect = path_side_effect

        from remo_cli.core.ssh import detect_timezone

        assert detect_timezone() == "Asia/Tokyo"

    def test_falls_back_to_etc_localtime_symlink(self, monkeypatch, mocker):
        """When /etc/timezone is absent, /etc/localtime symlink is checked."""
        monkeypatch.delenv("TZ", raising=False)

        # timedatectl not found
        mocker.patch("subprocess.run", side_effect=FileNotFoundError)

        mock_path_cls = mocker.patch("remo_cli.core.ssh.Path")

        mock_etc_tz = MagicMock()
        mock_etc_tz.is_file.return_value = False

        mock_etc_lt = MagicMock()
        mock_etc_lt.is_symlink.return_value = True
        resolved_path = MagicMock()
        resolved_path.parts = ("/", "usr", "share", "zoneinfo", "US", "Eastern")
        mock_etc_lt.resolve.return_value = resolved_path

        def path_side_effect(arg):
            if arg == "/etc/timezone":
                return mock_etc_tz
            if arg == "/etc/localtime":
                return mock_etc_lt
            return MagicMock()

        mock_path_cls.side_effect = path_side_effect

        from remo_cli.core.ssh import detect_timezone

        assert detect_timezone() == "US/Eastern"

    def test_returns_empty_when_nothing_works(self, monkeypatch, mocker):
        """When all detection methods fail, returns empty string."""
        monkeypatch.delenv("TZ", raising=False)

        # All subprocess calls fail
        mocker.patch("subprocess.run", side_effect=FileNotFoundError)

        mock_path_cls = mocker.patch("remo_cli.core.ssh.Path")
        mock_etc_tz = MagicMock()
        mock_etc_tz.is_file.return_value = False
        mock_etc_lt = MagicMock()
        mock_etc_lt.is_symlink.return_value = False

        def path_side_effect(arg):
            if arg == "/etc/timezone":
                return mock_etc_tz
            if arg == "/etc/localtime":
                return mock_etc_lt
            return MagicMock()

        mock_path_cls.side_effect = path_side_effect

        from remo_cli.core.ssh import detect_timezone

        assert detect_timezone() == ""


# ---------------------------------------------------------------------------
# resolve_remo_host
# ---------------------------------------------------------------------------


class TestResolveRemoHost:
    """Tests for resolve_remo_host()."""

    def test_with_name_delegates_to_resolve_by_name(self, mocker):
        """When name is provided, resolve_remo_host_by_name is called."""
        host = KnownHost(type="hetzner", name="myhost", host="1.2.3.4", user="remo")
        mock_resolve = mocker.patch(
            "remo_cli.core.ssh.resolve_remo_host_by_name", return_value=host
        )

        from remo_cli.core.ssh import resolve_remo_host

        result = resolve_remo_host(name="myhost")

        mock_resolve.assert_called_once_with("myhost")
        assert result is host

    def test_without_name_single_host_returns_it(self, mocker):
        """With no name and exactly one registered host, returns that host."""
        host = KnownHost(type="aws", name="devbox", host="3.3.3.3", user="remo")
        mocker.patch("remo_cli.core.ssh.get_known_hosts", return_value=[host])

        from remo_cli.core.ssh import resolve_remo_host

        result = resolve_remo_host()

        assert result is host

    def test_without_name_no_hosts_raises_system_exit(self, mocker):
        """With no name and no registered hosts, raises SystemExit."""
        mocker.patch("remo_cli.core.ssh.get_known_hosts", return_value=[])

        from remo_cli.core.ssh import resolve_remo_host

        with pytest.raises(SystemExit, match="No remo environments registered"):
            resolve_remo_host()

    def test_without_name_multiple_hosts_invokes_picker(self, mocker):
        """With no name and multiple hosts, calls pick_environment."""
        host_a = KnownHost(type="hetzner", name="a", host="1.1.1.1", user="remo")
        host_b = KnownHost(type="aws", name="b", host="2.2.2.2", user="remo")
        hosts = [host_a, host_b]
        mocker.patch("remo_cli.core.ssh.get_known_hosts", return_value=hosts)
        mock_pick = mocker.patch("remo_cli.core.ssh.pick_environment", return_value=host_b)

        from remo_cli.core.ssh import resolve_remo_host

        result = resolve_remo_host()

        mock_pick.assert_called_once_with(hosts)
        assert result is host_b


# ---------------------------------------------------------------------------
# require_session_manager_plugin
# ---------------------------------------------------------------------------


class TestRequireSessionManagerPlugin:
    """Tests for require_session_manager_plugin()."""

    def test_raises_when_plugin_not_found(self, mocker):
        """When session-manager-plugin is not on PATH, raises SystemExit."""
        mocker.patch("shutil.which", return_value=None)

        from remo_cli.core.ssh import require_session_manager_plugin

        with pytest.raises(SystemExit, match="session-manager-plugin is not installed"):
            require_session_manager_plugin()

    def test_passes_when_plugin_found(self, mocker):
        """When session-manager-plugin is found, no error is raised."""
        mocker.patch("shutil.which", return_value="/usr/local/bin/session-manager-plugin")

        from remo_cli.core.ssh import require_session_manager_plugin

        # Should not raise
        require_session_manager_plugin()
