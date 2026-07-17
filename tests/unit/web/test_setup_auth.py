"""Setup-surface auth contract matrix (011-web-adopt T037/T038).

Exhaustive coverage of the authentication table in
specs/011-web-adopt/contracts/setup-api.md, over EVERY setup route
(FR-020/FR-021/FR-022/FR-024):

- valid token        -> the route's own domain code, never 401/404-auth
- invalid token      -> 401 {"detail": "unauthorized"} (near-miss variants)
- missing header     -> 401
- malformed header   -> 401 (no Bearer prefix, empty value, Basic scheme, ...)
- token unset        -> 404 with FastAPI's stock body on ALL setup routes,
                        byte-identical to a genuinely unknown route
- non-setup surface  -> byte-identical behavior with and without a token
- comparison         -> the verdict comes from `hmac.compare_digest`
                        (constant-time), proven by a monkeypatch spy

The basic auth-inheritance proofs (each route 404s when unset / 401s on a
plain wrong token) live in tests/unit/web/test_setup_api.py; this module goes
deeper rather than repeating them.

T038 (failed-auth observability, FR-022/FR-024): every 401 emits a log line
with route/method context that never contains any fragment of the presented
credential, and a worst-case simulated line carrying an Authorization header
is masked by the configured `RedactingFilter` (same fabricated-record
convention as tests/unit/web/test_log_redaction.py).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from remo_cli.web import app as app_module
from remo_cli.web.api import setup as setup_api
from remo_cli.web.logging_config import RedactingFilter

_ORIGIN = "http://testserver"
_TOKEN = "unit-test-setup-token"

_SETUP_ROUTES = [
    ("GET", "/api/v1/setup/status"),
    ("GET", "/api/v1/setup/identity"),
    ("PUT", "/api/v1/setup/registry"),
    ("POST", "/api/v1/setup/verify"),
]

#: Expected status per route with a VALID token and a minimal request body
#: (`{}` for PUT/POST). Registry hits the 422 invalid_payload domain path --
#: still proof the request got PAST auth (contract: "route handles request").
_SETUP_ROUTES_VALID_TOKEN = [
    ("GET", "/api/v1/setup/status", 200),
    ("GET", "/api/v1/setup/identity", 200),
    ("PUT", "/api/v1/setup/registry", 422),
    ("POST", "/api/v1/setup/verify", 200),
]

_UNAUTHORIZED_BODY = {"detail": "unauthorized"}
_NOT_FOUND_BODY = {"detail": "Not Found"}


class _NoopDiscovery:
    """Stops the app lifespan's initial discovery from opening real SSH."""

    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        return None

    def get_snapshot(self) -> list[Any]:
        return []


def _client(state_dir, *, token: str = _TOKEN) -> TestClient:
    settings = state_dir.settings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[_ORIGIN],
        api_token=token,
    )
    application = app_module.create_app(settings)
    application.state.discovery_service = _NoopDiscovery()
    return TestClient(application, base_url=_ORIGIN)


def _request(client: TestClient, method: str, path: str, headers: dict[str, str]):
    """Issue *method path*; PUT/POST always carry an allowed Origin + `{}` body."""
    kwargs: dict[str, Any] = {"headers": {"Origin": _ORIGIN, **headers}}
    if method in {"PUT", "POST"}:
        kwargs["json"] = {}
    return client.request(method, path, **kwargs)


