"""Tests for SourceRegistry: capacity, reconcile, epoch-guarded remove, seed,
snapshot, drain_all (spec 009 T010)."""

from __future__ import annotations

import asyncio

import pytest

from remo_cli.notifier.models import SourceRegistration
from remo_cli.notifier.sources.registry import AtCapacity, SourceRegistry
from remo_cli.notifier.state import PendingApprovals

from ..conftest import FakeAgentsh


class _IdlePoller:
    """A poller whose run() idles forever — isolates registry logic from polling."""

    def __init__(self, source) -> None:
        self.source = source

    async def run(self) -> None:
        await asyncio.Event().wait()


def _make_registry(max_sources: int = 2) -> tuple[SourceRegistry, PendingApprovals]:
    pending = PendingApprovals(max_pending=50)
    registry = SourceRegistry(
        max_sources=max_sources,
        pending=pending,
        poller_factory=_IdlePoller,
        client_factory=lambda api_url, api_key: FakeAgentsh([]),
    )
    return registry, pending


def _reg(source_id: str = "a", url: str = "http://a:8080") -> SourceRegistration:
    return SourceRegistration(source_id=source_id, api_url=url, api_key="k", labels={})


async def test_register_starts_one_task_and_counts() -> None:
    registry, _ = _make_registry()
    src = await registry.register(_reg("a"))
    assert registry.count() == 1
    assert src.epoch == 1
    assert src.task is not None and not src.task.done()
    await registry.drain_all()


async def test_capacity_raises_at_capacity() -> None:
    registry, _ = _make_registry(max_sources=1)
    await registry.register(_reg("a"))
    with pytest.raises(AtCapacity) as exc:
        await registry.register(_reg("b"))
    assert exc.value.max_sources == 1
    assert registry.count() == 1
    await registry.drain_all()


async def test_duplicate_source_id_reconciles_latest_wins() -> None:
    registry, _ = _make_registry()
    first = await registry.register(_reg("a", "http://old:8080"))
    old_task = first.task
    second = await registry.register(_reg("a", "http://new:8080"))
    # Single source, epoch bumped, old task cancelled (one loop per source_id).
    assert registry.count() == 1
    assert second.epoch == 2
    assert second.api_url == "http://new:8080"
    await asyncio.sleep(0.01)
    assert old_task is not None and (old_task.cancelled() or old_task.done())
    assert second.task is not None and not second.task.done()
    await registry.drain_all()


async def test_remove_is_epoch_guarded() -> None:
    registry, _ = _make_registry()
    src = await registry.register(_reg("a"))
    # A stale epoch never removes the current registration.
    assert await registry.remove("a", src.epoch - 1) is False
    assert registry.count() == 1
    # The current epoch removes it.
    assert await registry.remove("a", src.epoch) is True
    assert registry.count() == 0


async def test_permanent_seed_never_removed() -> None:
    registry, _ = _make_registry()
    seed = await registry.add_seed("seed", FakeAgentsh([]))
    assert seed.permanent is True
    assert seed.epoch == 0
    assert await registry.remove("seed", 0) is False
    assert registry.count() == 1
    await registry.drain_all()


async def test_seed_then_dynamic_reconcile_supersedes_seed() -> None:
    # R7: a dynamic POST carrying the seed's source_id reconciles (latest wins).
    registry, _ = _make_registry()
    await registry.add_seed("seed", FakeAgentsh([]))
    src = await registry.register(_reg("seed", "http://dyn:8080"))
    assert registry.count() == 1
    assert src.permanent is False
    assert src.epoch == 1  # bumped above the seed's epoch 0
    assert await registry.remove("seed", src.epoch) is True
    await registry.drain_all()


async def test_snapshot_excludes_secrets_and_lists_health() -> None:
    registry, _ = _make_registry()
    await registry.register(_reg("a", "http://a:8080"))
    rows = registry.snapshot()
    assert len(rows) == 1
    row = rows[0]
    dumped = row.model_dump()
    assert "api_key" not in dumped
    assert "api_url" not in dumped
    assert row.source_id == "a"
    assert row.poll_state in {"polling", "backing_off"}
    await registry.drain_all()


async def test_drain_all_clears_and_stops() -> None:
    registry, _ = _make_registry()
    s1 = await registry.register(_reg("a"))
    s2 = await registry.register(_reg("b"))
    await registry.drain_all()
    assert registry.count() == 0
    await asyncio.sleep(0.01)
    assert s1.task is not None and (s1.task.cancelled() or s1.task.done())
    assert s2.task is not None and (s2.task.cancelled() or s2.task.done())


async def test_remove_drains_in_flight_fail_secure() -> None:
    from remo_cli.notifier.models import Decision

    from ..conftest import make_request

    registry, pending = _make_registry()
    src = await registry.register(_reg("a"))
    # Simulate an in-flight delivery for this source.
    req = make_request()
    entry = await pending.reserve(
        "delivery-1", req, source_id="a", epoch=src.epoch, agentsh_approval_id=req.id
    )
    await registry.remove("a", src.epoch)
    # Local fail-secure deny, and the best-effort wire deny was attempted.
    assert entry.future.done()
    assert entry.future.result().decision is Decision.deny
    assert any(r[0] == req.id and r[1] is Decision.deny for r in src.client.resolved)


async def test_remove_ignores_unreachable_agentsh_on_drain() -> None:
    from remo_cli.notifier.agentsh_client import AgentshError
    from remo_cli.notifier.models import Decision

    from ..conftest import make_request

    registry, pending = _make_registry()
    src = await registry.register(_reg("a"))

    async def boom(approval_id, decision, *, reason=""):
        raise AgentshError("unreachable")

    src.client.resolve = boom  # best-effort wire deny will fail
    req = make_request()
    entry = await pending.reserve(
        "delivery-1", req, source_id="a", epoch=src.epoch, agentsh_approval_id=req.id
    )
    # Removal still succeeds and the local deny still holds (fail-secure).
    assert await registry.remove("a", src.epoch) is True
    assert entry.future.result().decision is Decision.deny
    assert registry.count() == 0
