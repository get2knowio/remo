"""Mint-endpoint gating tests (012-web-adopt-pairing, T024, US3).

`POST /pairing/mint` is refused (403) without the trusted forward-auth header,
succeeds with it, records the operator subject in logs and on the session, and
never logs the code (FR-011/FR-012, SC-004).
"""

from __future__ import annotations

import logging

from ._pairing_support import ORIGIN, bearer, make_client


def test_mint_refused_without_header(state_dir):
    state_dir.adopted()
    client = make_client(state_dir, operator_auth="forward", forward_auth_header="X-Forwarded-User")
    resp = client.post("/api/v1/pairing/mint", headers={"Origin": ORIGIN})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "operator authentication required"}
    # No session created -> setup stays dormant.
    assert client.get("/api/v1/setup/status", headers=bearer("x")).status_code == 404


def test_mint_succeeds_with_header(state_dir):
    state_dir.adopted()
    client = make_client(state_dir, operator_auth="forward", forward_auth_header="X-Forwarded-User")
    resp = client.post(
        "/api/v1/pairing/mint",
        headers={"Origin": ORIGIN, "X-Forwarded-User": "alice"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-store"
    code = resp.json()["code"]
    assert client.get("/api/v1/setup/status", headers=bearer(code)).status_code == 200


def test_mint_disabled_when_unconfigured(state_dir):
    state_dir.adopted()
    client = make_client(state_dir, operator_auth="")  # no provider
    resp = client.post("/api/v1/pairing/mint", headers={"Origin": ORIGIN})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "operator authentication not configured"}


def test_mint_logs_subject_not_code(state_dir, caplog):
    state_dir.adopted()
    client = make_client(state_dir, operator_auth="forward", forward_auth_header="X-Forwarded-User")
    with caplog.at_level(logging.INFO, logger="remo_cli.web.pairing"):
        resp = client.post(
            "/api/v1/pairing/mint",
            headers={"Origin": ORIGIN, "X-Forwarded-User": "carol"},
        )
    code = resp.json()["code"]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "carol" in text
    assert code not in text
