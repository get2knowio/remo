"""Shared helpers for the 012 pairing app-level tests (dormancy/mint/origin).

Builds a TestClient over the real FastAPI app with a no-op discovery service
(so the lifespan never opens SSH) and a controllable operator-auth posture, plus
a `mint` helper that drives the real `/pairing/mint` endpoint.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from remo_cli.web import app as app_module

ORIGIN = "http://127.0.0.1:8080"


class _NoopDiscovery:
    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        return None


def make_client(
    state_dir,
    *,
    operator_auth: str = "none",
    forward_auth_header: str = "",
    **extra,
) -> TestClient:
    overrides = dict(
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
        allowed_origins=[ORIGIN],
        operator_auth=operator_auth,
        forward_auth_header=forward_auth_header,
    )
    overrides.update(extra)
    settings = state_dir.settings(**overrides)
    application = app_module.create_app(settings)
    application.state.discovery_service = _NoopDiscovery()
    return TestClient(application, base_url=ORIGIN)


def mint(client: TestClient, *, headers: dict[str, str] | None = None, origin: str = "adopt") -> str:
    """Mint a code through the real endpoint; return it. Raises on non-200."""
    hdrs = {"Origin": ORIGIN}
    if headers:
        hdrs.update(headers)
    resp = client.post(f"/api/v1/pairing/mint?origin={origin}", headers=hdrs)
    assert resp.status_code == 200, (resp.status_code, resp.text)
    return resp.json()["code"]


def bearer(code: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {code}"}
