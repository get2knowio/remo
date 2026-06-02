"""Tests for the FastAPI server: health, the local test-injection path, the
agentsh poll→deliver→resolve loop, grant short-circuit, and the webhook trigger
(spec 008)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from remo_cli.notifier.grants import (
    Grant,
    GrantPredicate,
    GrantScope,
    GrantScopeType,
    TargetMatchType,
)
from remo_cli.notifier.models import Decision
from remo_cli.notifier.server import create_app

from ..conftest import FakeAgentsh, make_request


def _wait_until(pred, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


def _any_grant() -> Grant:
    return Grant.create(
        predicate=GrantPredicate(kind="command", target_match=TargetMatchType.any),
        scope=GrantScope(type=GrantScopeType.glob),
        ttl_seconds=3600,
        created_by="t",
        source_approval_id="x",
    )


# --- health -----------------------------------------------------------------
def test_health(config, fake_transport) -> None:
    app = create_app(config, fake_transport)  # no agentsh
    with TestClient(app) as client:
        data = client.get("/v1/health").json()
    assert data["status"] == "ok"
    assert data["transport"] == "fake"
    assert data["agentsh_connected"] is False
    assert data["pending_approvals"] == 0
    assert "version" in data


def test_lifespan_starts_and_stops_transport(config, fake_transport) -> None:
    app = create_app(config, fake_transport)
    with TestClient(app) as client:
        assert fake_transport.started is True
        assert client.get("/v1/health").status_code == 200
    assert fake_transport.stopped is True


# --- /v1/test local injection -----------------------------------------------
def test_test_endpoint_allow(config, fake_transport) -> None:
    fake_transport.auto_resolve = Decision.allow
    app = create_app(config, fake_transport)
    with TestClient(app) as client:
        resp = client.post("/v1/test", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "allow"
    assert data["responder"] == "telegram:tester"
    assert data["latency_ms"] >= 0
    # The test path delivers through the channel but never resolves to agentsh.
    assert len(fake_transport.sent) == 1
    assert fake_transport.sent[0].rule == "test"


def test_test_endpoint_deny(config, fake_transport) -> None:
    fake_transport.auto_resolve = Decision.deny
    app = create_app(config, fake_transport)
    with TestClient(app) as client:
        resp = client.post("/v1/test", json={})
    assert resp.json()["decision"] == "deny"


def test_test_endpoint_timeout(config, fake_transport) -> None:
    app = create_app(config, fake_transport)
    with TestClient(app) as client:
        resp = client.post("/v1/test", json={"timeout_seconds": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "deny"
    assert data["reason"] == "timeout"
    assert fake_transport.cancelled[0][1] == "timeout"


def test_test_endpoint_send_failure_503(config, fake_transport) -> None:
    fake_transport.fail_send = True
    app = create_app(config, fake_transport)
    with TestClient(app) as client:
        resp = client.post("/v1/test", json={})
    assert resp.status_code == 503
    assert app.state.registry.count() == 0  # FR-008: no slot for an undelivered request


def test_test_endpoint_unhealthy_503(config, fake_transport) -> None:
    fake_transport.healthy_flag = False
    app = create_app(config, fake_transport)
    with TestClient(app) as client:
        resp = client.post("/v1/test", json={})
    assert resp.status_code == 503


# --- agentsh poll → deliver → resolve ---------------------------------------
def test_poll_delivers_and_resolves(config, fake_transport) -> None:
    fake_transport.auto_resolve = Decision.allow
    req = make_request()
    agentsh = FakeAgentsh([req])
    app = create_app(config, fake_transport, agentsh)
    with TestClient(app):
        _wait_until(lambda: agentsh.resolved)
    assert agentsh.resolved
    aid, decision, _reason = agentsh.resolved[0]
    assert aid == req.id
    assert decision is Decision.allow
    assert len(fake_transport.sent) == 1


def test_poll_marks_agentsh_connected(config, fake_transport) -> None:
    agentsh = FakeAgentsh([])
    app = create_app(config, fake_transport, agentsh)
    with TestClient(app) as client:
        _wait_until(lambda: client.get("/v1/health").json()["agentsh_connected"] is True)
        assert client.get("/v1/health").json()["agentsh_connected"] is True


def test_poll_failure_marks_disconnected(config, fake_transport) -> None:
    agentsh = FakeAgentsh([])
    agentsh.fail_poll = True
    app = create_app(config, fake_transport, agentsh)
    with TestClient(app) as client:
        _wait_until(lambda: client.get("/v1/health").json()["agentsh_connected"] is False)
        assert client.get("/v1/health").json()["agentsh_connected"] is False


def test_grant_short_circuit_resolves_without_delivery(config, fake_transport) -> None:
    req = make_request(kind="command", target="git push")
    agentsh = FakeAgentsh([req])
    app = create_app(config, fake_transport, agentsh)
    g = _any_grant()
    app.state.grant_store._grants[g.grant_id] = g  # noqa: SLF001 - seed before startup
    with TestClient(app):
        _wait_until(lambda: agentsh.resolved)
    aid, decision, _reason = agentsh.resolved[0]
    assert aid == req.id
    assert decision is Decision.allow
    assert fake_transport.sent == []  # FR-G1: auto-approved, no notification


def test_poll_dedups_in_flight(config, fake_transport) -> None:
    # The same pending id polled repeatedly must be delivered only once while
    # the human has not yet decided (no auto_resolve -> stays pending).
    req = make_request()
    agentsh = FakeAgentsh([req])
    app = create_app(config, fake_transport, agentsh)
    with TestClient(app):
        _wait_until(lambda: len(fake_transport.sent) >= 1)
        time.sleep(1.2)  # let the loop poll again
    assert len(fake_transport.sent) == 1


# --- webhook trigger (optional) ---------------------------------------------
def test_webhook_disabled_by_default(config, fake_transport) -> None:
    app = create_app(config, fake_transport, FakeAgentsh([]))
    with TestClient(app) as client:
        assert client.post("/v1/webhook", json={}).status_code == 404


def test_webhook_triggers_poll(config, fake_transport) -> None:
    config.agentsh.webhook_enabled = True
    fake_transport.auto_resolve = Decision.allow
    req = make_request()
    agentsh = FakeAgentsh([])  # nothing pending at startup
    app = create_app(config, fake_transport, agentsh)
    with TestClient(app) as client:
        agentsh.pending = [req]
        # The untrusted body is not used as authority — only schedules a poll.
        resp = client.post("/v1/webhook", json={"untrusted": "event", "id": "fake"})
        assert resp.status_code == 202
        _wait_until(lambda: agentsh.resolved)
    assert agentsh.resolved
    assert agentsh.resolved[0][0] == req.id
