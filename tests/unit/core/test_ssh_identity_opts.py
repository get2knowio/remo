"""Unit tests for the identity_file/known_hosts_file parameters added to
build_ssh_opts() and build_ssh_base_cmd() (T006, specs/011-web-adopt R6).

Covers T010 from specs/011-web-adopt/tasks.md:

- Regression: with both parameters left at their ``None`` defaults, the
  produced argv is byte-identical to the pre-change output for representative
  direct-access and SSM hosts (expected argv lists are hardcoded here to pin
  the contract).
- New parameter emission: ``identity_file`` alone (IdentityFile +
  IdentitiesOnly=yes), ``known_hosts_file`` alone (UserKnownHostsFile), both
  together, and their interaction with SSM mode (the SSM branch's
  ``UserKnownHostsFile=/dev/null`` stays first, so SSH's first-value-wins
  semantics preserve SSM's no-host-key-checking behavior).
- build_ssh_base_cmd() accepts both parameters and passes them through to
  build_ssh_opts().
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
    every hardcoded expected argv in this file deterministic."""
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr("remo_cli.core.ssh.detect_timezone", lambda: "")


@pytest.fixture(autouse=True)
def _no_control_dir_env(monkeypatch):
    """Ensure REMO_SSH_CONTROL_DIR is unset so ControlPath uses the ~/.ssh
    default in every hardcoded expected argv."""
    monkeypatch.delenv("REMO_SSH_CONTROL_DIR", raising=False)


