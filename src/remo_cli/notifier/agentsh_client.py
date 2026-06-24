"""agentsh approval REST client (CORE) — the approver edge of the notifier.

The notifier is an **approver client**: it polls agentsh for pending approvals
and resolves each one. The human's decision flows human → channel → notifier →
agentsh; the human never calls agentsh directly (FR-020/FR-022).

Verified against agentsh source on 2026-06-01 (``internal/approvals/manager.go``,
``internal/api/app.go``). Pin to a verified agentsh version when integrating —
these are internal types that may evolve. See contracts/agentsh-integration.md.
"""

from __future__ import annotations

import httpx

from remo_cli.notifier.logging_setup import get_logger
from remo_cli.notifier.models import AgentshRequest, Decision

#: The agentsh revision whose approval API this client was verified against.
AGENTSH_PINNED_VERSION = "canyonroad/agentsh @ 2026-06-01 (api mode)"

_log = get_logger("remo_notifier.agentsh")

# Internal allow/deny -> agentsh wire vocabulary (contracts/agentsh-integration.md).
_WIRE = {Decision.allow: "approve", Decision.deny: "deny"}


class AgentshError(Exception):
    """Any failure talking to agentsh. Callers treat it fail-secure."""


class AgentshClient:
    """Polls ``GET /api/v1/approvals`` and resolves ``POST /api/v1/approvals/{id}``.

    Auth is an approver ``X-API-Key`` header read from the secret file. When
    agentsh has auth disabled, its approvals API is disabled entirely (anti
    self-approval) — calls then fail, and the notifier stays fail-secure.
    """

    def __init__(self, *, api_url: str, api_key: str, timeout: float = 10.0) -> None:
        self._base = api_url.rstrip("/")
        self._headers = {"X-API-Key": api_key}
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base, headers=self._headers, timeout=self._timeout
            )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise AgentshError("agentsh client not started")
        return self._client

    async def poll(self) -> list[AgentshRequest]:
        """Fetch the authoritative list of pending approvals.

        Raises ``AgentshError`` on any transport/auth/parse failure so the caller
        can mark agentsh unreachable and retry — never silently treating an
        outage as "no approvals" beyond the retry loop.
        """
        try:
            resp = await self._http().get("/api/v1/approvals")
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AgentshError(f"poll failed: {exc}") from exc
        items = payload if isinstance(payload, list) else payload.get("approvals", [])
        out: list[AgentshRequest] = []
        for raw in items or []:
            try:
                out.append(AgentshRequest.model_validate(raw))
            except Exception as exc:  # noqa: BLE001 - skip malformed entries, keep the rest
                _log.warning("agentsh_request_parse_failed", error=str(exc))
        return out

    async def resolve(self, approval_id: str, decision: Decision, *, reason: str = "") -> bool:
        """Resolve one approval. ``allow`` -> ``approve``; anything else -> ``deny``.

        Returns True on a successful resolution; raises ``AgentshError`` on
        failure (the caller logs it — the human's intent is preserved on retry,
        and agentsh's own ``ExpiresAt`` denies if we never succeed).
        """
        wire = _WIRE.get(decision, "deny")
        body = {"decision": wire, "reason": reason}
        try:
            resp = await self._http().post(f"/api/v1/approvals/{approval_id}", json=body)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AgentshError(f"resolve failed for {approval_id}: {exc}") from exc
        _log.info("agentsh_resolved", approval_id=approval_id, decision=wire)
        return True
