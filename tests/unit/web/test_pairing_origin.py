"""Origin-allowlist interaction tests (012-web-adopt-pairing, T022, R11).

The browser-only `/pairing/*` routes remain subject to the Origin allowlist,
while the CLI-facing `/api/v1/setup/*` origin-less exemption is intact.
"""

from __future__ import annotations

from ._pairing_support import bearer, make_client, mint


def test_mint_rejected_without_origin(state_dir):
    state_dir.adopted()
    client = make_client(state_dir)
    # No Origin header on a state-changing POST -> blocked by the allowlist.
    resp = client.post("/api/v1/pairing/mint")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_origin"


def test_mint_rejected_with_bad_origin(state_dir):
    state_dir.adopted()
    client = make_client(state_dir)
    resp = client.post("/api/v1/pairing/mint", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden_origin"


def test_setup_routes_remain_originless_exempt(state_dir):
    state_dir.adopted()
    client = make_client(state_dir)
    code = mint(client)
    # The CLI calls setup with NO Origin header (it has none) — still allowed.
    resp = client.put(
        "/api/v1/setup/registry",
        headers=bearer(code),  # deliberately no Origin
        json={
            "version": 1,
            "registry": [{"type": "incus", "name": "dev", "host": "10.0.0.5", "user": "remo"}],
            "host_keys": {},
        },
    )
    assert resp.status_code == 200
