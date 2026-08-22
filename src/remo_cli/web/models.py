"""Service-only, in-memory entities for the terminal subsystem (T034).

These are colocated in ``remo_cli.web`` (not ``remo_cli.models``) because
they are pure web-service runtime state with no CLI/registry meaning, per
``data-model.md`` ("colocate in src/remo_cli/web/ if service-only"). All are
ephemeral: no database, no server-side persistence (NFR-006).

The classified terminal error reuses :class:`remo_cli.models.discovery.TypedError`
so the shape lines up with the discovery layer's error envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from remo_cli.models.discovery import TypedError

__all__ = [
    "ExitInfo",
    "TerminalAttachment",
    "TerminalKind",
    "TerminalState",
    "TypedError",
    "WsToken",
]


class TerminalKind(str, Enum):
    """What a terminal attaches to (plan §2.3, host-shell terminal path).

    ``session`` -- the classic ``remo-host sessions attach`` project session.
    ``host_shell`` -- a plain interactive login shell on the host itself
    (host-admin-gated; needs no host tools).
    """

    SESSION = "session"
    HOST_SHELL = "host_shell"


class TerminalState(str, Enum):
    """Lifecycle state of a :class:`TerminalAttachment` (data-model.md)."""

    PENDING = "pending"
    CONNECTING = "connecting"
    READY = "ready"
    DISCONNECTED = "disconnected"
    CLOSED = "closed"
    ERROR = "error"


#: States that hold a live resource and so count toward the terminal caps.
LIVE_STATES: frozenset[TerminalState] = frozenset(
    {TerminalState.PENDING, TerminalState.CONNECTING, TerminalState.READY}
)


@dataclass
class ExitInfo:
    """Recorded when a terminal's underlying process terminates."""

    code: int
    classification: str | None = None


@dataclass
class TerminalAttachment:
    """Server-side ephemeral terminal (data-model.md, "TerminalAttachment")."""

    terminal_id: str
    #: The attach origin's opaque id: a ``SessionTarget.id`` for
    #: ``kind=session``, the instance id for ``kind=host_shell``.
    session_target_id: str
    state: TerminalState
    cols: int
    rows: int
    token_expires_at: str
    created_at: str
    last_activity_at: str
    client_id: str
    kind: TerminalKind = TerminalKind.SESSION
    exit: ExitInfo | None = None
    error: TypedError | None = None


@dataclass
class WsToken:
    """Single-use WebSocket authorization token (data-model.md, "WsToken").

    ``expires_at`` is expressed in the :class:`~remo_cli.web.tokens.TokenStore`'s
    injected clock domain (``time.monotonic`` by default), not wall-clock, so
    expiry comparisons are monotonic and testable with a fake clock. The
    human-facing wall-clock deadline lives on
    :attr:`TerminalAttachment.token_expires_at` instead.
    """

    value: str = field(repr=False)  # secret: never logged/reprd (FR-028/FR-049)
    terminal_id: str = ""
    session_target_id: str = ""
    expires_at: float = 0.0
    consumed: bool = False
