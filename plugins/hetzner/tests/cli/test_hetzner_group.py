"""`remo hetzner` is mounted from this distribution's entry point, with the
same generated verbs it had as a built-in (moved from remo's
tests/unit/cli/test_main.py)."""

from __future__ import annotations

from click.testing import CliRunner

from remo_cli.cli.main import cli


def _invoke(*args: str):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


class TestHetznerGroup:
    def test_hetzner_help_exits_zero(self):
        assert _invoke("hetzner", "--help").exit_code == 0

    def test_hetzner_help_shows_description(self):
        assert "Hetzner" in _invoke("hetzner", "--help").output

    def test_hetzner_subcommands(self):
        """All Hetzner subcommands must appear in ``remo hetzner --help``."""
        result = _invoke("hetzner", "--help")
        expected = ["create", "destroy", "upgrade", "resize", "tag", "list", "sync"]
        for name in expected:
            assert name in result.output, f"Hetzner subcommand '{name}' missing from help output"
        assert "host" not in result.output

    def test_root_help_lists_hetzner(self):
        assert "hetzner" in _invoke("--help").output

    def test_registered_as_a_plugin_not_a_builtin(self):
        from remo_cli.core.provider_registry import builtin_descriptors, descriptor_source

        assert "hetzner" not in {d.type_name for d in builtin_descriptors()}
        assert descriptor_source("hetzner").startswith("remo-provider-hetzner ")


def test_remo_providers_lists_this_distribution():
    out = CliRunner().invoke(cli, ["providers"], catch_exceptions=False).output
    [row] = [line for line in out.splitlines() if line.startswith("hetzner ")]
    assert "remo-provider-hetzner" in row
    assert row.rstrip().endswith("loaded")
