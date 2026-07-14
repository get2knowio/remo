"""Negative-security tests for the terminal REST + WebSocket surface (T068).

Reference: quickstart.md's "V6 -- Security rejections" scenario (SC-007):

    - `POST /terminals` with a fabricated/undiscovered `session_target_id` -> 404.
    - WS handshake from a wrong `Origin` -> rejected (1008).
    - Reuse a consumed or expired `ws_token` -> rejected (1008). Token never
      appears in server logs or URLs.

All of these are rejection paths that fail before any real SSH/PTY work
happens, so -- unlike the Docker-gated fixtures in `test_nine_terminals.py` /
`test_terminal_attach.py` -- no disposable SSH container is needed here. This
module reuses `tests/unit/web/test_terminals_api.py`'s no-SSH technique: a
`_StubDiscovery` standing in for `DiscoveryService.find_target`/`find_host`,
and `build_attach_argv` monkeypatched to a trivial `["cat"]` for the one
scenario (replay-after-a-real-upgrade) that needs a token actually consumed
by a real WS accept first.

The log-redaction guarantee this module adds is deliberately narrower than
(and complementary to) `tests/unit/web/test_log_redaction.py`: that file
proves (a) no source file interpolates a secret-shaped variable into a log
call, and (b) the `RedactingFilter` masks secret-shaped substrings when given
a fabricated record. This module instead runs the REAL rejection code paths
end-to-end (fabricated target, bad Origin, replayed/expired token) with
`caplog` attached, and asserts the actual raw token VALUES issued during this
test run never appear in any captured log record -- end-to-end confidence for
exactly the paths SC-007 cares about, not just a source-grep/synthetic-record
guard.
"""

from __future__ import annotations

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

_ORIGIN = "http://testserver"
_HEADERS = {"Origin": _ORIGIN}


def _ws_headers(origin: str = _ORIGIN) -> dict[str, str]:
    """A FRESH headers dict per WS connect (`websocket_connect` mutates it in
    place -- see `test_terminals_api.py`'s module docstring for why every
    call site needs its own dict rather than sharing one).
    """
    return {"Origin": origin}


class _StubDiscovery:
    """Minimal stand-in for `DiscoveryService`: resolves exactly one target."""

    def __init__(self, target: SessionTarget | None, host: KnownHost | None) -> None:
        self._target = target
        self._host = host

    def find_target(self, target_id: str):
        if self._target is None:
            return None
        return self._target if target_id == self._target.id else None

    def find_host(self, instance_type: str, instance_name: str):
        if self._host is None:
            return None
        if instance_type == self._host.type and instance_name == self._host.name:
            return self._host
        return None

    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        return None


class _EmptyDiscovery:
    """A discovery service that has never discovered anything at all."""

    def find_target(self, target_id: str):
        return None

    def find_host(self, instance_type: str, instance_name: str):
        return None

    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        return None


@pytest.fixture
def target() -> SessionTarget:
    return SessionTarget(
        id="target-real-abc",
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


def _settings(**overrides) -> WebSettings:
    return WebSettings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[_ORIGIN],
        ssh_control_dir="/tmp/remo-ssh-test-security-rejections",
        **overrides,
    )


def _app_with_stub_discovery(monkeypatch, target, known_host, **settings_overrides):
    settings = _settings(**settings_overrides)
    application = app_module.create_app(settings)
    application.state.discovery_service = _StubDiscovery(target, known_host)
    monkeypatch.setattr(
        terminals_module,
        "build_attach_argv",
        lambda host, project, control_dir=None: ["cat"],
    )
    return application


# ---------------------------------------------------------------------------
# POST /terminals with a fabricated/undiscovered session_target_id -> 404
# (FR-050, quickstart V6 bullet 1)
# ---------------------------------------------------------------------------


def test_post_terminal_fabricated_target_is_404_against_populated_discovery(
    monkeypatch, target, known_host
):
    """A target id that was never discovered is rejected even when the
    discovery cache has OTHER real targets in it -- proves the lookup is a
    real per-id check, not "any target exists so allow it".
    """
    app = _app_with_stub_discovery(monkeypatch, target, known_host)
    with TestClient(app, base_url=_ORIGIN) as client:
        resp = client.post(
            "/api/v1/terminals",
            json={"session_target_id": "fabricated-id-never-discovered", "cols": 80, "rows": 24},
            headers=_HEADERS,
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_target"


def test_post_terminal_fabricated_target_is_404_against_empty_discovery(monkeypatch):
    """Same rejection when discovery has never found anything at all."""
    settings = _settings()
    app = app_module.create_app(settings)
    app.state.discovery_service = _EmptyDiscovery()
    monkeypatch.setattr(
        terminals_module,
        "build_attach_argv",
        lambda host, project, control_dir=None: ["cat"],
    )
    with TestClient(app, base_url=_ORIGIN) as client:
        resp = client.post(
            "/api/v1/terminals",
            json={"session_target_id": "totally-made-up", "cols": 80, "rows": 24},
            headers=_HEADERS,
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_target"


# ---------------------------------------------------------------------------
# WS handshake from a disallowed Origin -> 1008 (FR-048, quickstart V6 bullet 2)
# ---------------------------------------------------------------------------


def test_ws_handshake_disallowed_origin_is_rejected_1008(monkeypatch, target, known_host):
    app = _app_with_stub_discovery(monkeypatch, target, known_host)
    with TestClient(app, base_url=_ORIGIN) as client:
        created = client.post(
            "/api/v1/terminals",
            json={"session_target_id": target.id, "cols": 80, "rows": 24},
            headers=_HEADERS,
        ).json()
        terminal_id, token = created["terminal_id"], created["ws_token"]

        # settings.allowed_origins == [_ORIGIN] only; this Origin is not in it.
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                f"/api/v1/terminals/{terminal_id}",
                subprotocols=["remo-terminal.v1", token],
                headers=_ws_headers("http://attacker.example"),
            ):
                pass
    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# Reuse of a consumed ws_token -> 1008 (FR-049, quickstart V6 bullet 3)
# ---------------------------------------------------------------------------


def test_ws_replay_of_consumed_token_is_rejected_1008(monkeypatch, target, known_host):
    app = _app_with_stub_discovery(monkeypatch, target, known_host)
    with TestClient(app, base_url=_ORIGIN) as client:
        created = client.post(
            "/api/v1/terminals",
            json={"session_target_id": target.id, "cols": 80, "rows": 24},
            headers=_HEADERS,
        ).json()
        terminal_id, token = created["terminal_id"], created["ws_token"]

        # First upgrade legitimately consumes the token.
        with client.websocket_connect(
            f"/api/v1/terminals/{terminal_id}",
            subprotocols=["remo-terminal.v1", token],
            headers=_ws_headers(),
        ) as ws:
            assert ws.receive_json()["type"] == "ready"

        # A second attempt replaying the SAME (now-consumed) token value.
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                f"/api/v1/terminals/{terminal_id}",
                subprotocols=["remo-terminal.v1", token],
                headers=_ws_headers(),
            ):
                pass
    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# Expired ws_token -> 1008 (FR-049, quickstart V6 bullet 3)
