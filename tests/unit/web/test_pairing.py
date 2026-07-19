"""Pairing session manager unit tests (012-web-adopt-pairing, T008).

Exercises the in-memory `PairingSessionManager` with an injected fake monotonic
clock so every TTL branch is deterministic (no `sleep`): mint returns a code,
rotation invalidates the prior, authenticate touches the sliding window, idle
expiry drops the session, and end() is idempotent (Constitution II).
"""

from __future__ import annotations

from remo_cli.web.operator_auth import OperatorIdentity
from remo_cli.web.pairing import PairingSessionManager


class _Clock:
    """A controllable monotonic clock."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _manager(clock: _Clock, ttl_s: float = 900.0) -> PairingSessionManager:
    return PairingSessionManager(ttl_s=ttl_s, now=clock)


def test_mint_returns_code_and_ttl():
    clock = _Clock()
    mgr = _manager(clock)
    code, ttl = mgr.mint(None)
    assert code and isinstance(code, str)
    assert ttl == 900.0
    assert mgr.is_live()


def test_mint_records_identity():
    clock = _Clock()
    mgr = _manager(clock)
    identity = OperatorIdentity(subject="alice", provider="forward")
    code, _ = mgr.mint(identity, "resync")
    assert mgr.current_identity() == identity
    session = mgr.authenticate(code)
    assert session is not None
    assert session.identity == identity
    assert session.origin == "resync"


def test_authenticate_success_and_wrong_code():
    clock = _Clock()
    mgr = _manager(clock)
    code, _ = mgr.mint(None)
    assert mgr.authenticate(code) is not None
    assert mgr.authenticate("nope") is None
    assert mgr.authenticate("") is None


def test_authenticate_non_ascii_presented_is_a_clean_mismatch():
    # A raw Authorization header byte 0x80-0xFF decodes to a non-ASCII str;
    # comparing it must NOT raise (hmac.compare_digest rejects non-ASCII str) —
    # it must return None (the dormant 404), never crash the gate with a 500.
    clock = _Clock()
    mgr = _manager(clock)
    mgr.mint(None)
    assert mgr.authenticate("café☕éé") is None


def test_rotation_invalidates_prior_code():
    clock = _Clock()
    mgr = _manager(clock)
    first, _ = mgr.mint(None)
    second, _ = mgr.mint(None)
    assert first != second
    assert mgr.authenticate(first) is None
    assert mgr.authenticate(second) is not None


def test_sliding_ttl_touch_extends_window():
    clock = _Clock()
    mgr = _manager(clock, ttl_s=100.0)
    code, _ = mgr.mint(None)
    clock.advance(80)
    # A successful authenticate touches the session, resetting the idle window.
    assert mgr.authenticate(code) is not None
    clock.advance(80)  # 80s since the touch < 100s ttl
    assert mgr.authenticate(code) is not None


def test_idle_expiry_drops_session():
    clock = _Clock()
    mgr = _manager(clock, ttl_s=100.0)
    code, _ = mgr.mint(None)
    clock.advance(101)
    assert mgr.is_live() is False
    assert mgr.authenticate(code) is None


def test_end_is_idempotent():
    clock = _Clock()
    mgr = _manager(clock)
    code, _ = mgr.mint(None)
    mgr.end()
    assert mgr.is_live() is False
    mgr.end()  # no error on a second end
    assert mgr.authenticate(code) is None


def test_authenticate_when_dormant_returns_none():
    clock = _Clock()
    mgr = _manager(clock)
    assert mgr.is_live() is False
    assert mgr.authenticate("anything") is None
