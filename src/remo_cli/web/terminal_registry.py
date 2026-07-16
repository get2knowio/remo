"""Terminal attachment registry, caps, and single-use token binding (T037).

Owns the in-memory ``terminal_id -> TerminalAttachment`` map plus the live
:class:`~remo_cli.web.terminal.TerminalSession` per terminal, mints tokens via
a :class:`~remo_cli.web.tokens.TokenStore`, enforces the global/per-client caps
(defaults 32/16, FR-022), and drives the attachment lifecycle
(``pending -> connecting -> ready -> disconnected/closed/error``).

``client_id`` accounting: there is no auth layer yet, so the API layer derives
``client_id`` from the requesting connection's remote IP. That is a deliberate,
documented MVP choice — good enough for per-client cap fairness on a trusted
LAN, and swappable for a real principal once auth lands (FR-053) without
touching this registry.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from remo_cli.web.config import WebSettings
from remo_cli.web.models import (
    LIVE_STATES,
    ExitInfo,
    TerminalAttachment,
    TerminalState,
    WsToken,
)
from remo_cli.web.terminal import TerminalSession
from remo_cli.web.tokens import TokenStore

__all__ = ["CapReachedError", "TerminalRegistry"]

_TERMINAL_ID_NBYTES = 16  # 128-bit opaque public id.


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


class CapReachedError(Exception):
    """Raised by :meth:`TerminalRegistry.register` when a cap is hit (-> 429)."""

    def __init__(self, scope: str, limit: int) -> None:
        self.scope = scope
        self.limit = limit
        super().__init__(f"{scope} terminal cap reached (limit {limit})")


class TerminalRegistry:
    def __init__(
        self,
        settings: WebSettings,
        token_store: TokenStore | None = None,
    ) -> None:
        self._settings = settings
        self._tokens = token_store or TokenStore(settings.ws_token_ttl_s)
        self._attachments: dict[str, TerminalAttachment] = {}
        self._sessions: dict[str, TerminalSession] = {}

    @property
    def token_store(self) -> TokenStore:
        return self._tokens

    # -- cap accounting ---------------------------------------------------

    def _prune_expired_pending(self) -> None:
        """Drop PENDING attachments whose single-use token has already expired.

        A PENDING attachment counts toward the caps (LIVE_STATES) but only ever
        transitions forward when its WS handshake consumes the token. If that
        WS never arrives (client abandoned it, handshake rejected, tab closed),
        the token expires and the attachment can *never* progress — yet without
        this sweep it would occupy a cap slot forever, letting repeated failed
        opens exhaust the global/per-client cap. Reap them lazily on the next
        register() rather than running a background timer.
        """
        now = _now()
        stale = [
            tid
            for tid, att in self._attachments.items()
            if att.state is TerminalState.PENDING
            and (deadline := _parse_iso(att.token_expires_at)) is not None
            and deadline < now
        ]
        for tid in stale:
            self._attachments.pop(tid, None)
            self._sessions.pop(tid, None)

    def _live_count(self, client_id: str | None = None) -> int:
        return sum(
            1
            for att in self._attachments.values()
            if att.state in LIVE_STATES and (client_id is None or att.client_id == client_id)
        )

    # -- creation ---------------------------------------------------------

    async def register(
        self,
        session_target_id: str,
        cols: int,
        rows: int,
        client_id: str,
    ) -> tuple[TerminalAttachment, WsToken]:
        """Create a ``pending`` attachment + issue its single-use WS token.

        Enforces caps *before* issuing anything. Raises :class:`CapReachedError`
        when the global or per-client cap is already met, which the API layer
        maps to ``429``.
        """
        # Reap abandoned PENDING attachments (expired token, WS never arrived)
        # so they can't permanently hold cap slots.
        self._prune_expired_pending()
        if self._live_count() >= self._settings.terminal_cap_global:
            raise CapReachedError("global", self._settings.terminal_cap_global)
        if self._live_count(client_id) >= self._settings.terminal_cap_per_client:
            raise CapReachedError("per_client", self._settings.terminal_cap_per_client)

        terminal_id = secrets.token_urlsafe(_TERMINAL_ID_NBYTES)
        token = await self._tokens.issue(terminal_id, session_target_id)

        now = _now()
        attachment = TerminalAttachment(
            terminal_id=terminal_id,
            session_target_id=session_target_id,
            state=TerminalState.PENDING,
            cols=cols,
            rows=rows,
            token_expires_at=_iso(now + timedelta(seconds=self._settings.ws_token_ttl_s)),
            created_at=_iso(now),
            last_activity_at=_iso(now),
            client_id=client_id,
        )
        self._attachments[terminal_id] = attachment
        return attachment, token

    # -- reads ------------------------------------------------------------

    def get(self, terminal_id: str) -> TerminalAttachment | None:
        return self._attachments.get(terminal_id)

    def list_for_client(self, client_id: str) -> list[TerminalAttachment]:
        return [a for a in self._attachments.values() if a.client_id == client_id]

    def get_session(self, terminal_id: str) -> TerminalSession | None:
        return self._sessions.get(terminal_id)

    # -- token binding ----------------------------------------------------

    async def consume_token(self, value: str, terminal_id: str) -> WsToken | None:
        """Atomically consume *value* and confirm it is bound to *terminal_id*.

        Returns ``None`` for any failure (unknown/expired/replayed token, or a
        token bound to a different terminal) — the caller closes ``1008``.
        """
        token = await self._tokens.consume(value)
        if token is None or token.terminal_id != terminal_id:
            return None
        return token

    # -- lifecycle transitions -------------------------------------------

    def set_state(self, terminal_id: str, state: TerminalState) -> None:
        att = self._attachments.get(terminal_id)
        if att is not None:
            att.state = state
            att.last_activity_at = _iso(_now())

    def touch(self, terminal_id: str) -> None:
        att = self._attachments.get(terminal_id)
        if att is not None:
            att.last_activity_at = _iso(_now())

    def attach_session(self, terminal_id: str, session: TerminalSession) -> None:
        self._sessions[terminal_id] = session

    def record_exit(self, terminal_id: str, code: int, classification: str | None) -> None:
        att = self._attachments.get(terminal_id)
        if att is not None:
            att.exit = ExitInfo(code=code, classification=classification)

    async def mark_disconnected(self, terminal_id: str) -> None:
        """Reap the live session (leaving remote Zellij intact) and mark disconnected.

        Used on WS transport loss (FR-019/FR-020): the attachment record is
        kept (visible in ``GET /terminals``) but its process group is reaped.
        A reconnect creates a brand-new attachment to the same target.
        """
        session = self._sessions.pop(terminal_id, None)
        if session is not None:
            await session.close()
        att = self._attachments.get(terminal_id)
        if att is not None and att.state not in (TerminalState.CLOSED, TerminalState.ERROR):
            att.state = TerminalState.DISCONNECTED

    async def close(self, terminal_id: str) -> None:
        """Reap and remove a terminal (``DELETE /terminals/{id}``)."""
        session = self._sessions.pop(terminal_id, None)
        if session is not None:
            await session.close()
        await self._tokens.discard(terminal_id)
        att = self._attachments.pop(terminal_id, None)
        if att is not None:
            att.state = TerminalState.CLOSED

    async def close_all(self) -> None:
        """Reap every live session (graceful shutdown, NFR-007)."""
        for terminal_id in list(self._sessions):
            await self.close(terminal_id)
