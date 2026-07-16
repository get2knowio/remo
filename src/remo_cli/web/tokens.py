"""Short-lived single-use WebSocket authorization tokens (T037).

A :class:`TokenStore` mints an opaque, >=128-bit token bound to a
``(terminal_id, session_target_id)`` pair on ``POST /terminals`` and lets the
WebSocket upgrade handler atomically consume it exactly once (R9/FR-049).

Security notes:

* The raw token value is a secret. This module NEVER logs it — there are no
  logging statements here at all, which makes "token never appears in logs"
  trivially and verifiably true (see ``tests/unit/web/test_tokens.py``).
* Consumption is single-use and atomic: :meth:`TokenStore.consume` removes the
  token from the store before returning it, so a replayed value finds nothing
  and yields ``None``. The caller cannot tell *why* a consume failed
  (unknown vs expired vs already-used) — that ambiguity is intentional and is
  not leaked (FR-028).
* Expiry uses an injectable monotonic ``clock`` (default ``time.monotonic``)
  so tests can advance time deterministically without real sleeps.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Callable

from remo_cli.web.models import WsToken

__all__ = ["TokenStore"]

#: token_urlsafe(32) -> 32 random bytes -> 256 bits of entropy (>= the 128-bit
#: floor required by FR-049/data-model.md), URL-safe base64 (~43 chars).
_TOKEN_NBYTES = 32


class TokenStore:
    """In-memory registry of single-use WS tokens with monotonic expiry."""

    def __init__(
        self,
        ttl_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = float(ttl_s)
        self._clock = clock
        self._tokens: dict[str, WsToken] = {}
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _get_lock(self) -> asyncio.Lock:
        # Bind the lock lazily to the *currently running* loop, recreating it if
        # the loop changed. An asyncio.Lock is loop-bound, and a single store
        # may legitimately be exercised from more than one loop across its life
        # (e.g. one loop per request under some test harnesses). Access is still
        # sequential — this only avoids a "Future attached to a different loop"
        # error, not real cross-loop concurrency.
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    @property
    def ttl_s(self) -> float:
        return self._ttl_s

    async def issue(self, terminal_id: str, session_target_id: str) -> WsToken:
        """Mint a fresh single-use token bound to *terminal_id*/*session_target_id*."""
        token = WsToken(
            value=secrets.token_urlsafe(_TOKEN_NBYTES),
            terminal_id=terminal_id,
            session_target_id=session_target_id,
            expires_at=self._clock() + self._ttl_s,
        )
        async with self._get_lock():
            self._tokens[token.value] = token
        return token

    async def consume(self, value: str) -> WsToken | None:
        """Atomically consume *value*, returning the token or ``None``.

        Returns ``None`` for an unknown, expired, or already-consumed value —
        the three cases are deliberately indistinguishable to the caller.
        The token is removed from the store on the first successful consume,
        so any subsequent (replay) attempt returns ``None``.
        """
        async with self._get_lock():
            token = self._tokens.pop(value, None)
            if token is None:
                return None
            if token.consumed:
                return None
            if self._clock() > token.expires_at:
                return None
            token.consumed = True
            return token

    async def discard(self, terminal_id: str) -> None:
        """Drop any unconsumed token(s) bound to *terminal_id* (cleanup)."""
        async with self._get_lock():
            for value in [v for v, t in self._tokens.items() if t.terminal_id == terminal_id]:
                del self._tokens[value]
