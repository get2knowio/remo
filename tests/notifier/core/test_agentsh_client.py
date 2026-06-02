"""Tests for the agentsh approver client (spec 008, T022 / FR-020..FR-023).

The decision routes human → notifier → agentsh: the client only ever resolves
on the notifier's behalf, never the other way around.
"""

from __future__ import annotations

import json

import httpx
import pytest

from remo_cli.notifier.agentsh_client import AgentshClient, AgentshError
from remo_cli.notifier.models import Decision


def _client(handler) -> AgentshClient:
    c = AgentshClient(api_url="http://agentsh:8080", api_key="approver-key")
    c._client = httpx.AsyncClient(  # noqa: SLF001 - inject a mock transport
        transport=httpx.MockTransport(handler),
        base_url="http://agentsh:8080",
        headers={"X-API-Key": "approver-key"},
    )
    return c


_PENDING = [
    {
        "id": "appr-1",
        "session_id": "s1",
        "kind": "file_delete",
        "target": "/ws/a.txt",
        "rule": "fs.delete",
        "message": "delete?",
        "expires_at": "2026-06-01T12:05:00Z",
    },
    {"id": "appr-2", "session_id": "s2", "kind": "command", "target": "rm -rf /tmp"},
]


async def test_poll_parses_request_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/approvals"
        assert request.headers["X-API-Key"] == "approver-key"
        return httpx.Response(200, json=_PENDING)

    reqs = await _client(handler).poll()
    assert [r.id for r in reqs] == ["appr-1", "appr-2"]
    assert reqs[0].kind == "file_delete"
    assert reqs[0].expires_at is not None


async def test_poll_tolerates_wrapped_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"approvals": _PENDING})

    reqs = await _client(handler).poll()
    assert len(reqs) == 2


async def test_poll_http_error_fails_secure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(AgentshError):
        await _client(handler).poll()


async def test_poll_auth_disabled_fails_secure() -> None:
    # agentsh disables the approvals API when auth is off (anti self-approval).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "approvals disabled"})

    with pytest.raises(AgentshError):
        await _client(handler).poll()


async def test_poll_skips_malformed_entries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "ok"}, {"no_id": True}])

    reqs = await _client(handler).poll()
    assert [r.id for r in reqs] == ["ok"]


async def test_resolve_allow_maps_to_approve() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/approvals/appr-1"
        assert request.headers["X-API-Key"] == "approver-key"
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    ok = await _client(handler).resolve("appr-1", Decision.allow, reason="human approved")
    assert ok is True
    assert seen["body"] == {"decision": "approve", "reason": "human approved"}


async def test_resolve_deny_maps_to_deny() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    await _client(handler).resolve("appr-2", Decision.deny, reason="timeout")
    assert seen["body"]["decision"] == "deny"


async def test_resolve_not_found_fails_secure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "approval not found"})

    with pytest.raises(AgentshError):
        await _client(handler).resolve("ghost", Decision.allow)
