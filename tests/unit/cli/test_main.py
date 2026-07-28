"""Tests for remo.cli.main – root CLI group, help output, and subcommand registration."""

from __future__ import annotations

import re

import pytest
from click.testing import CliRunner

import remo_cli
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
        expected = ["shell", "cp", "incus", "hetzner", "aws"]
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

    EXPECTED_COMMANDS = [
        "shell",
        "cp",
        "add",
        "remove",
        "incus",
        "proxmox",
        "hetzner",
        "aws",
        "completion",
        "web",
    ]

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
        expected = ["create", "destroy", "upgrade", "resize", "list", "sync", "stop", "start", "reboot", "info"]
        for name in expected:
            assert name in result.output, f"AWS subcommand '{name}' missing from help output"
        assert "tag" not in result.output
        assert "host" not in result.output


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
        expected = ["create", "destroy", "upgrade", "resize", "tag", "list", "sync"]
        for name in expected:
            assert name in result.output, f"Hetzner subcommand '{name}' missing from help output"
        assert "host" not in result.output


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
        expected = ["create", "destroy", "upgrade", "resize", "tag", "list", "sync", "host"]
        for name in expected:
            assert name in result.output, f"Incus subcommand '{name}' missing from help output"

    def test_incus_bootstrap_lives_under_host(self):
        """`bootstrap` is not a flat top-level command — it's `host bootstrap`."""
        result = _invoke("incus", "host", "--help")
        assert "bootstrap" in result.output


# ---------------------------------------------------------------------------
# remo completion <shell>
# ---------------------------------------------------------------------------


class TestCompletionCommand:
    """Verify ``remo completion <shell>`` emits a usable activation script."""

    def test_fish_completion_emits_script(self):
        result = _invoke("completion", "fish")
        assert result.exit_code == 0
        assert "function _remo_completion" in result.output

    def test_fish_completion_skips_empty_candidates(self):
        """Click emits a bare newline when no completions match (e.g.,
        `remo upd<TAB>`). Some fish versions iterate the for-loop once with
        an empty $completion, which then breaks `string split "," ""`. The
        emitted script must guard against this.
        """
        result = _invoke("completion", "fish")
        assert result.exit_code == 0
        assert 'if test -z "$completion"' in result.output
        # Sanity: the guard sits *inside* the for-loop, before string split
        loop_idx = result.output.index("for completion in $response;")
        guard_idx = result.output.index('if test -z "$completion"')
        split_idx = result.output.index('set -l metadata (string split')
        assert loop_idx < guard_idx < split_idx

    def test_fish_completion_quotes_metadata_comparisons(self):
        """An unquoted `test $metadata[1] = "dir"` reports "test: Missing
        argument at index 3" once per candidate per keypress when the split
        yields nothing, turning one malformed entry into a screenful. Quoted,
        the comparison is merely false.
        """
        result = _invoke("completion", "fish")
        assert result.exit_code == 0
        for branch in ("dir", "file", "plain"):
            assert f'test "$metadata[1]" = "{branch}";' in result.output
            assert f'test $metadata[1] = "{branch}";' not in result.output

    def test_fish_hardening_still_matches_clicks_template(self):
        """`str.replace` fails open: if a Click upgrade reflows the fish
        template, every substitution silently no-ops and we ship the unpatched
        script. Pin that each pair matches exactly once against the live
        template, so a Click bump breaks CI instead of shipping the bug back.
        """
        from click.shell_completion import FishComplete

        from remo_cli.cli.main import FISH_HARDENING

        source = FishComplete(cli, {}, "remo", "_REMO_COMPLETE").source()
        for old, _new in FISH_HARDENING:
            assert source.count(old) == 1, (
                f"fish hardening no longer matches Click's template: {old!r} "
                f"appears {source.count(old)} times, expected 1. Click's "
                "activation script changed — update FISH_HARDENING in "
                "src/remo_cli/cli/main.py to match."
            )

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_completion_script_carries_version_stamp(self, shell):
        """The activation script is a static snapshot — upgrading remo does not
        rewrite a file the user already generated. The stamp is what makes a
        stale file identifiable without counting line numbers, and it is what
        the passive staleness nudge compares against.
        """
        result = _invoke("completion", shell)
        assert result.exit_code == 0
        assert f"generated by remo {remo_cli.__version__}" in result.output
        assert f"remo completion install {shell}" in result.output

    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_bare_shell_form_still_routes_to_show(self, shell):
        """`remo completion <shell>` predates the group split and is what every
        existing README and shell history contains. It must keep working.
        """
        bare = _invoke("completion", shell)
        explicit = _invoke("completion", "show", shell)
        assert bare.exit_code == 0
        assert bare.output == explicit.output

    def test_bash_completion_emits_script(self):
        result = _invoke("completion", "bash")
        assert result.exit_code == 0
        assert "_remo_completion" in result.output

    def test_zsh_completion_emits_script(self):
        result = _invoke("completion", "zsh")
        assert result.exit_code == 0
        assert "_remo_completion" in result.output
