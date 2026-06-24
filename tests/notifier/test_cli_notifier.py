"""Tests for the `remo notifier` laptop CLI (spec 008 — channel selection).

SSH and ansible-runner seams are mocked; no host is contacted.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from remo_cli.cli import notifier as nmod
from remo_cli.cli.notifier import notifier
from remo_cli.models.host import KnownHost

HOST = KnownHost(type="hetzner", name="box", host="5.6.7.8", user="remo")

_CREDS = {
    "REMO_NOTIFIER_AGENTSH_API_URL": "http://172.17.0.1:8080",
    "REMO_NOTIFIER_AGENTSH_API_KEY": "approver-key",
    "REMO_NOTIFIER_TELEGRAM_BOT_TOKEN": "12345:T",
    "REMO_NOTIFIER_TELEGRAM_CHAT_ID": "987",
}


@pytest.fixture(autouse=True)
def _patch_common(monkeypatch):
    monkeypatch.setattr(nmod, "resolve_remo_host", lambda name: HOST)
    monkeypatch.setattr(nmod, "_ensure_build_context", lambda: "/tmp/ctx")
    monkeypatch.setattr(nmod, "build_ssh_opts", lambda host: (["-o", "X=Y"], "remo@5.6.7.8"))


def _set_creds(monkeypatch, **overrides):
    creds = dict(_CREDS)
    creds.update(overrides)
    for k in _CREDS:
        monkeypatch.delenv(k, raising=False)
    for k, v in creds.items():
        if v is not None:
            monkeypatch.setenv(k, v)


def _completed(rc=0, stdout="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


# --------------------------------------------------------------------------- channels (T012)
def test_channels_lists_catalog():
    result = CliRunner().invoke(notifier, ["channels"])
    assert result.exit_code == 0
    assert "telegram" in result.output
    assert "Telegram" in result.output
    assert "REMO_NOTIFIER_TELEGRAM_BOT_TOKEN (secret)" in result.output
    assert "REMO_NOTIFIER_TELEGRAM_CHAT_ID" in result.output


# --------------------------------------------------------------------------- deploy (T013-T015)
def test_deploy_single_channel_autoselect(monkeypatch):
    _set_creds(monkeypatch)
    captured = {}

    def fake_run(playbook, extra_vars=None, verbose=False):
        captured["playbook"] = playbook
        captured["ev"] = extra_vars
        return 0

    monkeypatch.setattr(nmod, "run_playbook", fake_run)
    result = CliRunner().invoke(notifier, ["deploy", "box"])
    assert result.exit_code == 0
    assert captured["playbook"] == "notifier_deploy.yml"
    ev = captured["ev"]
    assert "remo_notifier_channel=telegram" in ev
    assert any(x.startswith("remo_notifier_transport_toml=") and "[transport.telegram]" in x for x in ev)
    assert "remo_notifier_agentsh_api_url=http://172.17.0.1:8080" in ev
    assert "remo_notifier_agentsh_api_key=approver-key" in ev
    assert "remo_notifier_channel_secret=12345:T" in ev
    assert "remo_notifier_secret_filename=telegram_bot_token" in ev


def test_deploy_named_channel(monkeypatch):
    _set_creds(monkeypatch)
    captured = {}
    monkeypatch.setattr(nmod, "run_playbook", lambda p, extra_vars=None, verbose=False: captured.update(ev=extra_vars) or 0)
    result = CliRunner().invoke(notifier, ["deploy", "box", "--channel", "telegram"])
    assert result.exit_code == 0
    assert "remo_notifier_channel=telegram" in captured["ev"]


def test_deploy_unknown_channel_aborts(monkeypatch):
    _set_creds(monkeypatch)
    called = {"ran": False}
    monkeypatch.setattr(nmod, "run_playbook", lambda *a, **k: called.update(ran=True) or 0)
    result = CliRunner().invoke(notifier, ["deploy", "box", "--channel", "bogus"])
    assert result.exit_code == 1
    assert "unknown channel 'bogus'" in result.output
    assert "telegram" in result.output
    assert called["ran"] is False


def test_deploy_rebuild_flag(monkeypatch):
    _set_creds(monkeypatch)
    captured = {}
    monkeypatch.setattr(nmod, "run_playbook", lambda p, extra_vars=None, verbose=False: captured.update(ev=extra_vars) or 0)
    result = CliRunner().invoke(notifier, ["deploy", "box", "--rebuild"])
    assert result.exit_code == 0
    assert "remo_notifier_force_rebuild=true" in captured["ev"]


def test_deploy_missing_agentsh_creds_aborts(monkeypatch):
    _set_creds(monkeypatch, REMO_NOTIFIER_AGENTSH_API_KEY=None)
    called = {"ran": False}
    monkeypatch.setattr(nmod, "run_playbook", lambda *a, **k: called.update(ran=True) or 0)
    result = CliRunner().invoke(notifier, ["deploy", "box"])
    assert result.exit_code == 1
    assert "REMO_NOTIFIER_AGENTSH_API_KEY" in result.output
    assert called["ran"] is False


def test_deploy_missing_channel_creds_aborts(monkeypatch):
    # SC-007: a different channel's vars being present must not satisfy preflight.
    _set_creds(monkeypatch, REMO_NOTIFIER_TELEGRAM_BOT_TOKEN=None)
    called = {"ran": False}
    monkeypatch.setattr(nmod, "run_playbook", lambda *a, **k: called.update(ran=True) or 0)
    result = CliRunner().invoke(notifier, ["deploy", "box"])
    assert result.exit_code == 1
    assert "REMO_NOTIFIER_TELEGRAM_BOT_TOKEN" in result.output
    assert called["ran"] is False


def test_deploy_no_channel_noninteractive_multichannel_errors(monkeypatch):
    _set_creds(monkeypatch)
    # Force a multi-channel catalog + non-interactive stdin.
    from remo_cli.notifier.channels import catalog as cat
    from remo_cli.notifier.channels.base import ChannelDescriptor, RequiredEnv

    extra = ChannelDescriptor(
        id="stub", label="Stub", image_name="remo-notifier-stub",
        required_env=[RequiredEnv("REMO_NOTIFIER_STUB_TOKEN", True, "x")],
        transport_factory="x:y", render_transport_toml=lambda v: "",
    )
    monkeypatch.setattr(cat, "CHANNELS", [cat.CHANNELS[0], extra])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    called = {"ran": False}
    monkeypatch.setattr(nmod, "run_playbook", lambda *a, **k: called.update(ran=True) or 0)
    result = CliRunner().invoke(notifier, ["deploy", "box"])
    assert result.exit_code == 1
    assert "--channel" in result.output
    assert called["ran"] is False


# --------------------------------------------------------------------------- status / logs / restart
def test_status_renders_health(monkeypatch):
    health = {"status": "ok", "pending_approvals": 0, "transport": "telegram"}
    monkeypatch.setattr(nmod, "_ssh_run", lambda h, cmd, capture=False: _completed(0, json.dumps(health)))
    result = CliRunner().invoke(notifier, ["status", "box"])
    assert result.exit_code == 0
    assert '"transport": "telegram"' in result.output


def test_status_unreachable(monkeypatch):
    monkeypatch.setattr(nmod, "_ssh_run", lambda h, cmd, capture=False: _completed(7, ""))
    result = CliRunner().invoke(notifier, ["status", "box"])
    assert result.exit_code == 1
    assert "unreachable" in result.output


def test_sources_lists_connected(monkeypatch):
    payload = {
        "count": 1,
        "sources": [
            {
                "source_id": "proj-a",
                "labels": {"project": "proj-a"},
                "poll_state": "polling",
                "last_success_at": "2026-06-08T12:00:10Z",
                "consecutive_failures": 0,
                "permanent": False,
            }
        ],
    }
    seen = {}

    def fake_ssh(host, cmd, capture=False):
        seen["cmd"] = cmd
        return _completed(0, json.dumps(payload))

    monkeypatch.setattr(nmod, "_ssh_run", fake_ssh)
    result = CliRunner().invoke(notifier, ["sources", "box"])
    assert result.exit_code == 0
    assert '"source_id": "proj-a"' in result.output
    assert "/v1/sources" in seen["cmd"]


def test_sources_unreachable(monkeypatch):
    monkeypatch.setattr(nmod, "_ssh_run", lambda h, cmd, capture=False: _completed(7, ""))
    result = CliRunner().invoke(notifier, ["sources", "box"])
    assert result.exit_code == 1
    assert "unreachable" in result.output


def test_logs_builds_journalctl(monkeypatch):
    seen = {}
    monkeypatch.setattr(nmod, "_ssh_run", lambda h, cmd, capture=False: seen.update(cmd=cmd) or _completed(0))
    result = CliRunner().invoke(notifier, ["logs", "box", "--follow", "--lines", "50"])
    assert result.exit_code == 0
    assert "journalctl -u remo-notifier.service -n 50 -f" == seen["cmd"]


def test_restart_runs_systemctl(monkeypatch):
    seen = {}
    monkeypatch.setattr(nmod, "_ssh_run", lambda h, cmd, capture=False: seen.update(cmd=cmd) or _completed(0))
    result = CliRunner().invoke(notifier, ["restart", "box"])
    assert result.exit_code == 0
    assert seen["cmd"] == "sudo systemctl restart remo-notifier.service"


# --------------------------------------------------------------------------- test (T020a)
def test_test_command_posts_to_test_endpoint(monkeypatch):
    seen = {}

    def fake_ssh(host, cmd, capture=False):
        seen["cmd"] = cmd
        return _completed(0, json.dumps({"decision": "allow", "responder": "telegram:p"}))

    monkeypatch.setattr(nmod, "_ssh_run", fake_ssh)
    result = CliRunner().invoke(notifier, ["test", "box"])
    assert result.exit_code == 0
    assert '"decision": "allow"' in result.output
    # Drives the local injection endpoint, not the removed /v1/approve.
    assert "/v1/test" in seen["cmd"]
    assert "/v1/approve" not in seen["cmd"]


def test_test_command_unreachable(monkeypatch):
    monkeypatch.setattr(nmod, "_ssh_run", lambda h, cmd, capture=False: _completed(7, ""))
    result = CliRunner().invoke(notifier, ["test", "box"])
    assert result.exit_code == 1
    assert "unreachable" in result.output