# ---------------------------------------------------------------------------


def test_ws_expired_token_is_rejected_1008(monkeypatch, target, known_host):
    # A near-zero TTL (WebSettings.ws_token_ttl_s) so a short real sleep is
    # enough to push past expiry deterministically without mocking the clock.
    app = _app_with_stub_discovery(monkeypatch, target, known_host, ws_token_ttl_s=0.1)
    with TestClient(app, base_url=_ORIGIN) as client:
        created = client.post(
            "/api/v1/terminals",
            json={"session_target_id": target.id, "cols": 80, "rows": 24},
            headers=_HEADERS,
        ).json()
        terminal_id, token = created["terminal_id"], created["ws_token"]

        time.sleep(0.3)

        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                f"/api/v1/terminals/{terminal_id}",
                subprotocols=["remo-terminal.v1", token],
                headers=_ws_headers(),
            ):
                pass
    assert exc.value.code == 1008


# ---------------------------------------------------------------------------
# Tokens never appear in server logs, across ALL of the above rejection paths
# (FR-028/FR-049, quickstart V6). This is deliberately end-to-end: it runs the
# real rejection code with caplog attached and asserts the ACTUAL raw token
# values issued during this test never leak into a log record -- distinct
# from tests/unit/web/test_log_redaction.py's source-grep + synthetic-record
# guarantees (see this module's docstring).
# ---------------------------------------------------------------------------


def test_tokens_never_appear_in_server_logs_across_rejection_paths(
    monkeypatch, target, known_host, caplog
):
    caplog.set_level("DEBUG")
    issued_tokens: list[str] = []

    def _create_and_capture(client) -> tuple[str, str]:
        body = client.post(
            "/api/v1/terminals",
            json={"session_target_id": target.id, "cols": 80, "rows": 24},
            headers=_HEADERS,
        ).json()
        issued_tokens.append(body["ws_token"])
        return body["terminal_id"], body["ws_token"]

    # 1. Fabricated target (no token issued, but exercises the log path).
    app = _app_with_stub_discovery(monkeypatch, target, known_host)
    with TestClient(app, base_url=_ORIGIN) as client:
        client.post(
            "/api/v1/terminals",
            json={"session_target_id": "fabricated-nope", "cols": 80, "rows": 24},
            headers=_HEADERS,
        )

        # 2. Bad Origin.
        terminal_id, token = _create_and_capture(client)
        try:
            with client.websocket_connect(
                f"/api/v1/terminals/{terminal_id}",
                subprotocols=["remo-terminal.v1", token],
                headers=_ws_headers("http://attacker.example"),
            ):
                pass
        except WebSocketDisconnect:
            pass

        # 3. Replay.
        terminal_id, token = _create_and_capture(client)
        with client.websocket_connect(
            f"/api/v1/terminals/{terminal_id}",
            subprotocols=["remo-terminal.v1", token],
            headers=_ws_headers(),
        ) as ws:
            ws.receive_json()
        try:
            with client.websocket_connect(
                f"/api/v1/terminals/{terminal_id}",
                subprotocols=["remo-terminal.v1", token],
                headers=_ws_headers(),
            ):
                pass
        except WebSocketDisconnect:
            pass

    # 4. Expired token, on a second app instance with a near-zero TTL.
    expired_app = _app_with_stub_discovery(monkeypatch, target, known_host, ws_token_ttl_s=0.1)
    with TestClient(expired_app, base_url=_ORIGIN) as client:
        terminal_id, token = _create_and_capture(client)
        time.sleep(0.3)
        try:
            with client.websocket_connect(
                f"/api/v1/terminals/{terminal_id}",
                subprotocols=["remo-terminal.v1", token],
                headers=_ws_headers(),
            ):
                pass
        except WebSocketDisconnect:
            pass

    assert len(issued_tokens) == 3, "sanity: this scenario should have issued three real tokens"
    all_log_text = "\n".join(record.getMessage() for record in caplog.records)
    for raw_token in issued_tokens:
        assert raw_token not in all_log_text, (
            f"raw token value leaked into server logs: {raw_token!r}"
        )
