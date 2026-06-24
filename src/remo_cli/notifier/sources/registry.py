"""In-memory, bounded, lock-guarded registry of sources (spec 009 R2/R7).

Mirrors ``PendingApprovals``'s concurrency discipline: an ``asyncio.Lock`` makes
the capacity gate and the duplicate-``source_id`` reconcile race-free. One
``asyncio.Task`` poll loop is supervised per source. Never persisted — starts
empty on restart; recovery is by source reconnection (FR-013).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from remo_cli.notifier.agentsh_client import AgentshClient
from remo_cli.notifier.logging_setup import get_logger
from remo_cli.notifier.models import Decision, SourceRegistration, SourceStatus
from remo_cli.notifier.sources.poller import SourcePoller
from remo_cli.notifier.sources.source import PollHealth, Source
from remo_cli.notifier.state import PendingApprovals

_log = get_logger("remo_notifier.registry")

# (source) -> SourcePoller. Injected so the server can close ``dispatch`` over the
# transport/grants without the registry knowing about delivery.
PollerFactory = Callable[[Source], SourcePoller]


class AtCapacity(Exception):
    """Registration refused: ``max_sources`` already reached (FR-004)."""

    def __init__(self, max_sources: int) -> None:
        super().__init__(f"max_sources={max_sources} reached")
        self.max_sources = max_sources


class SourceRegistry:
    def __init__(
        self,
        *,
        max_sources: int,
        pending: PendingApprovals,
        poller_factory: PollerFactory,
        client_factory: Callable[[str, str], AgentshClient] | None = None,
    ) -> None:
        self._max = max_sources
        self._pending = pending
        self._poller_factory = poller_factory
        self._client_factory = client_factory or (
            lambda api_url, api_key: AgentshClient(api_url=api_url, api_key=api_key)
        )
        self._sources: dict[str, Source] = {}
        self._epochs: dict[str, int] = {}
        self._lock = asyncio.Lock()

    # -- queries ------------------------------------------------------------
    def count(self) -> int:
        return len(self._sources)

    def get(self, source_id: str) -> Source | None:
        return self._sources.get(source_id)

    def snapshot(self) -> list[SourceStatus]:
        return [
            SourceStatus(
                source_id=s.source_id,
                labels=dict(s.labels),
                poll_state=s.health.poll_state,
                last_success_at=s.health.last_success_at,
                consecutive_failures=s.health.consecutive_failures,
                permanent=s.permanent,
            )
            for s in self._sources.values()
        ]

    def wake_all(self) -> None:
        """Nudge every source's poller to poll immediately (webhook trigger)."""
        for s in self._sources.values():
            s.wake.set()

    def any_polling(self) -> bool:
        """True if ≥1 source is currently polling successfully (health probe)."""
        return any(
            s.health.poll_state == "polling" and s.health.last_success_at is not None
            for s in self._sources.values()
        )

    # -- mutation -----------------------------------------------------------
    def _spawn(self, source: Source) -> None:
        poller = self._poller_factory(source)
        source.task = asyncio.create_task(poller.run())
        self._sources[source.source_id] = source

    async def register(self, reg: SourceRegistration) -> Source:
        """Register (or reconcile) a dynamic source; start its poll loop.

        Capacity is checked only for a genuinely new ``source_id``. A duplicate
        reconciles latest-connection-wins: the epoch is bumped, the prior source
        torn down (its in-flight approvals fail-secure drained), and a fresh
        source installed (FR-003).
        """
        async with self._lock:
            existing = self._sources.get(reg.source_id)
            if existing is None and len(self._sources) >= self._max:
                raise AtCapacity(self._max)
            epoch = self._epochs.get(reg.source_id, 0) + 1
            self._epochs[reg.source_id] = epoch
            client = self._client_factory(reg.api_url, reg.api_key)
            await client.start()
            source = Source(
                source_id=reg.source_id,
                api_url=reg.api_url,
                api_key=reg.api_key,
                epoch=epoch,
                client=client,
                labels=dict(reg.labels),
                permanent=False,
                health=PollHealth(),
            )
            if existing is not None:
                await self._teardown(existing, drain=True)
            self._spawn(source)
            _log.info("source_registered", source_id=source.source_id, epoch=epoch)
            return source

    async def add_seed(
        self, source_id: str, client: AgentshClient, *, labels: dict[str, str] | None = None
    ) -> Source:
        """Register the optional permanent ``[agentsh]`` seed source (epoch 0, R7).

        Counts toward ``max_sources`` but is never removed by connection-drop
        logic (it has no presence connection). Wraps an already-built client so
        tests can inject a fake and the seed reuses 008's secret-file wiring.
        """
        async with self._lock:
            await client.start()
            source = Source(
                source_id=source_id,
                api_url=getattr(client, "_base", ""),
                api_key="<seed>",
                epoch=0,
                client=client,
                labels=dict(labels or {}),
                permanent=True,
                health=PollHealth(),
            )
            self._spawn(source)
            _log.info("seed_source_registered", source_id=source_id)
            return source

    async def remove(self, source_id: str, epoch: int) -> bool:
        """Epoch-guarded removal triggered by a presence-connection drop (FR-007).

        No-op if the source is gone, if ``epoch`` is stale (a superseded
        connection's cleanup), or if the source is ``permanent``. Otherwise tears
        the source down and fail-secure drains its in-flight approvals.
        """
        async with self._lock:
            source = self._sources.get(source_id)
            if source is None or source.epoch != epoch or source.permanent:
                return False
            del self._sources[source_id]
            await self._teardown(source, drain=True)
            _log.info("source_removed", source_id=source_id, epoch=epoch)
            return True

    async def drain_all(self) -> None:
        """Tear down every source (shutdown). Fail-secure drains each."""
        async with self._lock:
            sources = list(self._sources.values())
            self._sources.clear()
            for source in sources:
                await self._teardown(source, drain=True)

    async def _teardown(self, source: Source, *, drain: bool) -> None:
        """Cancel the poll loop, fail-secure drain, best-effort wire deny, stop.

        Order matters: the local deny (``drain_source``) is the guaranteed
        fail-secure outcome; the best-effort agentsh deny is attempted while the
        client is still live and its failure is ignored (FR-009 / R9). The client
        is stopped last.
        """
        if source.task is not None:
            source.task.cancel()
        if drain:
            agentsh_ids = self._pending.drain_source(source.source_id)
            for aid in agentsh_ids:
                try:
                    await source.client.resolve(aid, Decision.deny, reason="source removed")
                except Exception:  # noqa: BLE001 - best-effort only (R9)
                    pass
        try:
            await source.client.stop()
        except Exception:  # noqa: BLE001 - stop is best-effort
            pass