@pytest.fixture(autouse=True)
def _no_aws_profile(monkeypatch):
    """Ensure AWS_PROFILE is unset so the SSM ProxyCommand has no env
    wrapper prefix in the hardcoded expected argv."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture(autouse=True)
def _mock_aws_region(mocker):
    mocker.patch("remo_cli.core.ssh.get_aws_region", return_value="us-west-2")


# The exact ProxyCommand string the SSM branch emits today (no AWS_PROFILE).
SSM_PROXY_CMD = (
    "aws ssm start-session"
    " --region us-west-2"
    " --target %h"
    " --document-name AWS-StartSSHSession"
    " --parameters 'portNumber=%p'"
)


# ---------------------------------------------------------------------------
# Regression: defaults produce byte-identical argv to the pre-change output
# ---------------------------------------------------------------------------


class TestDefaultsAreByteIdentical:
    """With identity_file/known_hosts_file left as None, output must equal
    the pre-change argv exactly (hardcoded here to pin the contract)."""

    def test_direct_host_opts_default(self, direct_host):
        from remo_cli.core.ssh import build_ssh_opts

        opts, target = build_ssh_opts(direct_host)

        assert opts == []
        assert target == "remo@5.6.7.8"

    def test_direct_host_base_cmd_default(self, direct_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(direct_host)

        assert cmd == ["ssh", "remo@5.6.7.8"]

    def test_direct_host_base_cmd_multiplex(self, direct_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(direct_host, multiplex=True)

        assert cmd == [
            "ssh",
            "-o", "ControlMaster=auto",
            "-o", "ControlPath=~/.ssh/remo-%r@%h-%p",
            "-o", "ControlPersist=60s",
            "remo@5.6.7.8",
        ]

    def test_ssm_host_opts_default(self, ssm_host):
        from remo_cli.core.ssh import build_ssh_opts

        opts, target = build_ssh_opts(ssm_host)

        assert opts == [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ProxyCommand={SSM_PROXY_CMD}",
        ]
        assert target == "remo@i-0abc123def"

    def test_ssm_host_base_cmd_default(self, ssm_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(ssm_host)

        assert cmd == [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ProxyCommand={SSM_PROXY_CMD}",
            "remo@i-0abc123def",
        ]

    def test_ssm_host_base_cmd_multiplex_tty(self, ssm_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(ssm_host, tty=True, multiplex=True)

        assert cmd == [
            "ssh",
            "-o", "ControlMaster=auto",
            "-o", "ControlPath=~/.ssh/remo-%r@%h-%p",
            "-o", "ControlPersist=60s",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ProxyCommand={SSM_PROXY_CMD}",
            "-tt",
            "remo@i-0abc123def",
        ]


# ---------------------------------------------------------------------------
# identity_file emission
# ---------------------------------------------------------------------------


class TestIdentityFile:
    def test_identity_file_emits_identityfile_and_identitiesonly(self, direct_host):
        from remo_cli.core.ssh import build_ssh_opts

        opts, target = build_ssh_opts(
            direct_host, identity_file="/state/web-identity/id_ed25519"
        )

        assert opts == [
            "-o", "IdentityFile=/state/web-identity/id_ed25519",
            "-o", "IdentitiesOnly=yes",
        ]
        assert target == "remo@5.6.7.8"

    def test_identity_file_via_base_cmd(self, direct_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(
            direct_host, identity_file="/state/web-identity/id_ed25519"
        )

        assert cmd == [
            "ssh",
            "-o", "IdentityFile=/state/web-identity/id_ed25519",
            "-o", "IdentitiesOnly=yes",
            "remo@5.6.7.8",
        ]

    def test_identity_file_alone_no_userknownhostsfile(self, direct_host):
        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(direct_host, identity_file="/some/key")

        assert not any("UserKnownHostsFile" in part for part in opts)


# ---------------------------------------------------------------------------
# known_hosts_file emission
# ---------------------------------------------------------------------------


class TestKnownHostsFile:
    def test_known_hosts_file_emits_userknownhostsfile(self, direct_host):
        from remo_cli.core.ssh import build_ssh_opts

        opts, target = build_ssh_opts(
            direct_host, known_hosts_file="/state/web-identity/known_hosts"
        )

        assert opts == [
            "-o", "UserKnownHostsFile=/state/web-identity/known_hosts",
        ]
        assert target == "remo@5.6.7.8"

    def test_known_hosts_file_via_base_cmd(self, direct_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(
            direct_host, known_hosts_file="/state/web-identity/known_hosts"
        )

        assert cmd == [
            "ssh",
            "-o", "UserKnownHostsFile=/state/web-identity/known_hosts",
            "remo@5.6.7.8",
        ]

    def test_known_hosts_file_alone_no_identity_opts(self, direct_host):
        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(direct_host, known_hosts_file="/some/kh")

        assert not any("IdentityFile" in part for part in opts)
        assert not any("IdentitiesOnly" in part for part in opts)


# ---------------------------------------------------------------------------
# Both parameters together
# ---------------------------------------------------------------------------


class TestBothParams:
    def test_both_params_direct_host(self, direct_host):
        from remo_cli.core.ssh import build_ssh_opts

        opts, target = build_ssh_opts(
            direct_host,
            identity_file="/state/web-identity/id_ed25519",
            known_hosts_file="/state/web-identity/known_hosts",
        )

        assert opts == [
            "-o", "IdentityFile=/state/web-identity/id_ed25519",
            "-o", "IdentitiesOnly=yes",
            "-o", "UserKnownHostsFile=/state/web-identity/known_hosts",
        ]
        assert target == "remo@5.6.7.8"

    def test_both_params_base_cmd_with_multiplex_tty(self, direct_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(
            direct_host,
            tty=True,
            multiplex=True,
            identity_file="/state/web-identity/id_ed25519",
            known_hosts_file="/state/web-identity/known_hosts",
        )

        assert cmd == [
            "ssh",
            "-o", "ControlMaster=auto",
            "-o", "ControlPath=~/.ssh/remo-%r@%h-%p",
            "-o", "ControlPersist=60s",
            "-o", "IdentityFile=/state/web-identity/id_ed25519",
            "-o", "IdentitiesOnly=yes",
            "-o", "UserKnownHostsFile=/state/web-identity/known_hosts",
            "-tt",
            "remo@5.6.7.8",
        ]


# ---------------------------------------------------------------------------
# Interaction with SSM mode
# ---------------------------------------------------------------------------


class TestSsmInteraction:
    def test_ssm_with_identity_file(self, ssm_host):
        """SSM opts are unchanged and the identity opts are appended after
        them; ProxyCommand/target are untouched."""
        from remo_cli.core.ssh import build_ssh_opts

        opts, target = build_ssh_opts(
            ssm_host, identity_file="/state/web-identity/id_ed25519"
        )

        assert opts == [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ProxyCommand={SSM_PROXY_CMD}",
            "-o", "IdentityFile=/state/web-identity/id_ed25519",
            "-o", "IdentitiesOnly=yes",
        ]
        assert target == "remo@i-0abc123def"

    def test_ssm_dev_null_known_hosts_stays_first(self, ssm_host):
        """SSH honors the first value obtained per option, so the SSM
        branch's UserKnownHostsFile=/dev/null must appear before any caller
        known_hosts_file — preserving SSM's no-host-key-checking behavior."""
        from remo_cli.core.ssh import build_ssh_opts

        opts, _target = build_ssh_opts(
            ssm_host, known_hosts_file="/state/web-identity/known_hosts"
        )

        assert opts == [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ProxyCommand={SSM_PROXY_CMD}",
            "-o", "UserKnownHostsFile=/state/web-identity/known_hosts",
        ]
        dev_null_idx = opts.index("UserKnownHostsFile=/dev/null")
        custom_idx = opts.index("UserKnownHostsFile=/state/web-identity/known_hosts")
        assert dev_null_idx < custom_idx

    def test_ssm_both_params_via_base_cmd(self, ssm_host):
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(
            ssm_host,
            identity_file="/state/web-identity/id_ed25519",
            known_hosts_file="/state/web-identity/known_hosts",
        )

        assert cmd == [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ProxyCommand={SSM_PROXY_CMD}",
            "-o", "IdentityFile=/state/web-identity/id_ed25519",
            "-o", "IdentitiesOnly=yes",
            "-o", "UserKnownHostsFile=/state/web-identity/known_hosts",
            "remo@i-0abc123def",
        ]
