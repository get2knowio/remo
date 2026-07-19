"""In-memory ephemeral pairing sessions (012-web-adopt-pairing).

Replaces 011's static `REMO_WEB_API_TOKEN` gate on `/api/v1/setup/*` with a
short-lived, page-minted **pairing code**. At most one pairing session is live
at a time (most-recent-wins rotation, FR-003); a session exists only in process
memory and is dropped on restart (FR-008).

Lifecycle (data-model.md):

    (none) --mint()--> LIVE
    LIVE   --authenticate() success (touch)--> LIVE   (sliding idle TTL reset)
    LIVE   --mint() again--> LIVE   (fresh code; prior code invalid)   [FR-003]
    LIVE   --idle > ttl_s--> (none) (lazy expiry)                      [FR-002]
    LIVE   --end()--> (none)  (adoption apply / page-hide beacon)      [FR-004/7]

Time is measured against a monotonic clock so a wall-clock change can neither
extend nor prematurely expire a session (spec Edge Cases). The clock is
injectable (`now=`) so TTL branches are deterministic in tests — no `sleep`.

Thread safety: FastAPI runs the sync setup/mint routes in a threadpool, so
every mutation is guarded by a `threading.Lock`.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from remo_cli.web.operator_auth import OperatorIdentity

#: Bytes of entropy for a pairing code (research R6). `token_urlsafe(24)` yields
#: ~192 bits, ~32 url-safe chars — clipboard-delivered, never hand-typed, so we
#: favor generous entropy over brevity.
_CODE_ENTROPY_BYTES = 24

PairingOrigin = Literal["adopt", "resync"]


@dataclass
class PairingSession:
    """One live adoption/push handoff. In-memory only (never persisted)."""

    code: str
    identity: OperatorIdentity | None
    origin: PairingOrigin
    last_activity: float
    ttl_s: float

    def is_expired(self, now: float) -> bool:
        return (now - self.last_activity) > self.ttl_s


class PairingSessionManager:
    """Holds at most one live pairing session (most-recent-wins).

    All methods are lock-guarded and use the injected monotonic clock.
    """

    def __init__(
        self,
        *,
        ttl_s: float = 900.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._now = now
        self._lock = threading.Lock()
        self._session: PairingSession | None = None

    def mint(
        self, identity: OperatorIdentity | None, origin: PairingOrigin = "adopt"
    ) -> tuple[str, float]:
        """Create a fresh code, evicting any prior session (rotation, FR-003).

        Returns ``(code, ttl_s)``. The raw code is returned exactly once; it is
        never stored anywhere but in the live session.
        """
        code = secrets.token_urlsafe(_CODE_ENTROPY_BYTES)
        with self._lock:
            self._session = PairingSession(
                code=code,
                identity=identity,
                origin=origin,
                last_activity=self._now(),
                ttl_s=self._ttl_s,
            )
        return code, self._ttl_s

    def authenticate(self, presented: str) -> PairingSession | None:
        """Constant-time match against the live, non-expired session.

        On success the session is touched (sliding TTL reset) and returned; an
        absent/expired/wrong code yields ``None`` — indistinguishable to the
        caller, which turns all of them into the dormant 404 (FR-005/FR-006).
        """
        with self._lock:
            session = self._live_locked()
            if session is None or not presented:
                return None
            # Compare as bytes, never as str: hmac.compare_digest raises
            # TypeError on a non-ASCII str, and `presented` is attacker-
            # controlled (a raw Authorization header byte 0x80-0xFF decodes to a
            # non-ASCII str). Encoding to UTF-8 keeps the comparison constant-
            # time and total, so a crafted bearer yields a clean mismatch (the
            # dormant 404) instead of a 500 that would reveal a live session.
            if not hmac.compare_digest(presented.encode("utf-8"), session.code.encode("utf-8")):
                return None
            session.last_activity = self._now()
            return session

    def current_identity(self) -> OperatorIdentity | None:
        """Identity of the live session (for audit logging), or None."""
        with self._lock:
            session = self._live_locked()
            return session.identity if session else None

    def is_live(self) -> bool:
        """True when a non-expired session exists (drives dormancy)."""
        with self._lock:
            return self._live_locked() is not None

    def end(self) -> None:
        """Drop the live session. Idempotent (completion / beacon / rotation)."""
        with self._lock:
            self._session = None

    # -- internals ---------------------------------------------------------

    def _live_locked(self) -> PairingSession | None:
        """Return the live session, lazily dropping it if expired. Caller holds the lock."""
        session = self._session
        if session is None:
            return None
        if session.is_expired(self._now()):
            self._session = None
            return None
        return session
