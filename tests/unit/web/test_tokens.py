"""Unit tests for the single-use WS token store (T031, FR-049).

Covers: single-use consumption, configurable-TTL expiry (via an injected fake
clock — no real sleeps), replay rejection, and a source-level guard that the
raw token value is never interpolated into a logging statement.
"""

from __future__ import annotations

import ast
import inspect
import re

import pytest

from remo_cli.web import tokens
from remo_cli.web.tokens import TokenStore


class _FakeClock:
    """A manually-advanced monotonic clock for deterministic expiry tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_issue_returns_bound_opaque_token():
    store = TokenStore(ttl_s=30.0)
    token = await store.issue("term-1", "target-abc")

    assert token.terminal_id == "term-1"
    assert token.session_target_id == "target-abc"
    assert token.consumed is False
    # token_urlsafe(32) -> ~43 chars of URL-safe base64, >= 128 bits entropy.
    assert len(token.value) >= 32
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token.value)


@pytest.mark.asyncio
async def test_single_use_consumption():
    store = TokenStore(ttl_s=30.0)
    token = await store.issue("term-1", "target-abc")

    first = await store.consume(token.value)
    assert first is not None
    assert first.terminal_id == "term-1"
    assert first.consumed is True

    # Second consume of the same value must fail (single-use).
    assert await store.consume(token.value) is None


@pytest.mark.asyncio
async def test_replay_rejection_after_consume():
    store = TokenStore(ttl_s=30.0)
    token = await store.issue("term-1", "target-abc")

    assert await store.consume(token.value) is not None
    # Replays (any number) all rejected.
    assert await store.consume(token.value) is None
    assert await store.consume(token.value) is None


@pytest.mark.asyncio
async def test_unknown_token_rejected():
    store = TokenStore(ttl_s=30.0)
    assert await store.consume("never-issued") is None


@pytest.mark.asyncio
async def test_configurable_ttl_expiry():
    clock = _FakeClock()
    store = TokenStore(ttl_s=30.0, clock=clock)
    token = await store.issue("term-1", "target-abc")

    # Just before expiry: still valid.
    clock.advance(29.9)
    # Re-issue since the previous consume would remove it; test a fresh one at
    # the boundary instead.
    fresh = await store.issue("term-2", "target-abc")
    clock.advance(0.05)
    assert await store.consume(fresh.value) is not None

    # Past the 30s TTL: expired -> None.
    clock.advance(31.0)
    assert await store.consume(token.value) is None


@pytest.mark.asyncio
async def test_shorter_ttl_is_honored():
    clock = _FakeClock()
    store = TokenStore(ttl_s=5.0, clock=clock)
    token = await store.issue("term-1", "target-abc")

    clock.advance(5.5)
    assert await store.consume(token.value) is None


@pytest.mark.asyncio
async def test_discard_removes_unconsumed_tokens():
    store = TokenStore(ttl_s=30.0)
    token = await store.issue("term-1", "target-abc")
    await store.discard("term-1")
    assert await store.consume(token.value) is None


def test_source_never_logs_raw_token_value():
    """Regression guard: no logging/print call exists in tokens.py at all.

    The simplest way to guarantee "tokens never appear in logs" (FR-028/FR-049)
    is to not log anything in this module. This walks the AST (so docstrings /
    comments that merely *mention* logging don't count) and asserts there are
    no calls to `print`, `logging.*`, or any `logger`-like object — a future
    edit that adds such a call trips the test.
    """
    source = inspect.getsource(tokens)
    assert "import logging" not in source

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            assert func.id != "print", "tokens.py must not call print()"
        elif isinstance(func, ast.Attribute):
            root = func
            while isinstance(root, ast.Attribute):
                root = root.value  # type: ignore[assignment]
            root_name = getattr(root, "id", "")
            assert "log" not in root_name.lower(), (
                f"tokens.py must not log (found call on {root_name!r})"
            )
