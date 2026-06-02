"""US3 / SC-002: a new channel is a self-contained drop-in.

Registers a fake ChannelDescriptor into the catalog under test and proves it is
listable, selectable, and preflight-checkable through the catalog + CLI — WITHOUT
importing or editing any core/Telegram module. This test module deliberately
imports only ``channels.base``, ``channels.catalog``, and ``cli.notifier``.
"""

from __future__ import annotations


import pytest
from click.testing import CliRunner

from remo_cli.cli import notifier as nmod
from remo_cli.cli.notifier import notifier
from remo_cli.models.host import KnownHost
from remo_cli.notifier.channels import catalog
from remo_cli.notifier.channels.base import ChannelDescriptor, RequiredEnv

HOST = KnownHost(type="hetzner", name="box", host="5.6.7.8", user="remo")


def _stub_descriptor() -> ChannelDescriptor:
    return ChannelDescriptor(
        id="stub",
        label="Stub Channel",
        image_name="remo-notifier-stub",
        required_env=[
            RequiredEnv("REMO_NOTIFIER_STUB_WEBHOOK_URL", secret=False, purpose="Webhook URL"),
            RequiredEnv("REMO_NOTIFIER_STUB_TOKEN", secret=True, purpose="Auth token"),
        ],
        transport_factory="some.fake.module:build",
        render_transport_toml=lambda v: (
            '[transport]\ntype = "stub"\n\n[transport.stub]\n'
            f'webhook_url = "{v["REMO_NOTIFIER_STUB_WEBHOOK_URL"]}"\n'
        ),
        secret_mount="/run/secrets/stub_token",
    )


@pytest.fixture
def with_stub(monkeypatch):
    monkeypatch.setattr(catalog, "CHANNELS", [*catalog.CHANNELS, _stub_descriptor()])


@pytest.fixture(autouse=True)
def _patch_common(monkeypatch):
    monkeypatch.setattr(nmod, "resolve_remo_host", lambda name: HOST)
    monkeypatch.setattr(nmod, "_ensure_build_context", lambda: "/tmp/ctx")
    monkeypatch.setattr(nmod, "build_ssh_opts", lambda host: (["-o", "X=Y"], "remo@5.6.7.8"))


def test_stub_appears_in_catalog(with_stub):
    assert catalog.get("stub") is not None
    assert "stub" in [c.id for c in catalog.list_channels()]


def test_stub_listed_by_channels_command(with_stub):
    result = CliRunner().invoke(notifier, ["channels"])
    assert result.exit_code == 0
    assert "stub" in result.output
    assert "Stub Channel" in result.output


def test_stub_resolves_and_deploys_via_cli(with_stub, monkeypatch):
    for k in ("REMO_NOTIFIER_AGENTSH_API_URL", "REMO_NOTIFIER_AGENTSH_API_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("REMO_NOTIFIER_STUB_WEBHOOK_URL", "https://hooks.example/abc")
    monkeypatch.setenv("REMO_NOTIFIER_STUB_TOKEN", "sekret")
    captured = {}
    monkeypatch.setattr(nmod, "run_playbook", lambda p, extra_vars=None, verbose=False: captured.update(ev=extra_vars) or 0)

    result = CliRunner().invoke(notifier, ["deploy", "box", "--channel", "stub"])
    assert result.exit_code == 0
    ev = captured["ev"]
    assert "remo_notifier_channel=stub" in ev
    assert any(x.startswith("remo_notifier_transport_toml=") and "[transport.stub]" in x for x in ev)
    assert "remo_notifier_secret_filename=stub_token" in ev


def test_stub_preflight_blocks_on_missing_env(with_stub, monkeypatch):
    for k in ("REMO_NOTIFIER_AGENTSH_API_URL", "REMO_NOTIFIER_AGENTSH_API_KEY", "REMO_NOTIFIER_STUB_WEBHOOK_URL"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("REMO_NOTIFIER_STUB_TOKEN", raising=False)
    called = {"ran": False}
    monkeypatch.setattr(nmod, "run_playbook", lambda *a, **k: called.update(ran=True) or 0)
    result = CliRunner().invoke(notifier, ["deploy", "box", "--channel", "stub"])
    assert result.exit_code == 1
    assert "REMO_NOTIFIER_STUB_TOKEN" in result.output
    assert called["ran"] is False
