"""Tests for the `remo notifier` CLI subcommands (T020, T032, T034).

SSH and ansible-runner seams are mocked; no host is contacted.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from remo_cli.cli import notifier as nmod
from remo_cli.cli.notifier import notifier
from remo_cli.models.host import KnownHost

HOST = KnownHost(type="hetzner", name="box", host="5.6.7.8", user="remo")


@pytest.fixture(autouse=True)
def _patch_common(monkeypatch):
    # Resolve always returns our fake host; build context is a fixed path.
    monkeypatch.setattr(nmod, "resolve_remo_host", lambda name: HOST)
    monkeypatch.setattr(nmod, "_ensure_build_context", lambda: "/tmp/ctx")
    monkeypatch.setattr(nmod, "build_ssh_opts", lambda host: (["-o", "X=Y"], "remo@5.6.7.8"))


def _completed(rc=0, stdout="") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


# --------------------------------------------------------------------------- deploy (T020 / US2)
def test_deploy_invokes_playbook(monkeypatch):
    monkeypatch.setenv("REMO_NOTIFIER_TELEGRAM_BOT_TOKEN", "12345:T")
    monkeypatch.setenv("REMO_NOTIFIER_TELEGRAM_CHAT_ID", "987")
    captured = {}

    def fake_run(playbook, extra_vars=None, verbose=False):
        captured["playbook"] = playbook
        captured["extra_vars"] = extra_vars
        return 0

    monkeypatch.setattr(nmod, "run_playbook", fake_run)
    result = CliRunner().invoke(notifier, ["deploy", "box"])
    assert result.exit_code == 0
    assert captured["playbook"] == "notifier_deploy.yml"
    ev = captured["extra_vars"]
    assert "-i" in ev and "5.6.7.8," in ev
    assert "ansible_user=remo" in ev
    assert any("remo_notifier_build_context_local=/tmp/ctx" == x for x in ev)


def test_deploy_rebuild_flag(monkeypatch):
    monkeypatch.setenv("REMO_NOTIFIER_TELEGRAM_BOT_TOKEN", "12345:T")
    monkeypatch.setenv("REMO_NOTIFIER_TELEGRAM_CHAT_ID", "987")
    captured = {}

    def fake_run(p, extra_vars=None, verbose=False):
        captured["ev"] = extra_vars
        return 0

    monkeypatch.setattr(nmod, "run_playbook", fake_run)
    result = CliRunner().invoke(notifier, ["deploy", "box", "--rebuild"])
    assert result.exit_code == 0
    assert "remo_notifier_force_rebuild=true" in captured["ev"]


def test_deploy_missing_creds_aborts(monkeypatch):
    monkeypatch.delenv("REMO_NOTIFIER_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("REMO_NOTIFIER_TELEGRAM_CHAT_ID", raising=False)
    called = {"ran": False}
    monkeypatch.setattr(nmod, "run_playbook", lambda *a, **k: called.update(ran=True) or 0)
    result = CliRunner().invoke(notifier, ["deploy", "box"])
    assert result.exit_code == 1
    assert "Missing Telegram credentials" in result.output
    assert called["ran"] is False


# --------------------------------------------------------------------------- status (T034 / US4)
def test_status_renders_health(monkeypatch):
    health = {"status": "ok", "pending_approvals": 0}
    monkeypatch.setattr(nmod, "_ssh_run", lambda h, cmd, capture=False: _completed(0, json.dumps(health)))
    result = CliRunner().invoke(notifier, ["status", "box"])
    assert result.exit_code == 0
    assert '"status": "ok"' in result.output


def test_status_unreachable(monkeypatch):
    monkeypatch.setattr(nmod, "_ssh_run", lambda h, cmd, capture=False: _completed(7, ""))
    result = CliRunner().invoke(notifier, ["status", "box"])
    assert result.exit_code == 1
    assert "unreachable" in result.output


# --------------------------------------------------------------------------- logs (T034 / US4)
def test_logs_builds_journalctl(monkeypatch):
    seen = {}

    def fake_ssh(host, cmd, capture=False):
        seen["cmd"] = cmd
        return _completed(0)

    monkeypatch.setattr(nmod, "_ssh_run", fake_ssh)
    result = CliRunner().invoke(notifier, ["logs", "box", "--follow", "--lines", "50"])
    assert result.exit_code == 0
    assert "journalctl -u remo-notifier.service -n 50 -f" == seen["cmd"]


# --------------------------------------------------------------------------- restart (T034 / US4)
def test_restart_runs_systemctl(monkeypatch):
    seen = {}
    monkeypatch.setattr(nmod, "_ssh_run", lambda h, cmd, capture=False: seen.update(cmd=cmd) or _completed(0))
    result = CliRunner().invoke(notifier, ["restart", "box"])
    assert result.exit_code == 0
    assert seen["cmd"] == "sudo systemctl restart remo-notifier.service"


# --------------------------------------------------------------------------- test (T032 / US3)
def test_test_command_posts_and_reports(monkeypatch):
    seen = {}

    def fake_ssh(host, cmd, capture=False):
        seen["cmd"] = cmd
        return _completed(0, json.dumps({"decision": "allow", "responder": "telegram:p"}))

    monkeypatch.setattr(nmod, "_ssh_run", fake_ssh)
    result = CliRunner().invoke(notifier, ["test", "box"])
    assert result.exit_code == 0
    assert '"decision": "allow"' in result.output
    # canonical test payload labels present in the remote curl body
    assert "policy_rule_name" in seen["cmd"]
    assert "test" in seen["cmd"]
    assert "/v1/approve" in seen["cmd"]


def test_test_command_unreachable(monkeypatch):
    monkeypatch.setattr(nmod, "_ssh_run", lambda h, cmd, capture=False: _completed(7, ""))
    result = CliRunner().invoke(notifier, ["test", "box"])
    assert result.exit_code == 1
    assert "unreachable" in result.output
