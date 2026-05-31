"""Tests for the PendingApprovals registry (T010)."""

from __future__ import annotations

import asyncio

import pytest

from remo_cli.notifier.models import ApprovalDecision, Decision
from remo_cli.notifier.state import (
    PendingApprovals,
    RegisterError,
    RegistrationFailed,
)

from .conftest import make_request



def _allow() -> ApprovalDecision:
    return ApprovalDecision(decision=Decision.allow, responder="telegram:t")


async def test_register_and_resolve() -> None:
    reg = PendingApprovals(max_pending=10)
    await reg.reserve("a", make_request())
    assert reg.count() == 1

    async def resolver() -> None:
        await asyncio.sleep(0.01)
        assert reg.resolve("a", _allow()) is True

    asyncio.create_task(resolver())
    decision = await reg.wait("a", timeout=1.0)
    assert decision.decision is Decision.allow
    assert reg.count() == 0


async def test_timeout_raises() -> None:
    reg = PendingApprovals(max_pending=10)
    await reg.reserve("a", make_request())
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await reg.wait("a", timeout=0.05)


async def test_duplicate_rejected() -> None:
    reg = PendingApprovals(max_pending=10)
    await reg.reserve("a", make_request())
    with pytest.raises(RegistrationFailed) as exc:
        await reg.reserve("a", make_request())
    assert exc.value.reason is RegisterError.duplicate


async def test_capacity_rejected() -> None:
    reg = PendingApprovals(max_pending=1)
    await reg.reserve("a", make_request())
    with pytest.raises(RegistrationFailed) as exc:
        await reg.reserve("b", make_request())
    assert exc.value.reason is RegisterError.at_capacity


async def test_release_frees_slot() -> None:
    reg = PendingApprovals(max_pending=1)
    await reg.reserve("a", make_request())
    await reg.release("a")
    assert reg.count() == 0
    # capacity freed: a new reserve succeeds
    await reg.reserve("b", make_request())
    assert reg.count() == 1


async def test_resolve_unknown_is_noop() -> None:
    reg = PendingApprovals(max_pending=10)
    assert reg.resolve("missing", _allow()) is False


async def test_double_resolve_is_noop() -> None:
    reg = PendingApprovals(max_pending=10)
    await reg.reserve("a", make_request())
    assert reg.resolve("a", _allow()) is True
    assert reg.resolve("a", _allow()) is False


async def test_discard_removes_without_resolving() -> None:
    reg = PendingApprovals(max_pending=10)
    await reg.reserve("a", make_request())
    reg.discard("a")
    assert reg.count() == 0


async def test_drain_resolves_all() -> None:
    reg = PendingApprovals(max_pending=10)
    e_a = await reg.reserve("a", make_request())
    e_b = await reg.reserve("b", make_request())
    deny = ApprovalDecision(decision=Decision.deny, responder="system:shutdown", reason="shutdown")
    drained = reg.drain(deny)
    assert set(drained) == {"a", "b"}
    assert reg.count() == 0
    assert e_a.future.result().decision is Decision.deny
    assert e_b.future.result().decision is Decision.deny


async def test_concurrent_registration_respects_cap() -> None:
    reg = PendingApprovals(max_pending=5)

    async def reserve(i: int) -> bool:
        try:
            await reg.reserve(f"id-{i}", make_request())
            return True
        except RegistrationFailed:
            return False

    results = await asyncio.gather(*(reserve(i) for i in range(20)))
    assert sum(results) == 5
    assert reg.count() == 5
