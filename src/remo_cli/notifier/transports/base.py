"""The NotificationTransport abstract base class.

The notifier core depends only on this interface; Telegram is the sole v1
implementation. See specs/007-notifier-sidecar/contracts/transport.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from remo_cli.notifier.models import ApprovalDecision, ApprovalRequest

ResponseCallback = Callable[[ApprovalDecision], None]


class NotificationTransport(ABC):
    """Delivers approval requests to a human and reports their decision.

    Lifecycle: ``start()`` once at app startup; ``stop()`` once at shutdown.
    Between them the server calls ``send_approval_request()`` per accepted
    request and may call ``cancel()`` to retract one resolved by other means.
    """

    #: Human-facing transport name, surfaced in GET /v1/health.
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
        request: ApprovalRequest,
        on_response: ResponseCallback,
    ) -> None:
        """Deliver one request to the human.

        MUST raise on delivery failure (the server maps this to 503 and holds no
        pending slot — FR-010a). On success, returns once the notification has
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
        """Readiness signal. When False, the server answers /v1/approve 503."""
        return True