# ---------------------------------------------------------------------------
# (a) Valid token -> the route's own domain response, never an auth code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "expected"), _SETUP_ROUTES_VALID_TOKEN)
def test_valid_token_reaches_route_handler(state_dir, method, path, expected):
    state_dir.unconfigured()  # empty registry: verify does no SSH round-trips
    with _client(state_dir) as client:
        resp = _request(client, method, path, {"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == expected
    assert resp.status_code not in (401, 404)
    body = resp.json()
    assert body != _UNAUTHORIZED_BODY
    assert body != _NOT_FOUND_BODY
    if path.endswith("/registry"):
        # The 422 is the route's OWN domain shape, not an auth rejection.
        assert body["reason"] == "invalid_payload"


def test_valid_token_accepts_case_insensitive_scheme_and_padding(state_dir):
    """RFC 7235: the auth scheme is case-insensitive; token whitespace is trimmed."""
    state_dir.unconfigured()
    with _client(state_dir) as client:
        for header in (f"bearer {_TOKEN}", f"BEARER {_TOKEN}", f"Bearer  {_TOKEN} "):
            resp = _request(client, "GET", "/api/v1/setup/status", {"Authorization": header})
            assert resp.status_code == 200, f"header {header!r} should authenticate"


# ---------------------------------------------------------------------------
# (b) Invalid token -> 401 (near-miss variants, beyond the plain wrong token
#     already covered by test_setup_api.py's inheritance tests)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "presented",
    [
        pytest.param(_TOKEN[:-1], id="prefix-of-real-token"),
        pytest.param(_TOKEN + "x", id="real-token-plus-suffix"),
        pytest.param(_TOKEN.upper(), id="case-flipped-token"),
        pytest.param(f"{_TOKEN} {_TOKEN}", id="token-with-trailing-garbage"),
    ],
)
@pytest.mark.parametrize(("method", "path"), _SETUP_ROUTES)
def test_near_miss_tokens_are_401_on_every_route(state_dir, method, path, presented):
    state_dir.unconfigured()
    with _client(state_dir) as client:
        resp = _request(client, method, path, {"Authorization": f"Bearer {presented}"})
    assert resp.status_code == 401
    assert resp.json() == _UNAUTHORIZED_BODY


# ---------------------------------------------------------------------------
# (c) + (d) Missing / malformed Authorization header -> 401 on every route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing-header"),
        pytest.param({"Authorization": ""}, id="empty-value"),
        pytest.param({"Authorization": _TOKEN}, id="bare-token-no-scheme"),
        pytest.param({"Authorization": f"Basic {_TOKEN}"}, id="basic-scheme"),
        pytest.param({"Authorization": "Bearer"}, id="scheme-only-no-token"),
        pytest.param({"Authorization": "Bearer "}, id="scheme-and-space-only"),
        pytest.param({"Authorization": f"Bearer{_TOKEN}"}, id="no-space-after-scheme"),
        pytest.param({"Authorization": f"Token {_TOKEN}"}, id="token-scheme"),
    ],
)
@pytest.mark.parametrize(("method", "path"), _SETUP_ROUTES)
def test_missing_or_malformed_header_is_401_on_every_route(state_dir, method, path, headers):
    state_dir.unconfigured()
    with _client(state_dir) as client:
        resp = _request(client, method, path, headers)
    assert resp.status_code == 401
    assert resp.json() == _UNAUTHORIZED_BODY


# ---------------------------------------------------------------------------
# (e) Token unset -> 404 with FastAPI's stock body; surface indistinguishable
#     from absent (FR-021)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token", [pytest.param("", id="empty"), pytest.param("   ", id="whitespace-only")])
@pytest.mark.parametrize(("method", "path"), _SETUP_ROUTES)
def test_unset_or_blank_token_is_404_even_with_credentials(state_dir, method, path, token):
    """Fail closed: with no token configured, even a client PRESENTING a
    bearer credential gets the stock 404 -- never a 401 that would reveal the
    surface exists."""
    state_dir.unconfigured()
    with _client(state_dir, token=token) as client:
        resp = _request(client, method, path, {"Authorization": "Bearer anything-at-all"})
    assert resp.status_code == 404
    assert resp.json() == _NOT_FOUND_BODY


