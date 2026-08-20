"""Setup-surface dormancy tests (012-web-adopt-pairing, T018/T019, US2).

Proves the setup surface is `404` (byte-identical to an unknown route) whenever
no live pairing session exists, responds to the live code once minted, returns
to dormant on idle expiry and on adoption completion, and that a wrong-but-
present code yields the same `404` — never a distinguishable `401`
(FR-005/FR-006, SC-001). Also asserts health/readiness bodies are byte-unchanged
across dormant / live-session / post-adoption states (SC-008).
"""

from __future__ import annotations

import pytest

from ._pairing_support import ORIGIN, bearer, make_client, mint

_SETUP_ROUTES = [
    ("GET", "/api/v1/setup/status"),
    ("GET", "/api/v1/setup/identity"),
    ("GET", "/api/v1/setup/registry"),
    ("PUT", "/api/v1/setup/registry"),
    ("POST", "/api/v1/setup/verify"),
    ("POST", "/api/v1/setup/end"),
]


def _request(client, method, path, headers=None):
    h = {"Origin": ORIGIN}
    if headers:
        h.update(headers)
    return client.request(method, path, headers=h, json={} if method in ("PUT", "POST") else None)


@pytest.mark.parametrize(("method", "path"), _SETUP_ROUTES)
def test_dormant_404_without_session(state_dir, method, path):
    state_dir.adopted()
    client = make_client(state_dir)
    # No mint -> dormant. With and without a bearer -> identical 404.
    for headers in (None, bearer("anything")):
        resp = _request(client, method, path, headers)
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not Found"}


def test_live_code_reaches_routes_then_wrong_code_is_404(state_dir):
    state_dir.adopted()
    client = make_client(state_dir)
    code = mint(client)
    ok = client.get("/api/v1/setup/status", headers=bearer(code))
    assert ok.status_code == 200
    # A wrong-but-present code is the SAME dormant 404, never a 401 (FR-006).
    bad = client.get("/api/v1/setup/status", headers=bearer("not-the-code"))
    assert bad.status_code == 404
    assert bad.json() == {"detail": "Not Found"}


def test_idle_expiry_returns_to_dormant(state_dir):
    state_dir.adopted()
    client = make_client(state_dir, pairing_ttl_s=60.0)
    code = mint(client)
    assert client.get("/api/v1/setup/status", headers=bearer(code)).status_code == 200
    # Deterministically age the session past its idle TTL (no reliance on the
    # monotonic clock advancing during the HTTP round-trip).
    session = client.app.state.pairing_manager._session
    session.last_activity -= 120.0
    resp = client.get("/api/v1/setup/status", headers=bearer(code))
    assert resp.status_code == 404


_PAYLOAD = {
    "version": 1,
    "registry": [{"type": "incus", "name": "dev", "host": "10.0.0.5", "user": "remo"}],
    "host_keys": {},
}


def test_adoption_completion_ends_session(state_dir):
    state_dir.adopted()
    client = make_client(state_dir)
    code = mint(client)
    applied = client.put(
        "/api/v1/setup/registry", headers={**bearer(code), "Origin": ORIGIN}, json=_PAYLOAD
    )
    assert applied.status_code == 200
    # The PUT does NOT end the session — the flow continues with the same code.
    assert client.get("/api/v1/setup/status", headers=bearer(code)).status_code == 200

    verified = client.post("/api/v1/setup/verify", headers={**bearer(code), "Origin": ORIGIN})
    assert verified.status_code == 200
    # Nor does verify (#158): it used to end the session here, which severed the
    # push flow's self-heal re-PUT + re-verify.
    assert client.get("/api/v1/setup/status", headers=bearer(code)).status_code == 200

    # FR-007: the client's explicit close ends it -> dormant again.
    ended = client.post("/api/v1/setup/end", headers={**bearer(code), "Origin": ORIGIN})
    assert ended.status_code == 200
    assert ended.json() == {"ended": True}
    assert client.get("/api/v1/setup/status", headers=bearer(code)).status_code == 404
    # And the close itself is now behind the dormant gate, like every setup route.
    assert (
        client.post("/api/v1/setup/end", headers={**bearer(code), "Origin": ORIGIN}).status_code
        == 404
    )


def test_self_heal_sequence_survives_on_one_code(state_dir):
    """#158 regression: the exact push sequence, including the self-heal pass
    that runs AFTER the first verify. Every step must stay authenticated by the
    single minted code until the CLI ends the session itself."""
    state_dir.adopted()
    client = make_client(state_dir)
    code = mint(client)
    auth = {**bearer(code), "Origin": ORIGIN}

    assert client.get("/api/v1/setup/status", headers=bearer(code)).status_code == 200
    assert client.get("/api/v1/setup/identity", headers=bearer(code)).status_code == 200

    first = client.put("/api/v1/setup/registry", headers=auth, json=_PAYLOAD)
    assert first.status_code == 200
    assert client.post("/api/v1/setup/verify", headers=auth).status_code == 200

    # The self-heal re-PUT + re-verify — both used to hit a dormant 404.
    second = client.put("/api/v1/setup/registry", headers=auth, json=_PAYLOAD)
    assert second.status_code == 200
    assert client.post("/api/v1/setup/verify", headers=auth).status_code == 200

    # The generation advances per successful PUT, which is what the workstation
    # caches for flap detection — so it must come from the LAST successful one.
    assert second.json()["mirror_generation"] == first.json()["mirror_generation"] + 1

    assert client.post("/api/v1/setup/end", headers=auth).status_code == 200
    assert client.get("/api/v1/setup/status", headers=bearer(code)).status_code == 404


def _health_ready(client):
    return (
        client.get("/api/v1/health").json(),
        client.get("/api/v1/ready").json(),
    )


def test_health_ready_unchanged_across_pairing_states(state_dir):
    state_dir.adopted()
    client = make_client(state_dir)

    dormant = _health_ready(client)
    mint(client)
    live = _health_ready(client)
    client.post("/api/v1/pairing/end", headers={"Origin": ORIGIN})
    post = _health_ready(client)

    # SC-008: pairing state (dormant/live/post) never changes health/ready output.
    assert dormant == live == post
