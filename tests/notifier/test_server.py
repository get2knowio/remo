"""Tests for the FastAPI server and all status codes (T011)."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from remo_cli.notifier.grants import (
    ArgMatchType,
    Grant,
    GrantPredicate,
    GrantScope,
    GrantScopeType,
)
from remo_cli.notifier.models import Decision, OperationKind
from remo_cli.notifier.server import create_app

from .conftest import make_request


def _global_git_grant() -> Grant:
    return Grant.create(
        predicate=GrantPredicate(kind=OperationKind.command, command="git", args=[], args_match=ArgMatchType.prefix),
        scope=GrantScope(type=GrantScopeType.glob),
        ttl_seconds=3600, created_by="telegram:t", source_approval_id="x",
    )



def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _body(**overrides) -> dict:
    return make_request(**overrides).model_dump(mode="json")


async def test_health(config, fake_transport) -> None:
    app = create_app(config, fake_transport)
    async with _client(app) as client:
        resp = await client.get("/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["transport"] == "fake"
    assert data["pending_approvals"] == 0
    assert "version" in data


async def test_approve_allow(config, fake_transport) -> None:
    fake_transport.auto_resolve = Decision.allow
    app = create_app(config, fake_transport)
    async with _client(app) as client:
        resp = await client.post("/v1/approve", json=_body())
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "allow"
    assert data["responder"] == "telegram:tester"
    assert data["latency_ms"] >= 0
    assert data["approval_id"]


async def test_approve_deny(config, fake_transport) -> None:
    fake_transport.auto_resolve = Decision.deny
    app = create_app(config, fake_transport)
    async with _client(app) as client:
        resp = await client.post("/v1/approve", json=_body())
    assert resp.status_code == 200
    assert resp.json()["decision"] == "deny"


async def test_validation_error_400(config, fake_transport) -> None:
    app = create_app(config, fake_transport)
    async with _client(app) as client:
        resp = await client.post("/v1/approve", json={"unknown": 1})
    assert resp.status_code == 400
    assert resp.json()["error"] == "validation_error"


async def test_timeout_408(config, fake_transport) -> None:
    # No auto-resolve and no human => times out.
    app = create_app(config, fake_transport)
    async with _client(app) as client:
        resp = await client.post("/v1/approve", json=_body(timeout_seconds=1))
    assert resp.status_code == 408
    data = resp.json()
    assert data["decision"] == "deny"
    assert data["reason"] == "timeout"
    # The transport was asked to edit the message with the timeout outcome.
    assert fake_transport.cancelled[0][1] == "timeout"


async def test_duplicate_409(config, fake_transport) -> None:
    dup = str(uuid.uuid4())
    app = create_app(config, fake_transport)
    await app.state.registry.reserve(dup, make_request())
    async with _client(app) as client:
        resp = await client.post("/v1/approve", json=_body(approval_id=dup))
    assert resp.status_code == 409
    assert resp.json()["error"] == "duplicate_approval_id"


async def test_capacity_503(config, fake_transport) -> None:
    config.approval.max_pending_approvals = 1
    app = create_app(config, fake_transport)
    await app.state.registry.reserve("held", make_request())
    async with _client(app) as client:
        resp = await client.post("/v1/approve", json=_body())
    assert resp.status_code == 503
    assert resp.json()["detail"] == "at capacity"


async def test_unhealthy_transport_503(config, fake_transport) -> None:
    fake_transport.healthy_flag = False
    app = create_app(config, fake_transport)
    async with _client(app) as client:
        resp = await client.post("/v1/approve", json=_body())
    assert resp.status_code == 503


async def test_send_failure_503_releases_slot(config, fake_transport) -> None:
    fake_transport.fail_send = True
    app = create_app(config, fake_transport)
    async with _client(app) as client:
        resp = await client.post("/v1/approve", json=_body())
    assert resp.status_code == 503
    assert resp.json()["detail"] == "notification delivery failed"
    # FR-010a: no pending slot held for an undelivered request.
    assert app.state.registry.count() == 0


def test_lifespan_starts_and_stops_transport(config, fake_transport) -> None:
    # TestClient as a context manager runs the FastAPI lifespan (startup +
    # shutdown), covering transport start/stop and the drain on shutdown.
    from fastapi.testclient import TestClient

    app = create_app(config, fake_transport)
    with TestClient(app) as client:
        assert fake_transport.started is True
        assert client.get("/v1/health").status_code == 200
    assert fake_transport.stopped is True


async def test_grant_short_circuit_allows(config, fake_transport) -> None:
    app = create_app(config, fake_transport)
    g = _global_git_grant()
    await app.state.grant_store.create(g)
    async with _client(app) as client:
        resp = await client.post(
            "/v1/approve", json=_body(operation={"kind": "command", "command": "git", "args": ["push"]})
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "allow"
    assert data["responder"] == f"rule:{g.grant_id}"
    assert data["grant_id"] == g.grant_id
    assert fake_transport.sent == []  # FR-G1: no notification
    assert app.state.registry.count() == 0  # no pending slot
    assert g.uses_count == 1


async def test_non_matching_request_falls_through(config, fake_transport) -> None:
    fake_transport.auto_resolve = Decision.allow
    app = create_app(config, fake_transport)
    await app.state.grant_store.create(_global_git_grant())
    async with _client(app) as client:
        resp = await client.post(
            "/v1/approve", json=_body(operation={"kind": "command", "command": "npm", "args": ["install"]})
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["responder"] == "telegram:tester"  # went through the transport
    assert data["grant_id"] is None
    assert len(fake_transport.sent) == 1


async def test_grants_disabled_skips_short_circuit(config, fake_transport) -> None:
    config.grants.enabled = False
    fake_transport.auto_resolve = Decision.allow
    app = create_app(config, fake_transport)
    await app.state.grant_store.create(_global_git_grant())
    async with _client(app) as client:
        resp = await client.post(
            "/v1/approve", json=_body(operation={"kind": "command", "command": "git", "args": ["push"]})
        )
    assert resp.json()["responder"] == "telegram:tester"  # not auto-approved


async def test_paused_skips_short_circuit(config, fake_transport) -> None:
    fake_transport.auto_resolve = Decision.allow
    app = create_app(config, fake_transport)
    await app.state.grant_store.create(_global_git_grant())
    app.state.grant_store.set_paused(True)
    async with _client(app) as client:
        resp = await client.post(
            "/v1/approve", json=_body(operation={"kind": "command", "command": "git", "args": ["push"]})
        )
    assert resp.json()["responder"] == "telegram:tester"


async def test_auto_approval_audit_log_has_no_secrets(config, fake_transport, capsys) -> None:
    # SC-G4 / FR-G11 / FR-017: structural log with grant_id, no workspace/body.
    from remo_cli.notifier.logging_setup import configure_logging

    configure_logging(level="info", json_logs=True)
    app = create_app(config, fake_transport)
    g = _global_git_grant()
    await app.state.grant_store.create(g)
    async with _client(app) as client:
        await client.post(
            "/v1/approve",
            json=_body(
                workspace="/secret/workspace/path",
                operation={"kind": "command", "command": "git", "args": ["push"]},
            ),
        )
    out = capsys.readouterr().out
    assert "auto_approved" in out
    assert g.grant_id in out
    assert "/secret/workspace/path" not in out  # workspace withheld


async def test_timeout_clamped_to_max(config, fake_transport) -> None:
    # G2/FR-006: an over-max request is clamped; we assert the effective timeout
    # used is the configured max by checking the request handed to the transport.
    config.approval.max_timeout_seconds = 1
    config.approval.default_timeout_seconds = 1
    app = create_app(config, fake_transport)
    async with _client(app) as client:
        resp = await client.post("/v1/approve", json=_body(timeout_seconds=9999))
    # No human responds; clamped to 1s -> 408 quickly.
    assert resp.status_code == 408
    assert fake_transport.sent[0].timeout_seconds == 1
