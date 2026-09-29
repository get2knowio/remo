"""`remo web check` → `provider_plugins` line (spec 027 FR-013): always PASS, always informative."""

from __future__ import annotations

import pytest

from remo_cli.core import provider_plugins
from remo_cli.core.provider_plugins import DISABLE_ENV_VAR, PluginLoadRecord
from remo_cli.web import check as check_module
from remo_cli.web.check import CheckResult, format_results


def _records(*records: PluginLoadRecord) -> None:
    pass


def test_no_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_plugins, "plugin_load_records", lambda: ())
    monkeypatch.setattr(provider_plugins, "discovery_was_disabled", lambda: False)
    result = check_module._provider_plugins_check()
    assert result == CheckResult("provider_plugins", True, "no plugins installed")


def test_loaded_and_skipped_are_listed_and_still_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        provider_plugins,
        "plugin_load_records",
        lambda: (
            PluginLoadRecord("acme-provider", "2.0", "acme", "acme", "loaded"),
            PluginLoadRecord("broken-provider", "0.9", "broken", None, "skipped", "ImportError: no such sdk"),
        ),
    )
    monkeypatch.setattr(provider_plugins, "discovery_was_disabled", lambda: False)
    result = check_module._provider_plugins_check()

    assert result.passed is True
    assert "1 loaded: acme (acme-provider 2.0)" in result.detail
    assert "1 skipped: broken (broken-provider 0.9): ImportError: no such sdk" in result.detail
    assert result.remediation is not None and "remo providers" in result.remediation
    assert "[PASS] provider_plugins:" in format_results([result])


def test_disabled_discovery_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(provider_plugins, "discovery_was_disabled", lambda: True)
    result = check_module._provider_plugins_check()
    assert result.passed is True and DISABLE_ENV_VAR in result.detail


def test_run_checks_includes_the_line_in_the_unconfigured_shape(tmp_config_dir, tmp_path, monkeypatch) -> None:
    from remo_cli.web.check import run_checks
    from remo_cli.web.config import WebSettings

    monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    results = run_checks(WebSettings(ssh_control_dir=str(tmp_path / "ctl")), include_instances=False)
    assert "provider_plugins" in {r.name for r in results}
