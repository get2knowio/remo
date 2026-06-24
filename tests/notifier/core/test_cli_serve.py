"""Tests for the `remo-notifier serve` CLI (spec 008 — catalog dispatch)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from remo_cli.notifier import cli as notifier_cli
from remo_cli.notifier.cli import build_transport, main

from ..conftest import FakeTransport


def test_help() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "notifier" in result.output.lower()


def test_serve_bad_config_exits(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["serve", "--config", str(tmp_path / "absent.toml")])
    assert result.exit_code == 1
    assert "invalid notifier config" in result.output


def test_serve_runs_with_mocked_uvicorn(config_toml, monkeypatch) -> None:
    captured = {}

    def fake_run(app, **kwargs):
        captured["host"] = kwargs.get("host")
        captured["port"] = kwargs.get("port")

    fake = FakeTransport()
    monkeypatch.setattr(notifier_cli, "build_transport", lambda cfg: fake)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)

    result = CliRunner().invoke(main, ["serve", "--config", str(config_toml)])
    assert result.exit_code == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 18181


def test_build_transport_resolves_telegram_via_catalog(config, monkeypatch) -> None:
    # Patch the concrete transport so no real PTB Application is constructed.
    created = {}

    class _FakeTg:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(
        "remo_cli.notifier.channels.telegram.transport.TelegramTransport", _FakeTg
    )
    build_transport(config)
    assert created["authorized_chat_id"] == 987654321
    assert created["instance_id"] == "test-instance"
    assert created["token"] == "12345:FAKE-TOKEN"


def test_build_transport_unknown_channel(config) -> None:
    config.transport.type = "nope"
    with pytest.raises(Exception) as exc:  # click.ClickException
        build_transport(config)
    assert "unknown channel" in str(exc.value)
