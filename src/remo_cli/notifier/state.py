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

from remo_cli.notifier.models import AgentshRequest, ApprovalDecision


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


@dataclass
class PendingApproval:
    approval_id: str
    request: AgentshRequest
    future: asyncio.Future[ApprovalDecision]
    created_at: datetime = field(default_factory=_utcnow)


class PendingApprovals:
    """Concurrency-safe registry keyed by approval_id."""

    def __init__(self, max_pending: int) -> None:
        self._max_pending = max_pending
        self._entries: dict[str, PendingApproval] = {}
        self._lock = asyncio.Lock()

    def count(self) -> int:
        return len(self._entries)

    async def reserve(self, approval_id: str, request: AgentshRequest) -> PendingApproval:
        """Atomically reserve a slot + id and return a live PendingApproval.

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
