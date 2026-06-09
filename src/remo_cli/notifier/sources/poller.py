"""Per-source poll/resolve loop with exponential backoff (spec 009 R2/R4).

Each ``Source`` owns one ``SourcePoller`` running as its own ``asyncio.Task`` so a
wedged agentsh endpoint never stalls another source. The loop polls the source's
``AgentshClient``, dedups in-flight approvals per source, and hands each *new*
approval to an injected ``dispatch(source, request)`` callback (the source-scoped
deliver/resolve flow lives in the server). A poll failure only **throttles** the
loop with backoff — it never de-registers the source (FR-014); de-registration is
exclusively the presence connection's job.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from remo_cli.notifier.agentsh_client import AgentshError
from remo_cli.notifier.logging_setup import get_logger
from remo_cli.notifier.models import AgentshRequest
from remo_cli.notifier.sources.source import Source

_log = get_logger("remo_notifier.poller")

DispatchFn = Callable[[Source, AgentshRequest], Awaitable[None]]


class SourcePoller:
    def __init__(
        self,
        source: Source,
        *,
        dispatch: DispatchFn,
        base_interval: float,
        backoff_factor: float,
        backoff_cap: float,
        backoff_jitter: float,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._source = source
        self._dispatch = dispatch
        self._base = float(base_interval)
        self._factor = float(backoff_factor)
        self._cap = float(backoff_cap)
        self._jitter = float(backoff_jitter)
        self._uniform = uniform
        self._inflight: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._source.health.current_backoff_seconds = self._base

    def _on_success(self) -> float:
        h = self._source.health
        h.consecutive_failures = 0
        h.poll_state = "polling"
        h.current_backoff_seconds = self._base
        h.last_success_at = datetime.now(timezone.utc)
        return self._base

    def _on_failure(self) -> float:
        """Advance backoff and return the jittered sleep for the next poll."""
        h = self._source.health
        h.consecutive_failures += 1
        h.poll_state = "backing_off"
        raw = min(self._cap, self._base * self._factor ** (h.consecutive_failures - 1))
        h.current_backoff_seconds = raw
        lo = raw * (1.0 - self._jitter)
        hi = raw * (1.0 + self._jitter)
        return self._uniform(lo, hi) if hi > lo else raw

    def _spawn(self, request: AgentshRequest) -> None:
        async def _run() -> None:
            try:
                await self._dispatch(self._source, request)
            finally:
                self._inflight.discard(request.id)

        task = asyncio.create_task(_run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def run(self) -> None:
        while True:
            try:
                requests = await self._source.client.poll()
                delay = self._on_success()
                for request in requests:
                    if request.id in self._inflight:
                        continue  # per-source in-flight dedup
                    self._inflight.add(request.id)
                    self._spawn(request)
            except AgentshError as exc:
                delay = self._on_failure()
                _log.warning(
                    "source_poll_failed",
                    source_id=self._source.source_id,
                    consecutive_failures=self._source.health.consecutive_failures,
                    backoff_seconds=round(self._source.health.current_backoff_seconds, 2),
                    error=str(exc),
                )
            try:
                await asyncio.wait_for(self._source.wake.wait(), timeout=delay)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            self._source.wake.clear()
