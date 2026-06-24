"""Presence-connection integration tests (spec 009 US1/US2, T012/T017).

The presence connection is a held-open HTTP/1.1 streaming request, so these run
against a real uvicorn server on an ephemeral loopback port (httpx ASGITransport
buffers held-open streams and cannot exercise this path).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import uvicorn

from remo_cli.notifier.models import Decision
from remo_cli.notifier.server import create_app

from ..conftest import FakeAgentsh, make_request


async def _await(pred, timeout: float = 5.0) -> bool:
    for _ in range(int(timeout / 0.02)):
        if pred():
            return True
        await asyncio.sleep(0.02)
    return bool(pred())


@asynccontextmanager
async def _serve(app):
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        if not await _await(lambda: server.started, timeout=10):
            raise RuntimeError("uvicorn did not start")
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


def _app(config, fake_transport, *, cloud: dict | None = None, seed=None):
    cloud = cloud or {}

    def factory(url: str, key: str):
        return cloud.setdefault(url, FakeAgentsh([]))

    return create_app(config, fake_transport, seed, source_client_factory=factory)


def _payload(source_id="a", url="http://a:8080", key="K", labels=None):
    return {"source_id": source_id, "api_url": url, "api_key": key, "labels": labels or {}}


async def _open(client: httpx.AsyncClient, payload):
    """Open a presence stream, consume the first keepalive, return (cm, resp)."""
    cm = client.stream("POST", "/v1/sources", json=payload)
    resp = await cm.__aenter__()
    assert resp.status_code == 200, resp.status_code
    first = await asyncio.wait_for(resp.aiter_raw().__anext__(), timeout=3)
    assert b"keepalive" in first
    return cm, resp


# --- US1 (T012) -------------------------------------------------------------
async def test_connect_registers_and_begins_polling(config, fake_transport) -> None:
    config.agentsh = None
    cloud = {"http://a:8080": FakeAgentsh([make_request(id="r1", target="cmd")])}
    app = _app(config, fake_transport, cloud=cloud)
    async with _serve(app) as base, httpx.AsyncClient(base_url=base) as client:
        cm, _ = await _open(client, _payload())
        assert await _await(lambda: app.state.sources.get("a") is not None)
        # Polling started within one interval → the approval was delivered.
        assert await _await(lambda: len(fake_transport.sent) >= 1)
        await cm.__aexit__(None, None, None)
        # Connection drop de-registers the source.
        assert await _await(lambda: app.state.sources.count() == 0)


async def test_two_presence_connections_resolve_to_correct_source(config, fake_transport) -> None:
    # Quickstart SC-001: two sources connect to two fake agentsh endpoints; raise
    # one approval on each; each is delivered and resolved against the CORRECT
    # source, concurrently — over real held-open presence connections.
    config.agentsh = None
    cloud_a = FakeAgentsh([make_request(id="raw", target="A")])
    cloud_b = FakeAgentsh([make_request(id="raw", target="B")])  # same raw id
    cloud = {"http://a:8080": cloud_a, "http://b:8080": cloud_b}
    app = _app(config, fake_transport, cloud=cloud)
    async with _serve(app) as base:
        async with httpx.AsyncClient(base_url=base) as c1, httpx.AsyncClient(base_url=base) as c2:
            cm1, _ = await _open(c1, _payload("a", "http://a:8080", "KA"))
            cm2, _ = await _open(c2, _payload("b", "http://b:8080", "KB"))
            assert await _await(lambda: len(fake_transport.sent) >= 2)
            for r in list(fake_transport.sent):
                fake_transport.human_decides(r.id, Decision.allow)
            assert await _await(lambda: cloud_a.resolved and cloud_b.resolved)
            assert [r[0] for r in cloud_a.resolved] == ["raw"]
            assert [r[0] for r in cloud_b.resolved] == ["raw"]
            await cm1.__aexit__(None, None, None)
            await cm2.__aexit__(None, None, None)


async def test_duplicate_source_id_reconciles_to_single_loop(config, fake_transport) -> None:
    config.agentsh = None
    app = _app(config, fake_transport)
    async with _serve(app) as base:
        async with httpx.AsyncClient(base_url=base) as c1, httpx.AsyncClient(base_url=base) as c2:
            cm1, _ = await _open(c1, _payload("dup", "http://a:8080"))
            assert await _await(lambda: app.state.sources.get("dup") is not None)
            cm2, _ = await _open(c2, _payload("dup", "http://b:8080"))
            # Latest connection wins: one source, epoch bumped (FR-003).
            assert await _await(lambda: app.state.sources.get("dup").epoch == 2)
            assert app.state.sources.count() == 1
            await cm1.__aexit__(None, None, None)
            await cm2.__aexit__(None, None, None)


async def test_at_capacity_returns_503_before_holding_stream(config, fake_transport) -> None:
    config.agentsh = None
    config.sources.max_sources = 1
    app = _app(config, fake_transport)
    async with _serve(app) as base, httpx.AsyncClient(base_url=base) as client:
        cm, _ = await _open(client, _payload("a", "http://a:8080"))
        assert await _await(lambda: app.state.sources.count() == 1)
        resp = await client.post("/v1/sources", json=_payload("b", "http://b:8080"))
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"] == "at_capacity"
        assert body["max_sources"] == 1
        await cm.__aexit__(None, None, None)


async def test_bad_payload_returns_400(config, fake_transport) -> None:
    config.agentsh = None
    app = _app(config, fake_transport)
    async with _serve(app) as base, httpx.AsyncClient(base_url=base) as client:
        # invalid source_id (colon) + missing api_key
        resp = await client.post("/v1/sources", json={"source_id": "bad:id", "api_url": "http://a"})
        assert resp.status_code == 400
        assert app.state.sources.count() == 0


# --- US2 (T017) -------------------------------------------------------------
async def test_graceful_close_stops_polling(config, fake_transport) -> None:
    config.agentsh = None
    cloud = {"http://a:8080": FakeAgentsh([make_request(id="r1")])}
    app = _app(config, fake_transport, cloud=cloud)
    async with _serve(app) as base, httpx.AsyncClient(base_url=base) as client:
        cm, _ = await _open(client, _payload())
        assert await _await(lambda: len(fake_transport.sent) >= 1)
        await cm.__aexit__(None, None, None)
        assert await _await(lambda: app.state.sources.count() == 0)
        # Poll loop stopped: no further deliveries after removal.
        n = len(fake_transport.sent)
        await asyncio.sleep(0.2)
        assert len(fake_transport.sent) == n


async def test_reconnect_re_serves_the_source(config, fake_transport) -> None:
    config.agentsh = None
    app = _app(config, fake_transport)
    async with _serve(app) as base, httpx.AsyncClient(base_url=base) as client:
        cm, _ = await _open(client, _payload("a"))
        assert await _await(lambda: app.state.sources.get("a") is not None)
        await cm.__aexit__(None, None, None)
        assert await _await(lambda: app.state.sources.count() == 0)
        # Reconnect: re-served with a fresh (bumped) epoch.
        cm2, _ = await _open(client, _payload("a"))
        assert await _await(lambda: app.state.sources.get("a") is not None)
        assert app.state.sources.get("a").epoch == 2
        await cm2.__aexit__(None, None, None)


def test_registry_starts_empty_on_fresh_app(config, fake_transport) -> None:
    config.agentsh = None
    app = _app(config, fake_transport)
    # No persistence: a fresh app has no sources before any connection (FR-013).
    assert app.state.sources.count() == 0


async def test_permanent_seed_survives(config, fake_transport) -> None:
    # [agentsh] seed present (injected fake client); it has no presence
    # connection and is never removed by drop logic.
    seed = FakeAgentsh([])
    app = create_app(config, fake_transport, seed)
    async with _serve(app):
        assert await _await(lambda: app.state.sources.get("seed") is not None)
        # A drop signal for the seed's epoch is a no-op (permanent).
        assert await app.state.sources.remove("seed", 0) is False
        assert app.state.sources.get("seed") is not None
