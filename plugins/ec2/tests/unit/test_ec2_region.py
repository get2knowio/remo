"""AWS region resolution (moved from core/known_hosts.py by #221)."""

from remo_cli.core.known_hosts import save_known_host
from remo_cli.models.host import KnownHost
from remo_provider_ec2.provider import get_aws_region


def _make_host(type_="incus", name="myhost/dev", host="10.0.0.1", user="remo", **kwargs):
    kwargs.setdefault("access_mode", "direct")
    return KnownHost(type=type_, name=name, host=host, user=user, **kwargs)


# -----------------------------------------------------------------------
# get_aws_region() -- lives with the provider since #221
# -----------------------------------------------------------------------


class TestGetAwsRegion:
    """AWS region resolution."""

    def test_returns_region_from_matching_host(self, tmp_config_dir, monkeypatch):
        """Returns region stored in the matching AWS host entry."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        save_known_host(
            _make_host(
                type_="ec2",
                name="devbox",
                host="1.2.3.4",
                instance_id="i-abc",
                access_mode="ssm",
                region="eu-west-1",
            )
        )
        assert get_aws_region("devbox") == "eu-west-1"

    def test_falls_back_to_aws_region_env(self, tmp_config_dir, monkeypatch):
        """Falls back to AWS_REGION env var when host has no region."""
        monkeypatch.setenv("AWS_REGION", "ap-southeast-1")
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        save_known_host(
            _make_host(
                type_="ec2",
                name="devbox",
                host="1.2.3.4",
                instance_id="i-abc",
                access_mode="ssm",
                region="",
            )
        )
        assert get_aws_region("devbox") == "ap-southeast-1"

    def test_falls_back_to_aws_default_region_env(self, tmp_config_dir, monkeypatch):
        """Falls back to AWS_DEFAULT_REGION when AWS_REGION is unset."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "sa-east-1")
        save_known_host(
            _make_host(
                type_="ec2",
                name="devbox",
                host="1.2.3.4",
                instance_id="i-abc",
                access_mode="ssm",
            )
        )
        assert get_aws_region("devbox") == "sa-east-1"

    def test_falls_back_to_us_west_2(self, tmp_config_dir, monkeypatch):
        """Falls back to 'us-west-2' when no region info is available."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        assert get_aws_region("nonexistent") == "us-west-2"

    def test_ignores_non_matching_host(self, tmp_config_dir, monkeypatch):
        """Does not use region from a different AWS host."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        save_known_host(
            _make_host(
                type_="ec2",
                name="other-box",
                host="1.2.3.4",
                instance_id="i-abc",
                access_mode="ssm",
                region="eu-west-1",
            )
        )
        assert get_aws_region("devbox") == "us-west-2"

    def test_ignores_non_aws_host(self, tmp_config_dir, monkeypatch):
        """Ignores non-aws hosts even if they have matching names."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        save_known_host(_make_host(type_="incus", name="devbox"))
        assert get_aws_region("devbox") == "us-west-2"

    def test_aws_region_env_priority_over_default_region(self, tmp_config_dir, monkeypatch):
        """AWS_REGION takes priority over AWS_DEFAULT_REGION."""
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-1")
        assert get_aws_region("nonexistent") == "us-east-1"
