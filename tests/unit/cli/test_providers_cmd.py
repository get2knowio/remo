"""`remo providers` (spec 027 FR-012)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.main import cli
from remo_cli.core import provider_plugins
from remo_cli.core.provider_plugins import DISABLE_ENV_VAR, PluginLoadRecord


def _run(*args: str) -> str:
    result = CliRunner().invoke(cli, ["providers", *args], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return result.output


def test_lists_builtins_with_builtin_source() -> None:
    out = _run()
    for name in ("incus", "proxmox", "aws", "hetzner"):
        assert f"{name}" in out
    assert out.count("builtin") >= 4
    assert "Provider API version: 1" in out


def test_lists_the_fixture_plugin_with_its_distribution_and_version() -> None:
    out = _run()
    assert "fixture" in out
    assert "remo-fixture-provider 0.1.0" in out


def test_skipped_plugins_are_listed_with_their_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        provider_plugins,
        "plugin_load_records",
        lambda: (
            PluginLoadRecord(
                distribution="broken-provider",
                version="0.9",
                entry_point="broken",
                type_name=None,
                status="skipped",
                reason="ImportError: no such sdk",
            ),
        ),
    )
    out = _run()
    assert "Skipped plugins:" in out
    assert "broken (broken-provider 0.9): ImportError: no such sdk" in out


def test_reports_when_discovery_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_plugins, "discovery_was_disabled", lambda: True)
    out = _run()
    assert f"Plugin discovery is disabled ({DISABLE_ENV_VAR} is set)." in out
