"""Tests for ``WebSettings`` env-var parsing (spec FR-004, 024-discovery-resilience).

Focused on ``discovery_offline_grace_s`` / ``REMO_WEB_DISCOVERY_OFFLINE_GRACE_S`` —
the grace-budget setting is new in this feature and has no prior test module.
"""

from __future__ import annotations

import pytest

from remo_cli.web.config import WebConfigError, WebSettings


def test_discovery_offline_grace_s_default_is_120(monkeypatch):
    monkeypatch.delenv("REMO_WEB_DISCOVERY_OFFLINE_GRACE_S", raising=False)
    settings = WebSettings()
    assert settings.discovery_offline_grace_s == 120.0


def test_discovery_offline_grace_s_env_override_parses(monkeypatch):
    monkeypatch.setenv("REMO_WEB_DISCOVERY_OFFLINE_GRACE_S", "45")
    settings = WebSettings()
    assert settings.discovery_offline_grace_s == 45.0


def test_discovery_offline_grace_s_blank_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("REMO_WEB_DISCOVERY_OFFLINE_GRACE_S", "   ")
    settings = WebSettings()
    assert settings.discovery_offline_grace_s == 120.0


def test_discovery_offline_grace_s_garbage_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("REMO_WEB_DISCOVERY_OFFLINE_GRACE_S", "not-a-number")
    settings = WebSettings()
    assert settings.discovery_offline_grace_s == 120.0


def test_discovery_offline_grace_s_negative_raises_web_config_error(monkeypatch):
    monkeypatch.setenv("REMO_WEB_DISCOVERY_OFFLINE_GRACE_S", "-1")
    with pytest.raises(WebConfigError):
        WebSettings()


def test_discovery_offline_grace_s_zero_is_accepted(monkeypatch):
    monkeypatch.setenv("REMO_WEB_DISCOVERY_OFFLINE_GRACE_S", "0")
    settings = WebSettings()
    assert settings.discovery_offline_grace_s == 0.0
