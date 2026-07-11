# Contract: `NotificationTransport` ABC

Location: `src/remo_cli/notifier/transports/base.py`. The notifier core depends only on this interface; Telegram is the sole v1 implementation (`transports/telegram.py`). Future Slack/Discord/ntfy backends subclass it without touching intake or registry logic (FR-015).

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from remo_cli.notifier.models import ApprovalDecision, ApprovalRequest


class NotificationTransport(ABC):
    """Delivers approval requests to a human and reports their decision.

    Lifecycle: start() once at app startup; stop() once at shutdown. Between
    them, the server calls send_approval_request() per accepted request and may
    call cancel() to retract one resolved by other means.
    """

    name: str  # e.g. "telegram" — surfaced in /v1/health

    @abstractmethod
    async def start(self) -> None:
        """Begin receiving human input (e.g. start long-polling). Idempotent."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop receiving input and release resources. Idempotent; safe after start failure."""

    @abstractmethod
    async def send_approval_request(
        self,
        request: ApprovalRequest,
        on_response: Callable[[ApprovalDecision], None],
    ) -> None:
        """Deliver one request to the human.

        MUST raise on delivery failure (the server maps this to 503 and holds no
        pending slot — FR-010a). On success, returns once the notification is
        delivered; the human's eventual tap invokes on_response(decision) exactly
        once with an authorized decision. on_response is a synchronous, loop-safe
        callback that resolves the pending approval's Future.
        """

    @abstractmethod
    async def cancel(self, approval_id: str) -> None:
        """Retract a still-displayed request (e.g. resolved elsewhere or on shutdown).

        Edits/annotates the human-facing message to reflect the non-human outcome.
        Idempotent and a no-op for unknown ids.
        """

    async def healthy(self) -> bool:
        """Optional readiness signal. Default True; transports may override.

        When False, the server answers new /v1/approve calls with 503 (FR-007).
        """
        return True
```

## Behavioral contract

| Guarantee | Requirement |
|-----------|-------------|
| `on_response` is called at most once per request, only with an **authorized** human decision | FR-008, FR-011 |
| Unauthorized chats / unknown approval ids are ignored (no `on_response`) | FR-011, FR-012, edge cases |
| `send_approval_request` raises ⇒ server returns 503, no pending slot held | FR-010a |
| `cancel` edits the human-facing message to show the final non-human outcome | FR-013 |
| `start`/`stop` are idempotent and tolerate partial init | Constitution III (idempotent), shutdown edge case |
| `name` reflected verbatim in `/v1/health.transport` | FR-016 |

## Telegram implementation notes (normative for v1)

- Built on `telegram.ext.Application`; long-polling via `updater.start_polling()` inside FastAPI lifespan; **never** `run_polling()` and **never** webhook mode (R1, FR-014).
- Message body and inline keyboard exactly as in `telegram-message.md`.
- `callback_data`: `approve:{approval_id}` / `deny:{approval_id}`.
- A `CallbackQueryHandler` checks the originating chat equals `authorized_chat_id` (else ignore, FR-011), parses `verb:approval_id`, and calls `on_response(ApprovalDecision(...))` guarded so a non-pending id is a no-op (FR-012).
- On resolution it edits the original message (`✅ Approved by @user at HH:MM` / `❌ Denied …` / `⌛ Timed out — denied (fail-secure)` / cancelled) (FR-013).
- Bot token read from file at startup, kept in memory only (FR-019); never logged (FR-017).
