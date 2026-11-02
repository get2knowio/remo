"""EC2's SSM ProxyCommand through core's ssh option builder.

Moved from remo's tests/unit/core/test_ssh.py when AWS became the ec2 plugin
(#258): core's own suite proves the ProxyCommand plumbing with a test-only
provider; what is EC2's own is how its ``ssh_proxy_hook`` builds the command --
an ``AWS_PROFILE`` wraps it in an env prefix that blanks the static keys.
"""

from __future__ import annotations

import pytest

from remo_cli.core.ssh import build_ssh_opts
from remo_cli.models.host import KnownHost


@pytest.fixture
def ssm_host():
    return KnownHost(
        type="ec2",
        name="devbox",
        host="3.14.15.92",
        user="remo",
        instance_id="i-0abc123def",
        access_mode="ssm",
        region="us-west-2",
    )


@pytest.fixture(autouse=True)
def _suppress_tz(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr("remo_cli.core.ssh.detect_timezone", lambda: "")


@pytest.fixture(autouse=True)
def _region(mocker):
    mocker.patch("remo_provider_ec2.provider.get_aws_region", return_value="us-west-2")


def test_ssm_host_gets_the_ec2_proxy_command(ssm_host):
    opts, target = build_ssh_opts(ssm_host)
    combined = " ".join(opts)
    assert target == "remo@i-0abc123def"
    assert "aws ssm start-session --region us-west-2" in combined
    assert "--document-name AWS-StartSSHSession" in combined


def test_aws_profile_in_proxy_command(ssm_host, monkeypatch):
    """When AWS_PROFILE is set, the ProxyCommand is wrapped with env prefix."""
    monkeypatch.setenv("AWS_PROFILE", "myprofile")
    opts, _target = build_ssh_opts(ssm_host)
    combined = " ".join(opts)
    assert "AWS_PROFILE=myprofile" in combined
    assert "env AWS_ACCESS_KEY_ID=" in combined


def test_no_aws_profile_no_env_prefix(ssm_host, monkeypatch):
    """Without AWS_PROFILE, no env wrapper in ProxyCommand."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    opts, _target = build_ssh_opts(ssm_host)
    combined = " ".join(opts)
    assert "env AWS_ACCESS_KEY_ID=" not in combined
    assert "aws ssm start-session" in combined


def test_direct_mode_host_has_no_proxy(ssm_host):
    direct = KnownHost(**{**ssm_host.__dict__, "access_mode": "direct"})
    opts, target = build_ssh_opts(direct)
    assert "ProxyCommand" not in " ".join(opts)
    assert target == "remo@3.14.15.92"
