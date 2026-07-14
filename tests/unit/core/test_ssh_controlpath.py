"""Unit tests for the ControlPath refactor: build_ssh_base_cmd() and the
control_dir/$REMO_SSH_CONTROL_DIR parameterization of build_ssh_opts().

Covers T013 from specs/010-web-session-interface/tasks.md:
- build_ssh_base_cmd() produces args identical in shape to today's
  build_ssh_opts(host, multiplex=True) + manual ["ssh", *opts, target]
  assembly, for both direct-SSH and SSM hosts (FR-055 direct/SSM parity).
- $REMO_SSH_CONTROL_DIR env var override.
- Explicit control_dir param takes precedence over the env var, which takes
  precedence over the hardcoded ~/.ssh default.
- Safe (list-based, non-shell) argv construction.
"""

from __future__ import annotations

import pytest

from remo_cli.models.host import KnownHost


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def direct_host():
    """A basic direct-SSH host (no SSM, no instance_id)."""
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


@pytest.fixture(autouse=True)
def _suppress_tz(monkeypatch):
    """Remove TZ env var and mock detect_timezone to return '' so
    build_ssh_opts/build_ssh_base_cmd don't append SendEnv=TZ, keeping
    every test in this file deterministic."""
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr("remo_cli.core.ssh.detect_timezone", lambda: "")


@pytest.fixture(autouse=True)
def _no_control_dir_env(monkeypatch):
    """Ensure REMO_SSH_CONTROL_DIR is unset by default; individual tests
    that want the env var opt back in with monkeypatch.setenv."""
    monkeypatch.delenv("REMO_SSH_CONTROL_DIR", raising=False)


@pytest.fixture(autouse=True)
def _mock_aws_region(mocker):
    mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")


# ---------------------------------------------------------------------------
# build_ssh_base_cmd() parity with today's build_ssh_opts() + manual assembly
# ---------------------------------------------------------------------------


