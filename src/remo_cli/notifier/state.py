"""In-memory registry of pending approvals.

No persistence (FR-009). A registry-level lock makes the capacity gate (FR-034)
and duplicate-id gate (FR-003a) race-free; the send-after-reserve flow honors
FR-010a (no slot held for a request whose notification failed). See
data-model.md and research R2.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from remo_cli.notifier.models import AgentshRequest, ApprovalDecision, Decision


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RegisterError(str, Enum):
    duplicate = "duplicate"
    at_capacity = "at_capacity"


class RegistrationFailed(Exception):
    """Raised when a slot cannot be reserved (duplicate id or at capacity)."""

    def __init__(self, reason: RegisterError) -> None:
        super().__init__(reason.value)
        self.reason = reason


# Responder stamped on a fail-secure deny minted by source removal (spec 009 R9).
# A dispatch coroutine awaiting the future uses it to skip a redundant agentsh
# resolve — the registry already issued the best-effort deny on the wire.
DRAINED_RESPONDER = "system:source-removed"


@dataclass
class PendingApproval:
    approval_id: str  # the core-minted, colon-free delivery id (spec 009 R3)
    request: AgentshRequest
    future: asyncio.Future[ApprovalDecision]
    created_at: datetime = field(default_factory=_utcnow)
    # Delivery-id mapping (spec 009 R3): the owning source + the *real* agentsh id
    # this delivery resolves against. ``source_id`` is None for source-unaware
    # entries (the local ``/v1/test`` injection path).
    source_id: str | None = None
    epoch: int = 0
    agentsh_approval_id: str | None = None


class PendingApprovals:
    """Concurrency-safe registry keyed by approval_id."""

    def __init__(self, max_pending: int) -> None:
        self._max_pending = max_pending
        self._entries: dict[str, PendingApproval] = {}
        self._lock = asyncio.Lock()

    def count(self) -> int:
        return len(self._entries)

    async def reserve(
        self,
        approval_id: str,
        request: AgentshRequest,
        *,
        source_id: str | None = None,
        epoch: int = 0,
        agentsh_approval_id: str | None = None,
    ) -> PendingApproval:
        """Atomically reserve a slot + id and return a live PendingApproval.

        ``approval_id`` is the core-minted, colon-free delivery id (spec 009 R3);
        the optional ``source_id``/``epoch``/``agentsh_approval_id`` record the
        delivery-id mapping so a removed source's entries can be drained
        (``drain_source``) and the human's tap resolves against the right source.

        Raises RegistrationFailed(duplicate) if the id is already pending, or
        RegistrationFailed(at_capacity) if the registry is full. The caller MUST
        call ``release()`` if a later step (e.g. notification send) fails so the
        slot is not held for an undelivered request (FR-010a).
        """
        async with self._lock:
            if approval_id in self._entries:
                raise RegistrationFailed(RegisterError.duplicate)
            if len(self._entries) >= self._max_pending:
                raise RegistrationFailed(RegisterError.at_capacity)
            loop = asyncio.get_running_loop()
            entry = PendingApproval(
                approval_id=approval_id,
                request=request,
                future=loop.create_future(),
                source_id=source_id,
                epoch=epoch,
                agentsh_approval_id=agentsh_approval_id,
            )
            self._entries[approval_id] = entry
            return entry

    async def release(self, approval_id: str) -> None:
        """Drop a reserved-but-unsent entry, freeing its slot (FR-010a)."""
        async with self._lock:
            entry = self._entries.pop(approval_id, None)
        if entry is not None and not entry.future.done():
            entry.future.cancel()

    def discard(self, approval_id: str) -> None:
        """Remove an entry without resolving (used after a timeout)."""
        self._entries.pop(approval_id, None)

    def resolve(self, approval_id: str, decision: ApprovalDecision) -> bool:
        """Resolve a pending approval with a decision.

        Returns True if it was pending and is now resolved; False if unknown or
        already resolved (late/duplicate callbacks are no-ops, FR-012).
        Loop-safe: invoked from the same event loop as the awaiter.
        """
        entry = self._entries.pop(approval_id, None)
        if entry is None:
            return False
        if not entry.future.done():
            entry.future.set_result(decision)
        return True

    async def wait(self, approval_id: str, timeout: float) -> ApprovalDecision:
        """Await the decision for a pending approval, bounded by ``timeout``.

        Raises asyncio.TimeoutError on expiry (caller maps to fail-secure deny)
        and KeyError if the id is not registered.
        """
        entry = self._entries.get(approval_id)
        if entry is None:
            raise KeyError(approval_id)
        return await asyncio.wait_for(entry.future, timeout=timeout)

    def drain(self, decision: ApprovalDecision) -> list[str]:
        """Resolve every pending approval (shutdown). Returns the drained ids."""
        ids = list(self._entries.keys())
        for approval_id in ids:
            entry = self._entries.pop(approval_id, None)
            if entry is not None and not entry.future.done():
                entry.future.set_result(decision)
        return ids

    def drain_source(self, source_id: str) -> list[str]:
        """Fail-secure deny every pending entry owned by ``source_id`` (spec 009 R9).

        Resolves each matching future to a deny stamped ``DRAINED_RESPONDER`` so
        no allow is ever delivered for a removed source — a guarantee that holds
        regardless of agentsh reachability. Returns the *real* agentsh approval
        ids that were pending, so the caller can issue a best-effort wire deny.
        """
        targets = [
            (aid, entry)
            for aid, entry in self._entries.items()
            if entry.source_id == source_id
        ]
        agentsh_ids: list[str] = []
        deny = ApprovalDecision(
            decision=Decision.deny, responder=DRAINED_RESPONDER, reason="source removed"
        )
        for aid, entry in targets:
            self._entries.pop(aid, None)
            if entry.agentsh_approval_id is not None:
                agentsh_ids.append(entry.agentsh_approval_id)
            if not entry.future.done():
                entry.future.set_result(deny)
        return agentsh_ids
