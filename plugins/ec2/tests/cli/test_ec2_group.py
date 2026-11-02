"""`remo ec2` is mounted from this distribution's entry point, with the same
generated verbs it had as the built-in `aws` (moved from remo's
tests/unit/cli/test_main.py, #258)."""

from __future__ import annotations

from click.testing import CliRunner

from remo_cli.cli.main import cli


def _invoke(*args: str):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


class TestEc2Group:
    def test_ec2_help_exits_zero(self):
        assert _invoke("ec2", "--help").exit_code == 0

    def test_ec2_help_shows_description(self):
        assert "EC2" in _invoke("ec2", "--help").output

    def test_ec2_subcommands(self):
        """All EC2 subcommands must appear in ``remo ec2 --help``."""
        result = _invoke("ec2", "--help")
        expected = [
            "create", "destroy", "upgrade", "resize", "list", "sync",
            "stop", "start", "reboot", "info",
        ]
        for name in expected:
            assert name in result.output, f"EC2 subcommand '{name}' missing from help output"
        assert "tag" not in result.output
        assert "host" not in result.output

    def test_ec2_is_listed_and_aws_alias_is_not(self):
        output = _invoke("--help").output
        assert "ec2" in output
        assert "  aws " not in output