def test_unset_token_404_is_indistinguishable_from_unknown_route(state_dir):
    state_dir.unconfigured()
    with _client(state_dir, token="") as client:
        disabled = client.get("/api/v1/setup/status")
        unknown = client.get("/api/v1/setup/no-such-route")
        unrelated_unknown = client.get("/api/v1/definitely-not-a-route")
    assert disabled.status_code == unknown.status_code == unrelated_unknown.status_code == 404
    assert disabled.json() == unknown.json() == unrelated_unknown.json() == _NOT_FOUND_BODY


# ---------------------------------------------------------------------------
# (f) Non-setup surface unaffected by token configuration (FR-021/FR-023)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/ready", "/api/v1/hosts"])
def test_non_setup_surface_identical_with_and_without_token(state_dir, path):
    state_dir.adopted()
    with _client(state_dir, token=_TOKEN) as client:
        with_token = client.get(path)
    with _client(state_dir, token="") as client:
        without_token = client.get(path)
    assert with_token.status_code == without_token.status_code
    assert with_token.json() == without_token.json()
    # And a token-configured deployment never demands the token OUTSIDE /setup.
    assert with_token.status_code != 401


def test_non_setup_surface_ignores_authorization_header(state_dir):
    """A bearer header (right or wrong) on non-setup routes changes nothing."""
    state_dir.adopted()
    with _client(state_dir) as client:
        bare = client.get("/api/v1/ready")
        with_good = client.get("/api/v1/ready", headers={"Authorization": f"Bearer {_TOKEN}"})
        with_bad = client.get("/api/v1/ready", headers={"Authorization": "Bearer wrong"})
    assert bare.status_code == with_good.status_code == with_bad.status_code
    assert bare.json() == with_good.json() == with_bad.json()


# ---------------------------------------------------------------------------
# (g) Constant-time comparison: `hmac.compare_digest` IS the verdict (FR-022)
# ---------------------------------------------------------------------------


class _SpyHmac:
    """Stands in for setup.py's `hmac` module global; records + delegates."""

    def __init__(self, forced: bool | None = None) -> None:
        self.calls: list[tuple[bytes, bytes]] = []
        self._forced = forced

    def compare_digest(self, a: bytes, b: bytes) -> bool:
        import hmac as real_hmac

        self.calls.append((a, b))
        return real_hmac.compare_digest(a, b) if self._forced is None else self._forced


def test_token_check_calls_hmac_compare_digest_with_both_tokens(state_dir, monkeypatch):
    state_dir.unconfigured()
    spy = _SpyHmac()
    monkeypatch.setattr(setup_api, "hmac", spy)
    with _client(state_dir) as client:
        ok = client.get("/api/v1/setup/status", headers={"Authorization": f"Bearer {_TOKEN}"})
        bad = client.get("/api/v1/setup/status", headers={"Authorization": "Bearer nope"})
    assert ok.status_code == 200
    assert bad.status_code == 401
    # Every comparison went through compare_digest, presented vs configured.
    assert spy.calls == [
        (_TOKEN.encode(), _TOKEN.encode()),
        (b"nope", _TOKEN.encode()),
    ]


def test_compare_digest_verdict_is_authoritative(state_dir, monkeypatch):
    """Force compare_digest to False: even the CORRECT token must 401, proving
    no shortcut equality (`==`) path exists beside the constant-time check."""
    state_dir.unconfigured()
    spy = _SpyHmac(forced=False)
    monkeypatch.setattr(setup_api, "hmac", spy)
    with _client(state_dir) as client:
        resp = client.get("/api/v1/setup/status", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 401
    assert spy.calls == [(_TOKEN.encode(), _TOKEN.encode())]


def test_unset_token_never_reaches_comparison(state_dir, monkeypatch):
    """The 404 fail-closed branch short-circuits BEFORE any token comparison."""
    state_dir.unconfigured()
    spy = _SpyHmac()
    monkeypatch.setattr(setup_api, "hmac", spy)
    with _client(state_dir, token="") as client:
        resp = client.get("/api/v1/setup/status", headers={"Authorization": f"Bearer {_TOKEN}"})
    assert resp.status_code == 404
    assert spy.calls == []


# ---------------------------------------------------------------------------
# T038: failed-auth observability (FR-022/FR-024)
# ---------------------------------------------------------------------------

_SETUP_LOGGER = "remo_cli.web.setup"

#: Deliberately distinctive so any fragment leaking into a log is caught.
_PRESENTED = "PRESENTEDsecretCREDENTIALxyzzy123456"


def _auth_failure_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == _SETUP_LOGGER and "authentication failure" in r.getMessage()
    ]


