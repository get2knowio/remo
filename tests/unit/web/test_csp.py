"""Content-Security-Policy tests (T062, FR-051).

Verifies `web/app.py`'s `_origin_allowlist_and_csp` middleware attaches a
restrictive `Content-Security-Policy` header to every HTTP response, and that
the policy stays within the "same-origin, no-CDN" bounds required by FR-038/
FR-051: no wildcard source, no bare external scheme, and the one WASM-eval
exception the Ghostty terminal renderer actually needs.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from remo_cli.web import app as app_module
from remo_cli.web.config import WebSettings

_ORIGIN = "http://testserver"

#: A source token is "safe" if it's a CSP keyword (quoted, e.g. 'self') or the
#: `data:` scheme used only for inline SVG/icon-ish img-src content here. Any
#: other bare token (a wildcard, a bare scheme like `http:`, or an external
#: host) is disallowed.
_SAFE_TOKENS = {
    "'self'",
    "'none'",
    "'unsafe-inline'",
    "'wasm-unsafe-eval'",
    "data:",
}

#: Directive names themselves (not sources) -- skip these when scanning.
_DIRECTIVE_NAME_RE = re.compile(r"^[a-z-]+$")


def _settings() -> WebSettings:
    return WebSettings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[_ORIGIN],
        ssh_control_dir="/tmp/remo-ssh-test-csp",
    )


def _client() -> TestClient:
    application = app_module.create_app(_settings())
    return TestClient(application, base_url=_ORIGIN)


def test_health_response_carries_csp_header():
    with _client() as client:
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert "Content-Security-Policy" in resp.headers


def test_csp_contains_default_src_self():
    with _client() as client:
        resp = client.get("/api/v1/health")
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp


def test_csp_contains_wasm_unsafe_eval_for_ghostty():
    with _client() as client:
        resp = client.get("/api/v1/health")
    csp = resp.headers["Content-Security-Policy"]
    assert "'wasm-unsafe-eval'" in csp


def test_csp_has_no_wildcard_or_bare_external_scheme_sources():
    with _client() as client:
        resp = client.get("/api/v1/health")
    csp = resp.headers["Content-Security-Policy"]

    directives = [d.strip() for d in csp.split(";") if d.strip()]
    assert directives, "CSP header must not be empty"

    for directive in directives:
        parts = directive.split()
        name, sources = parts[0], parts[1:]
        assert _DIRECTIVE_NAME_RE.match(name), f"unexpected directive name: {name!r}"
        for source in sources:
            assert source != "*", f"{name} lists a wildcard source"
            assert not source.startswith("http://"), (
                f"{name} lists a bare http:// source: {source!r}"
            )
            assert not source.startswith("https://"), (
                f"{name} lists a bare https:// source: {source!r}"
            )
            assert source in _SAFE_TOKENS, (
                f"{name} lists an unrecognized/unsafe source: {source!r}"
            )


def test_csp_has_no_bare_ws_scheme_source():
    # connect-src 'self' alone already covers same-origin ws:/wss: upgrades in
    # modern browsers; a bare `ws:`/`wss:` token would be strictly more
    # permissive (any host on that scheme) and is intentionally not present.
    with _client() as client:
        resp = client.get("/api/v1/health")
    csp = resp.headers["Content-Security-Policy"]
    assert "ws:" not in csp
    assert "wss:" not in csp


def test_csp_includes_hardening_directives():
    with _client() as client:
        resp = client.get("/api/v1/health")
    csp = resp.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp


def test_originless_setup_request_bypasses_origin_check(tmp_path, monkeypatch):
    # The setup API is bearer-token-only (no ambient credentials), so the
    # browser-CSRF origin check exempts Origin-less requests to
    # /api/v1/setup/* — this is what lets the `remo web adopt` CLI (and its
    # --via tunnel) talk to a live service. The bearer dependency still gates
    # the route: with a token configured and presented, the request reaches
    # domain validation (422 for a garbage body), never 403.
    settings = _settings()
    settings.api_token = "csp-test-token"
    settings.web_identity_dir = tmp_path / "web-identity"
    monkeypatch.setenv("REMO_HOME", str(tmp_path))
    application = app_module.create_app(settings)
    with TestClient(application, base_url=_ORIGIN) as client:
        resp = client.put(
            "/api/v1/setup/registry",
            json={},
            headers={"Authorization": "Bearer csp-test-token"},
        )
    assert resp.status_code == 422


def test_setup_request_with_disallowed_origin_still_rejected():
    # A present-but-disallowed Origin on a setup route is still a 403 — only
    # the Origin-less (non-browser) case is exempt.
    settings = _settings()
    settings.api_token = "csp-test-token"
    application = app_module.create_app(settings)
    with TestClient(application, base_url=_ORIGIN) as client:
        resp = client.put(
            "/api/v1/setup/registry",
            json={},
            headers={
                "Authorization": "Bearer csp-test-token",
                "Origin": "http://evil.example",
            },
        )
    assert resp.status_code == 403


def test_originless_non_setup_request_still_rejected():
    # The exemption is scoped to /api/v1/setup/* — every other state-changing
    # route still requires an allowed Origin.
    application = app_module.create_app(_settings())
    with TestClient(application, base_url=_ORIGIN) as client:
        resp = client.post(
            "/api/v1/terminals",
            json={"session_target_id": "x", "cols": 80, "rows": 24},
        )
    assert resp.status_code == 403


def test_csp_present_on_forbidden_origin_response_too():
    # Even a 403 (rejected-origin) response should carry the CSP header --
    # it's attached unconditionally in the middleware after call_next().
    application = app_module.create_app(_settings())
    with TestClient(application, base_url=_ORIGIN) as client:
        resp = client.post(
            "/api/v1/terminals",
            json={"session_target_id": "x", "cols": 80, "rows": 24},
            headers={"Origin": "http://evil.example"},
        )
    assert resp.status_code == 403
    assert "Content-Security-Policy" in resp.headers
