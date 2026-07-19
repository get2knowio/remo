"""Setup-surface pairing-gate contract (012-web-adopt-pairing).

Supersedes the 011 static-token matrix. Covers the `require_pairing_code` gate
(contracts/setup-api.md, FR-005/FR-006) over EVERY setup route:

- live session + correct code -> the route's own domain response, never a 404
- live session + wrong/missing/malformed bearer -> dormant 404 (never a 401)
- no live session -> dormant 404 with FastAPI's stock body, byte-identical to an
  unknown route (indistinguishable from absent), with or without a bearer
- non-setup surface -> unaffected by the pairing state / Authorization header
- comparison -> the verdict comes from `hmac.compare_digest` inside the pairing
  manager (constant-time), proven by a monkeypatch spy
- observability -> the wrong-code path logs route/method context but never the
  presented code; the no-session path logs nothing (silent as absent); the
  RedactingFilter backstop masks a worst-case interpolated code
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from remo_cli.web import app as app_module
from remo_cli.web import pairing as pairing_module
from remo_cli.web.logging_config import RedactingFilter
from remo_cli.web.pairing import PairingSession

_ORIGIN = "http://testserver"
_CODE = "unit-test-pairing-code"

_SETUP_ROUTES = [
    ("GET", "/api/v1/setup/status"),
    ("GET", "/api/v1/setup/identity"),
    ("PUT", "/api/v1/setup/registry"),
    ("POST", "/api/v1/setup/verify"),
]

_SETUP_ROUTES_VALID_CODE = [
    ("GET", "/api/v1/setup/status", 200),
    ("GET", "/api/v1/setup/identity", 200),
    ("PUT", "/api/v1/setup/registry", 422),  # got PAST the gate into domain code
    ("POST", "/api/v1/setup/verify", 200),
]

_NOT_FOUND_BODY = {"detail": "Not Found"}


class _NoopDiscovery:
    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        return None

    def get_snapshot(self) -> list[Any]:
        return []


def _inject_session(application, code: str = _CODE) -> None:
    application.state.pairing_manager._session = PairingSession(
        code=code, identity=None, origin="adopt", last_activity=time.monotonic(), ttl_s=1e9
    )


def _client(state_dir, *, live: bool = True) -> TestClient:
    settings = state_dir.settings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[_ORIGIN],
        operator_auth="none",
    )
    application = app_module.create_app(settings)
    application.state.discovery_service = _NoopDiscovery()
    if live:
        _inject_session(application)
    return TestClient(application, base_url=_ORIGIN)


def _request(client: TestClient, method: str, path: str, headers: dict[str, str]):
    kwargs: dict[str, Any] = {"headers": {"Origin": _ORIGIN, **headers}}
    if method in {"PUT", "POST"}:
        kwargs["json"] = {}
    return client.request(method, path, **kwargs)


# --- (a) valid code -> the route's own domain response ---------------------


@pytest.mark.parametrize(("method", "path", "expected"), _SETUP_ROUTES_VALID_CODE)
def test_valid_code_reaches_route_handler(state_dir, method, path, expected):
    state_dir.unconfigured()
    with _client(state_dir) as client:
        resp = _request(client, method, path, {"Authorization": f"Bearer {_CODE}"})
    assert resp.status_code == expected
    assert resp.status_code != 404
    if path.endswith("/registry"):
        assert resp.json()["reason"] == "invalid_payload"


def test_valid_code_accepts_case_insensitive_scheme(state_dir):
    state_dir.unconfigured()
    with _client(state_dir) as client:
        for header in (f"bearer {_CODE}", f"BEARER {_CODE}", f"Bearer  {_CODE} "):
            resp = _request(client, "GET", "/api/v1/setup/status", {"Authorization": header})
            assert resp.status_code == 200, f"header {header!r} should authenticate"


# --- (b) wrong/malformed bearer with a live session -> dormant 404 ---------


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing-header"),
        pytest.param({"Authorization": ""}, id="empty-value"),
        pytest.param({"Authorization": _CODE}, id="bare-code-no-scheme"),
        pytest.param({"Authorization": f"Basic {_CODE}"}, id="basic-scheme"),
        pytest.param({"Authorization": "Bearer"}, id="scheme-only"),
        pytest.param({"Authorization": f"Bearer {_CODE[:-1]}"}, id="near-miss"),
        pytest.param({"Authorization": f"Bearer {_CODE.upper()}"}, id="case-flipped"),
    ],
)
@pytest.mark.parametrize(("method", "path"), _SETUP_ROUTES)
def test_wrong_or_malformed_bearer_is_dormant_404(state_dir, method, path, headers):
    state_dir.unconfigured()
    with _client(state_dir) as client:
        resp = _request(client, method, path, headers)
    # FR-006: never a distinguishable 401.
    assert resp.status_code == 404
    assert resp.json() == _NOT_FOUND_BODY


# --- (c) no live session -> dormant 404, indistinguishable from absent -----


@pytest.mark.parametrize(("method", "path"), _SETUP_ROUTES)
def test_no_session_is_404_even_with_a_bearer(state_dir, method, path):
    state_dir.unconfigured()
    with _client(state_dir, live=False) as client:
        resp = _request(client, method, path, {"Authorization": "Bearer anything-at-all"})
    assert resp.status_code == 404
    assert resp.json() == _NOT_FOUND_BODY


def test_dormant_404_is_indistinguishable_from_unknown_route(state_dir):
    state_dir.unconfigured()
    with _client(state_dir, live=False) as client:
        dormant = client.get("/api/v1/setup/status")
        unknown = client.get("/api/v1/setup/no-such-route")
        unrelated = client.get("/api/v1/definitely-not-a-route")
    assert dormant.status_code == unknown.status_code == unrelated.status_code == 404
    assert dormant.json() == unknown.json() == unrelated.json() == _NOT_FOUND_BODY


# --- (d) non-setup surface unaffected --------------------------------------


def test_non_setup_surface_ignores_authorization_header(state_dir):
    state_dir.adopted()
    with _client(state_dir) as client:
        bare = client.get("/api/v1/ready")
        good = client.get("/api/v1/ready", headers={"Authorization": f"Bearer {_CODE}"})
        bad = client.get("/api/v1/ready", headers={"Authorization": "Bearer wrong"})
    assert bare.status_code == good.status_code == bad.status_code
    assert bare.json() == good.json() == bad.json()


# --- (e) constant-time comparison via the pairing manager ------------------


class _SpyHmac:
    def __init__(self, forced: bool | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._forced = forced

    def compare_digest(self, a, b) -> bool:
        import hmac as real_hmac

        self.calls.append((a, b))
        return real_hmac.compare_digest(a, b) if self._forced is None else self._forced


def test_code_check_uses_compare_digest(state_dir, monkeypatch):
    state_dir.unconfigured()
    spy = _SpyHmac()
    monkeypatch.setattr(pairing_module, "hmac", spy)
    with _client(state_dir) as client:
        ok = client.get("/api/v1/setup/status", headers={"Authorization": f"Bearer {_CODE}"})
        bad = client.get("/api/v1/setup/status", headers={"Authorization": "Bearer nope"})
    assert ok.status_code == 200
    assert bad.status_code == 404
    # The comparison is done on UTF-8 bytes (never str — a non-ASCII bearer
    # would make hmac.compare_digest raise on str and crash the gate).
    assert (_CODE.encode(), _CODE.encode()) in spy.calls
    assert (b"nope", _CODE.encode()) in spy.calls


def test_compare_digest_verdict_is_authoritative(state_dir, monkeypatch):
    """Force compare_digest False: even the correct code must 404."""
    state_dir.unconfigured()
    monkeypatch.setattr(pairing_module, "hmac", _SpyHmac(forced=False))
    with _client(state_dir) as client:
        resp = client.get("/api/v1/setup/status", headers={"Authorization": f"Bearer {_CODE}"})
    assert resp.status_code == 404


# --- (f) observability ------------------------------------------------------

_SETUP_LOGGER = "remo_cli.web.setup"
_PRESENTED = "PRESENTEDsecretCODExyzzy123456"


def _gate_failure_records(caplog):
    return [
        r
        for r in caplog.records
        if r.name == _SETUP_LOGGER and "no valid pairing code" in r.getMessage()
    ]


@pytest.mark.parametrize(("method", "path"), _SETUP_ROUTES)
def test_wrong_code_logs_route_context(state_dir, caplog, method, path):
    state_dir.unconfigured()
    with caplog.at_level(logging.WARNING, logger=_SETUP_LOGGER):
        with _client(state_dir) as client:
            resp = _request(client, method, path, {"Authorization": f"Bearer {_PRESENTED}"})
    assert resp.status_code == 404
    records = _gate_failure_records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert method in message and path in message


def test_wrong_code_log_never_contains_presented_code(state_dir, caplog):
    state_dir.unconfigured()
    with caplog.at_level(logging.WARNING, logger=_SETUP_LOGGER):
        with _client(state_dir) as client:
            resp = _request(
                client, "PUT", "/api/v1/setup/registry", {"Authorization": f"Bearer {_PRESENTED}"}
            )
    assert resp.status_code == 404
    assert _gate_failure_records(caplog)
    fragments = (_PRESENTED, _PRESENTED[:12], _PRESENTED[-12:], _CODE)
    for record in caplog.records:
        rendered = record.getMessage() + " " + str(record.args or "")
        for fragment in fragments:
            assert fragment not in rendered


def test_no_session_404_emits_no_gate_failure_log(state_dir, caplog):
    state_dir.unconfigured()
    with caplog.at_level(logging.DEBUG, logger=_SETUP_LOGGER):
        with _client(state_dir, live=False) as client:
            resp = client.get(
                "/api/v1/setup/status", headers={"Authorization": f"Bearer {_PRESENTED}"}
            )
    assert resp.status_code == 404
    assert _gate_failure_records(caplog) == []


def test_simulated_code_log_line_is_redacted(state_dir, caplog):
    state_dir.unconfigured()
    with caplog.at_level(logging.WARNING, logger=_SETUP_LOGGER):
        with _client(state_dir):
            logging.getLogger(_SETUP_LOGGER).warning(
                "rejected request with Authorization: Bearer %s", _PRESENTED
            )
    [record] = [r for r in caplog.records if r.name == _SETUP_LOGGER]
    RedactingFilter().filter(record)
    rendered = record.getMessage()
    assert _PRESENTED not in rendered
    assert "<redacted>" in rendered
    assert "rejected request" in rendered