@pytest.mark.parametrize(("method", "path"), _SETUP_ROUTES)
def test_401_emits_log_with_route_and_method_context(state_dir, caplog, method, path):
    state_dir.unconfigured()
    with caplog.at_level(logging.WARNING, logger=_SETUP_LOGGER):
        with _client(state_dir) as client:
            resp = _request(client, method, path, {"Authorization": f"Bearer {_PRESENTED}"})
    assert resp.status_code == 401

    records = _auth_failure_records(caplog)
    assert len(records) == 1, "exactly one auth-failure log line per rejected request"
    message = records[0].getMessage()
    assert method in message
    assert path in message
    assert records[0].levelno == logging.WARNING


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Authorization": f"Bearer {_PRESENTED}"}, id="wrong-bearer-token"),
        pytest.param({"Authorization": f"Basic {_PRESENTED}"}, id="basic-scheme"),
        pytest.param({"Authorization": _PRESENTED}, id="bare-credential"),
    ],
)
def test_401_log_never_contains_presented_credential(state_dir, caplog, headers):
    state_dir.unconfigured()
    with caplog.at_level(logging.WARNING, logger=_SETUP_LOGGER):
        with _client(state_dir) as client:
            resp = _request(client, "PUT", "/api/v1/setup/registry", headers)
    assert resp.status_code == 401
    assert _auth_failure_records(caplog), "the failure must still be observable"

    fragments = (_PRESENTED, _PRESENTED[:12], _PRESENTED[-12:], _TOKEN)
    for record in caplog.records:
        rendered = record.getMessage() + " " + str(record.args or "")
        for fragment in fragments:
            assert fragment not in rendered, (
                f"credential fragment {fragment!r} leaked into log: {rendered!r}"
            )


def test_disabled_surface_404_emits_no_auth_failure_log(state_dir, caplog):
    """Fail closed means silent-as-absent: the 404 path logs no auth failure."""
    state_dir.unconfigured()
    with caplog.at_level(logging.DEBUG, logger=_SETUP_LOGGER):
        with _client(state_dir, token="") as client:
            resp = client.get(
                "/api/v1/setup/status", headers={"Authorization": f"Bearer {_PRESENTED}"}
            )
    assert resp.status_code == 404
    assert _auth_failure_records(caplog) == []


def test_simulated_authorization_header_log_line_is_redacted(state_dir, caplog):
    """Backstop (FR-022): if a log line ever DID interpolate the raw header,
    the app's configured RedactingFilter masks it. Emit such a line through
    the real logger while the app (and thus `configure_logging()`'s
    filter-bearing handler) is live, then apply the filter to the captured
    record per test_log_redaction.py's fabricated-record convention."""
    state_dir.unconfigured()
    with caplog.at_level(logging.WARNING, logger=_SETUP_LOGGER):
        with _client(state_dir):  # create_app() ran configure_logging()
            logging.getLogger(_SETUP_LOGGER).warning(
                "rejected request with Authorization: Bearer %s", _PRESENTED
            )

    [record] = [r for r in caplog.records if r.name == _SETUP_LOGGER]
    RedactingFilter().filter(record)
    rendered = record.getMessage()
    assert _PRESENTED not in rendered
    assert "<redacted>" in rendered
    assert "rejected request" in rendered  # context survives, credential doesn't
