"""REST + WebSocket endpoint tests for the terminals API (T038), no SSH/Docker.

The WS pump is exercised end-to-end by monkeypatching the attach argv to a
trivial ``cat`` stand-in (same PTY/pump/reap plumbing, no real ssh), so the
subprotocol handshake, binary round-trip, control frames, and disconnect-reap
are all covered deterministically. Real-ssh coverage lives in the Docker-gated
``tests/integration/test_terminal_attach.py``.

Test-harness note: Starlette's ``TestClient.websocket_connect`` does
``headers.setdefault("sec-websocket-protocol", ...)``, which MUTATES the passed
``headers`` dict. So every ``websocket_connect`` here is given a FRESH headers
dict (via :func:`_ws_headers`) — reusing one dict would pin the first connect's
token onto all later connects.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from remo_cli.models.host import KnownHost
from remo_cli.models.session_target import (
    DevcontainerRunning,
    SessionTarget,
    ZellijState,
)
from remo_cli.web import app as app_module
from remo_cli.web.api import terminals as terminals_module
from remo_cli.web.config import WebSettings
from remo_cli.web.models import TerminalState

_ORIGIN = "http://testserver"
_HEADERS = {"Origin": _ORIGIN}


def _ws_headers(origin: str = _ORIGIN) -> dict[str, str]:
    """A FRESH headers dict per WS connect (websocket_connect mutates it)."""
    return {"Origin": origin}


def _settings(**overrides) -> WebSettings:
    return WebSettings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[_ORIGIN],
        ssh_control_dir="/tmp/remo-ssh-test",
        **overrides,
    )


class _StubDiscovery:
    def __init__(self, target: SessionTarget, host: KnownHost) -> None:
        self._target = target
        self._host = host

    def find_target(self, target_id: str):
        return self._target if target_id == self._target.id else None

    def find_host(self, instance_type: str, instance_name: str):
        if instance_type == self._host.type and instance_name == self._host.name:
            return self._host
        return None


@pytest.fixture
def target() -> SessionTarget:
    return SessionTarget(
        id="target-abc",
        instance_type="incus",
        instance_name="dev",
        project="my-proj",
        has_devcontainer=False,
        zellij_state=ZellijState.ABSENT,
        devcontainer_running=DevcontainerRunning.UNKNOWN,
        discovered_at="2026-07-13T00:00:00Z",
    )


@pytest.fixture
def known_host() -> KnownHost:
    return KnownHost(type="incus", name="dev", host="127.0.0.1", user="remo")


@pytest.fixture
def app(monkeypatch, target, known_host):
    settings = _settings()
    application = app_module.create_app(settings)
    application.state.discovery_service = _StubDiscovery(target, known_host)
    # Replace the real ssh attach argv with a trivial echo stand-in so the WS
    # pump can be exercised without SSH.
    monkeypatch.setattr(
        terminals_module,
        "build_attach_argv",
        lambda host, project, control_dir=None: ["cat"],
    )
    return application


def _new_client(app) -> TestClient:
    """A fresh TestClient (virgin httpx pool) bound to *app*."""
    return TestClient(app, base_url=_ORIGIN)


@pytest.fixture
def client(app):
    with _new_client(app) as test_client:
        yield test_client


def _create(client) -> tuple[str, str]:
    body = client.post(
        "/api/v1/terminals",
        json={"session_target_id": "target-abc", "cols": 80, "rows": 24},
        headers=_HEADERS,
    ).json()
    return body["terminal_id"], body["ws_token"]


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------


def test_post_terminal_rejects_nonpositive_dims(client):
    resp = client.post(
        "/api/v1/terminals",
        json={"session_target_id": "target-abc", "cols": 0, "rows": 24},
        headers=_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_dimensions"


def test_post_terminal_unknown_target_is_404(client):
    resp = client.post(
        "/api/v1/terminals",
        json={"session_target_id": "nope", "cols": 80, "rows": 24},
        headers=_HEADERS,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_target"


def test_post_terminal_success_returns_token_and_pending(client):
    resp = client.post(
        "/api/v1/terminals",
        json={"session_target_id": "target-abc", "cols": 5000, "rows": 24},
        headers=_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ws_subprotocol"] == "remo-terminal.v1"
    assert body["state"] == "pending"
    assert body["expires_in"] == 30
    assert body["ws_token"]
    assert body["terminal_id"]


def test_missing_origin_on_post_is_forbidden(client):
    resp = client.post(
        "/api/v1/terminals",
        json={"session_target_id": "target-abc", "cols": 80, "rows": 24},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_origin"


def test_per_client_cap_returns_429(monkeypatch, target, known_host):
    settings = _settings(terminal_cap_per_client=2, terminal_cap_global=100)
    application = app_module.create_app(settings)
    application.state.discovery_service = _StubDiscovery(target, known_host)

    ok = 0
    with _new_client(application) as client:
        for _ in range(3):
            resp = client.post(
                "/api/v1/terminals",
                json={"session_target_id": "target-abc", "cols": 80, "rows": 24},
                headers=_HEADERS,
            )
            if resp.status_code == 201:
                ok += 1
            else:
                assert resp.status_code == 429
                assert resp.json()["error"]["code"] == "cap_reached"
    assert ok == 2


def test_get_terminals_lists_this_client(client):
    client.post(
        "/api/v1/terminals",
        json={"session_target_id": "target-abc", "cols": 80, "rows": 24},
        headers=_HEADERS,
    )
    resp = client.get("/api/v1/terminals")
    assert resp.status_code == 200
    terms = resp.json()["terminals"]
    assert len(terms) == 1
    assert terms[0]["state"] == "pending"


def test_delete_unknown_terminal_is_404(client):
    resp = client.delete("/api/v1/terminals/does-not-exist", headers=_HEADERS)
    assert resp.status_code == 404


def test_delete_terminal_reaps_and_204(client):
    created = client.post(
        "/api/v1/terminals",
        json={"session_target_id": "target-abc", "cols": 80, "rows": 24},
        headers=_HEADERS,
    ).json()
    resp = client.delete(f"/api/v1/terminals/{created['terminal_id']}", headers=_HEADERS)
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# WebSocket handshake rejections (before accept -> 1008)
# ---------------------------------------------------------------------------


def test_ws_missing_protocol_id_rejected(client):
    terminal_id, token = _create(client)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/api/v1/terminals/{terminal_id}",
            subprotocols=[token],  # protocol id missing
            headers=_ws_headers(),
        ):
            pass
    assert exc.value.code == 1008


def test_ws_bad_origin_rejected(client):
    terminal_id, token = _create(client)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/api/v1/terminals/{terminal_id}",
            subprotocols=["remo-terminal.v1", token],
            headers=_ws_headers("http://evil.example"),
        ):
            pass
    assert exc.value.code == 1008


def test_ws_bad_token_rejected(client):
    terminal_id, _token = _create(client)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/api/v1/terminals/{terminal_id}",
            subprotocols=["remo-terminal.v1", "bogus-token"],
            headers=_ws_headers(),
        ):
            pass
    assert exc.value.code == 1008


def test_ws_replayed_token_rejected(client):
    terminal_id, token = _create(client)
    # First upgrade consumes the token successfully.
    with client.websocket_connect(
        f"/api/v1/terminals/{terminal_id}",
        subprotocols=["remo-terminal.v1", token],
        headers=_ws_headers(),
    ) as ws:
        assert ws.receive_json()["type"] == "ready"
    # Replaying the now-consumed token must be rejected.
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/api/v1/terminals/{terminal_id}",
            subprotocols=["remo-terminal.v1", token],
            headers=_ws_headers(),
        ):
            pass
    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# WebSocket happy path (cat stand-in for ssh)
# ---------------------------------------------------------------------------


def test_ws_full_roundtrip_with_cat_standin(client):
    terminal_id, token = _create(client)
    with client.websocket_connect(
        f"/api/v1/terminals/{terminal_id}",
        subprotocols=["remo-terminal.v1", token],
        headers=_ws_headers(),
    ) as ws:
        # ready control frame arrives first.
        assert ws.receive_json() == {"v": 1, "type": "ready"}

        # Binary input round-trips back through the PTY (echo + cat output).
        ws.send_bytes(b"hello-remo\n")
        seen = b""
        for _ in range(10):
            seen += ws.receive_bytes()
            if b"hello-remo" in seen:
                break
        assert b"hello-remo" in seen

        # Resize control frame is accepted without error.
        ws.send_json({"v": 1, "type": "resize", "cols": 100, "rows": 40})

        # Ping -> pong. Leftover binary echo frames may still be queued, so
        # skip binary frames until the pong text frame arrives.
        ws.send_json({"v": 1, "type": "ping"})
        pong = None
        for _ in range(20):
            msg = ws.receive()
            if "text" in msg and msg["text"] is not None:
                pong = json.loads(msg["text"])
                break
        assert pong == {"v": 1, "type": "pong"}

    # After the WS closes, the attachment is reaped -> disconnected, and the
    # local process is gone (registry no longer holds a live session).
    registry = client.app.state.terminal_registry
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        att = registry.get(terminal_id)
        if att is not None and att.state == TerminalState.DISCONNECTED:
            break
        time.sleep(0.05)
    att = registry.get(terminal_id)
    assert att is not None
    assert att.state == TerminalState.DISCONNECTED
    assert registry.get_session(terminal_id) is None
