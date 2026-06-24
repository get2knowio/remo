"""The NotificationTransport abstract base class.

The notifier core depends only on this interface; a channel package supplies a
concrete implementation. The delivered unit is agentsh's ``Request`` (spec 008).
See contracts/channel-extension.md and contracts/agentsh-integration.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from remo_cli.notifier.models import AgentshRequest, ApprovalDecision

ResponseCallback = Callable[[ApprovalDecision], None]


class NotificationTransport(ABC):
    """Delivers approval requests to a human and reports their decision.

    Lifecycle: ``start()`` once at app startup; ``stop()`` once at shutdown.
    Between them the core calls ``send_approval_request()`` per delivered agentsh
    ``Request`` and may call ``cancel()`` to retract one resolved by other means.
    """

    #: Human-facing transport / channel name, surfaced in GET /v1/health.
    name: str = "base"

    @abstractmethod
    async def start(self) -> None:
        """Begin receiving human input (e.g. start long-polling). Idempotent."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop receiving input and release resources.

        Idempotent and safe to call after a failed ``start()``.
        """

    @abstractmethod
    async def send_approval_request(
        self,
        request: AgentshRequest,
        on_response: ResponseCallback,
    ) -> None:
        """Deliver one agentsh ``Request`` to the human.

        MUST raise on delivery failure (the core then resolves nothing and frees
        the slot — FR-008/FR-010a). On success, returns once the notification has
        been delivered; the human's eventual authorized tap invokes
        ``on_response(decision)`` exactly once. ``on_response`` is synchronous
        and loop-safe; it resolves the pending approval's Future.
        """

    @abstractmethod
    async def cancel(self, approval_id: str, *, outcome: str = "cancelled") -> None:
        """Retract a still-displayed request (resolved elsewhere / on shutdown).

        ``outcome`` is one of ``"timeout"`` or ``"cancelled"`` and selects the
        human-facing message edit. Idempotent; a no-op for unknown ids.
        """

    async def healthy(self) -> bool:
        """Readiness signal. When False, the core delivers nothing."""
        return True
