"""Tests for the FastAPI server: health, the local test-injection path, the
agentsh poll→deliver→resolve loop, grant short-circuit, and the webhook trigger
(spec 008)."""

from __future__ import annotations

import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from remo_cli.notifier.grants import (
    Grant,
    GrantPredicate,
    GrantScope,
    GrantScopeType,
    TargetMatchType,
)
from remo_cli.notifier.models import Decision, SourceRegistration
from remo_cli.notifier.server import create_app

from ..conftest import FakeAgentsh, make_request


async def _await(pred, timeout: float = 3.0) -> bool:
    for _ in range(int(timeout / 0.02)):
        if pred():
            return True
        await asyncio.sleep(0.02)
    return bool(pred())


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


# --- US4 (T026): status surface ---------------------------------------------
async def test_get_sources_lists_health_and_hides_secrets(config, fake_transport) -> None:
    config.agentsh = None
    healthy = FakeAgentsh([])
    broken = FakeAgentsh([])
    broken.fail_poll = True
    clients = {"http://ok:8080": healthy, "http://bad:8080": broken}
    app = create_app(config, fake_transport, source_client_factory=lambda u, k: clients[u])
    async with app.router.lifespan_context(app):
        reg = app.state.sources
        await reg.register(
            SourceRegistration(
                source_id="ok", api_url="http://ok:8080", api_key="SECRET", labels={"project": "ok"}
            )
        )
        await reg.register(SourceRegistration(source_id="bad", api_url="http://bad:8080", api_key="K"))
        assert await _await(lambda: reg.get("ok").health.last_success_at is not None)
        assert await _await(lambda: reg.get("bad").health.poll_state == "backing_off")

        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        async with client:
            data = (await client.get("/v1/sources")).json()
            assert data["count"] == 2
            by_id = {r["source_id"]: r for r in data["sources"]}
            # No secrets ever leak (US4#1).
            for row in data["sources"]:
                assert "api_key" not in row
                assert "api_url" not in row
            assert "SECRET" not in (await client.get("/v1/sources")).text
            ok = by_id["ok"]
            assert ok["poll_state"] == "polling"
            assert ok["last_success_at"] is not None
            assert ok["labels"] == {"project": "ok"}
            # An unreachable agentsh shows backing_off while still listed (US4#2).
            assert by_id["bad"]["poll_state"] == "backing_off"

            health = (await client.get("/v1/health")).json()
            assert health["sources"] == 2
            assert health["agentsh_connected"] is True  # ≥1 polling

            # A dropped source is absent (US4#3).
            await reg.remove("bad", reg.get("bad").epoch)
            data2 = (await client.get("/v1/sources")).json()
            assert data2["count"] == 1
            assert "bad" not in {r["source_id"] for r in data2["sources"]}


# --- webhook trigger (optional) ---------------------------------------------
def test_webhook_disabled_by_default(config, fake_transport) -> None:
    app = create_app(config, fake_transport, FakeAgentsh([]))
    with TestClient(app) as client:
        assert client.post("/v1/webhook", json={}).status_code == 404


# --- US1 (T013): source-scoped delivery, no cross-routing -------------------
async def test_two_sources_no_cross_routing_colon_free_delivery_id(config, fake_transport) -> None:
    config.agentsh = None  # no seed; just the two dynamic sources
    # Both agentsh endpoints present the SAME raw approval id — only correct
    # routing keeps them apart.
    cloud_a = FakeAgentsh([make_request(id="agentsh-1", target="A")])
    cloud_b = FakeAgentsh([make_request(id="agentsh-1", target="B")])
    clients = {"http://a:8080": cloud_a, "http://b:8080": cloud_b}
    calls: list[tuple[str, str]] = []

    def factory(url: str, key: str):
        calls.append((url, key))
        return clients[url]

    app = create_app(config, fake_transport, source_client_factory=factory)
    async with app.router.lifespan_context(app):
        reg = app.state.sources
        await reg.register(SourceRegistration(source_id="a", api_url="http://a:8080", api_key="KA"))
        await reg.register(SourceRegistration(source_id="b", api_url="http://b:8080", api_key="KB"))
        assert await _await(lambda: len(fake_transport.sent) >= 2)
        delivered_ids = [r.id for r in fake_transport.sent]
        # The id the channel sees is a colon-free delivery id, never the raw id.
        assert all(":" not in d for d in delivered_ids)
        assert "agentsh-1" not in delivered_ids
        assert len(set(delivered_ids)) == 2  # collision-free across sources
        for did in delivered_ids:
            fake_transport.human_decides(did, Decision.allow)
        assert await _await(lambda: cloud_a.resolved and cloud_b.resolved)

    # Each decision resolved against its own agentsh with the REAL id, once.
    assert [r[0] for r in cloud_a.resolved] == ["agentsh-1"]
    assert [r[0] for r in cloud_b.resolved] == ["agentsh-1"]
    # Each source's client was built with its own key (routed via its own key).
    assert ("http://a:8080", "KA") in calls
    assert ("http://b:8080", "KB") in calls


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