class TestBuildSshBaseCmdParity:
    """build_ssh_base_cmd() must match today's manual ssh_cmd assembly."""

    def test_direct_host_matches_manual_assembly(self, direct_host):
        from remo_cli.core.ssh import build_ssh_base_cmd, build_ssh_opts

        opts, target = build_ssh_opts(direct_host, multiplex=True)
        expected = ["ssh"] + opts + [target]

        result = build_ssh_base_cmd(direct_host, multiplex=True)

        assert result == expected

    def test_ssm_host_matches_manual_assembly(self, ssm_host):
        from remo_cli.core.ssh import build_ssh_base_cmd, build_ssh_opts

        opts, target = build_ssh_opts(ssm_host, multiplex=True)
        expected = ["ssh"] + opts + [target]

        result = build_ssh_base_cmd(ssm_host, multiplex=True)

        assert result == expected

    def test_direct_and_ssm_parity_same_shape(self, direct_host, ssm_host):
        """Both direct and SSM hosts produce a well-formed ['ssh', ..., target]
        argv of the same overall shape (FR-055) — SSM just carries extra
        ProxyCommand/StrictHostKeyChecking opts and a different target."""
        from remo_cli.core.ssh import build_ssh_base_cmd

        direct_cmd = build_ssh_base_cmd(direct_host, multiplex=True)
        ssm_cmd = build_ssh_base_cmd(ssm_host, multiplex=True)

        assert direct_cmd[0] == "ssh"
        assert ssm_cmd[0] == "ssh"
        assert direct_cmd[-1] == "remo@5.6.7.8"
        assert ssm_cmd[-1] == "remo@i-0abc123def"
        # SSM carries a ProxyCommand; direct does not.
        assert any("ProxyCommand=" in part for part in ssm_cmd)
        assert not any("ProxyCommand=" in part for part in direct_cmd)

    def test_no_control_dir_no_env_default_controlpath(self, direct_host):
        """With no control_dir arg and no env var, ControlPath is unchanged
        from today's hardcoded ~/.ssh default."""
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(direct_host, multiplex=True)

        combined = " ".join(cmd)
        assert "ControlPath=~/.ssh/remo-%r@%h-%p" in combined

    def test_tty_true_appends_dash_tt_before_target(self, direct_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(direct_host, tty=True, multiplex=True)

        assert cmd[-1] == "remo@5.6.7.8"
        assert cmd[-2] == "-tt"

    def test_tty_false_no_tty_flag(self, direct_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(direct_host, multiplex=True)

        assert "-tt" not in cmd
        assert "-t" not in cmd


# ---------------------------------------------------------------------------
# $REMO_SSH_CONTROL_DIR override + precedence
# ---------------------------------------------------------------------------


class TestControlDirPrecedence:
    """Precedence: explicit control_dir param > $REMO_SSH_CONTROL_DIR > default."""

    def test_env_var_overrides_default(self, direct_host, monkeypatch):
        monkeypatch.setenv("REMO_SSH_CONTROL_DIR", "/run/remo-ssh")

        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(direct_host, multiplex=True)

        combined = " ".join(opts)
        assert "ControlPath=/run/remo-ssh/remo-%r@%h-%p" in combined
        assert "~/.ssh" not in combined

    def test_env_var_overrides_default_via_base_cmd(self, direct_host, monkeypatch):
        monkeypatch.setenv("REMO_SSH_CONTROL_DIR", "/run/remo-ssh")

        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(direct_host, multiplex=True)

        combined = " ".join(cmd)
        assert "ControlPath=/run/remo-ssh/remo-%r@%h-%p" in combined

    def test_explicit_control_dir_overrides_env_var(self, direct_host, monkeypatch):
        monkeypatch.setenv("REMO_SSH_CONTROL_DIR", "/run/remo-ssh")

        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(
            direct_host, multiplex=True, control_dir="/explicit/dir"
        )

        combined = " ".join(opts)
        assert "ControlPath=/explicit/dir/remo-%r@%h-%p" in combined
        assert "/run/remo-ssh" not in combined

    def test_explicit_control_dir_without_env_var(self, direct_host):
        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(
            direct_host, multiplex=True, control_dir="/explicit/dir"
        )

        combined = " ".join(opts)
        assert "ControlPath=/explicit/dir/remo-%r@%h-%p" in combined

    def test_no_control_dir_no_env_falls_back_to_default(self, direct_host):
        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(direct_host, multiplex=True)

        combined = " ".join(opts)
        assert "ControlPath=~/.ssh/remo-%r@%h-%p" in combined

    def test_resolve_ssh_control_dir_precedence_directly(self, monkeypatch):
        """Exercise resolve_ssh_control_dir() directly across all three tiers."""
        from remo_cli.core.ssh import resolve_ssh_control_dir

        # 3. Hardcoded default when nothing else is set.
        monkeypatch.delenv("REMO_SSH_CONTROL_DIR", raising=False)
        assert resolve_ssh_control_dir() == "~/.ssh"
        assert resolve_ssh_control_dir(None) == "~/.ssh"

        # 2. Env var wins over the hardcoded default.
        monkeypatch.setenv("REMO_SSH_CONTROL_DIR", "/run/remo-ssh")
        assert resolve_ssh_control_dir() == "/run/remo-ssh"

        # 1. Explicit param wins over the env var.
        assert resolve_ssh_control_dir("/explicit/dir") == "/explicit/dir"


# ---------------------------------------------------------------------------
# Safe argument construction (no shell-injection-prone string concatenation)
# ---------------------------------------------------------------------------


class TestSafeArgConstruction:
    """Target/host/user values must come back as clean list elements, never
    interpolated into a single string intended for shell=True."""

    def test_returns_list_of_strings(self, direct_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(direct_host, multiplex=True)

        assert isinstance(cmd, list)
        assert all(isinstance(part, str) for part in cmd)

    def test_target_is_a_single_discrete_arg(self, direct_host):
        """The user@host target is one argv element, not concatenated into
        a shell command string with other options."""
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(direct_host, multiplex=True)

        assert cmd.count("remo@5.6.7.8") == 1
        # No single element contains both the target and an -o flag glued
        # together (which would indicate shell-string concatenation).
        for part in cmd:
            if part != "remo@5.6.7.8":
                assert "remo@5.6.7.8" not in part

    def test_hostname_with_shell_metacharacters_stays_isolated(self, monkeypatch, mocker):
        """Even a maliciously-crafted hostname is carried as one argv element
        (never shell-interpreted), proving no shell=True string building."""
        mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")
        evil_host = KnownHost(
            type="hetzner",
            name="evil",
            host="5.6.7.8; rm -rf /",
            user="remo",
        )

        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(evil_host, multiplex=True)

        assert cmd[-1] == "remo@5.6.7.8; rm -rf /"
        # The dangerous substring only ever appears as part of that single
        # discrete target element - never split/re-joined elsewhere.
        occurrences = [part for part in cmd if "rm -rf" in part]
        assert occurrences == ["remo@5.6.7.8; rm -rf /"]

    def test_ssm_proxy_command_target_isolated(self, ssm_host):
        """SSM target (user@instance_id) is likewise a single argv element,
        distinct from the ProxyCommand option string."""
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(ssm_host, multiplex=True)

        assert cmd[-1] == "remo@i-0abc123def"
        proxy_entries = [part for part in cmd if part.startswith("ProxyCommand=")]
        assert len(proxy_entries) == 1
        assert "remo@i-0abc123def" not in proxy_entries[0]
