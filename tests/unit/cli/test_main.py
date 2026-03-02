"""Tests for remo.cli.main – root CLI group, help output, and subcommand registration."""

from __future__ import annotations

import re

from click.testing import CliRunner

from remo_cli.cli.main import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoke(*args: str) -> object:
    """Shortcut: invoke the CLI with the given arguments and return the result."""
    runner = CliRunner()
    return runner.invoke(cli, list(args), catch_exceptions=False)


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestCliHelp:
    """Verify that ``remo --help`` produces expected output."""

    def test_help_exits_zero(self):
        result = _invoke("--help")
        assert result.exit_code == 0

    def test_help_shows_description(self):
        result = _invoke("--help")
        assert "Remote development environment CLI" in result.output

    def test_help_lists_subcommands(self):
        """All registered subcommands must appear in the help text."""
        result = _invoke("--help")
        expected = ["shell", "cp", "init", "self-update", "incus", "hetzner", "aws"]
        for name in expected:
            assert name in result.output, f"Subcommand '{name}' missing from --help output"

    def test_short_help_flag(self):
        result = _invoke("-h")
        assert result.exit_code == 0
        assert "Remote development environment CLI" in result.output


# ---------------------------------------------------------------------------
# --version / -V
# ---------------------------------------------------------------------------


class TestCliVersion:
    """Verify that version flags work correctly."""

    def test_version_long_flag(self):
        result = _invoke("--version")
        assert result.exit_code == 0
        # Output format: "remo <version>"
        assert "remo" in result.output

    def test_version_contains_version_string(self):
        """The version output should contain a version-like pattern."""
        result = _invoke("--version")
        # Matches patterns like 0.8.0-dev, 1.2.3, 0.0.0-dev, etc.
        assert re.search(r"\d+\.\d+\.\d+", result.output), (
            f"Version output does not contain a version string: {result.output!r}"
        )


# ---------------------------------------------------------------------------
# Subcommand registration
# ---------------------------------------------------------------------------


class TestSubcommandRegistration:
    """Ensure all expected subcommands are registered on the root CLI group."""

    EXPECTED_COMMANDS = ["shell", "cp", "init", "self-update", "incus", "hetzner", "aws"]

    def test_all_subcommands_registered(self):
        """Every expected command name must be present in the CLI group's commands dict."""
        registered = list(cli.commands.keys())
        for name in self.EXPECTED_COMMANDS:
            assert name in registered, f"Subcommand '{name}' not registered on cli group"

    def test_no_unexpected_commands(self):
        """Guard against accidental registrations – the set of commands should
        match exactly what we expect (update this test when adding new
        commands)."""
        registered = set(cli.commands.keys())
        expected = set(self.EXPECTED_COMMANDS)
        assert registered == expected, (
            f"Registered commands differ from expected.\n"
            f"  Extra:   {registered - expected}\n"
            f"  Missing: {expected - registered}"
        )


# ---------------------------------------------------------------------------
# Provider group: AWS
# ---------------------------------------------------------------------------


class TestAwsGroup:
    """Verify the ``remo aws`` subcommand group."""

    def test_aws_help_exits_zero(self):
        result = _invoke("aws", "--help")
        assert result.exit_code == 0

    def test_aws_help_shows_description(self):
        result = _invoke("aws", "--help")
        assert "AWS" in result.output or "EC2" in result.output

    def test_aws_subcommands(self):
        """All AWS subcommands must appear in ``remo aws --help``."""
        result = _invoke("aws", "--help")
        expected = ["create", "destroy", "update", "list", "sync", "stop", "start", "reboot", "info"]
        for name in expected:
            assert name in result.output, f"AWS subcommand '{name}' missing from help output"


# ---------------------------------------------------------------------------
# Provider group: Hetzner
# ---------------------------------------------------------------------------


class TestHetznerGroup:
    """Verify the ``remo hetzner`` subcommand group."""

    def test_hetzner_help_exits_zero(self):
        result = _invoke("hetzner", "--help")
        assert result.exit_code == 0

    def test_hetzner_help_shows_description(self):
        result = _invoke("hetzner", "--help")
        assert "Hetzner" in result.output

    def test_hetzner_subcommands(self):
        """All Hetzner subcommands must appear in ``remo hetzner --help``."""
        result = _invoke("hetzner", "--help")
        expected = ["create", "destroy", "update", "list", "sync"]
        for name in expected:
            assert name in result.output, f"Hetzner subcommand '{name}' missing from help output"


# ---------------------------------------------------------------------------
# Provider group: Incus
# ---------------------------------------------------------------------------


class TestIncusGroup:
    """Verify the ``remo incus`` subcommand group."""

    def test_incus_help_exits_zero(self):
        result = _invoke("incus", "--help")
        assert result.exit_code == 0

    def test_incus_help_shows_description(self):
        result = _invoke("incus", "--help")
        assert "Incus" in result.output

    def test_incus_subcommands(self):
        """All Incus subcommands must appear in ``remo incus --help``."""
        result = _invoke("incus", "--help")
        expected = ["create", "destroy", "update", "list", "sync", "bootstrap"]
        for name in expected:
            assert name in result.output, f"Incus subcommand '{name}' missing from help output"
