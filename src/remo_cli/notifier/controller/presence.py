"""Presence-connection Registrar for the controller (Approach B — issue #46).

Registrar wire semantics (decided): the controller holds one **presence
connection** (`POST /v1/sources`, held open) per discovered source — exactly the
mechanism the in-container connector used in Approach A, now centralized on the
host. This reuses spec-009's drop-detection + fail-secure drain with **no server
change**: registering = open & hold; deregistering = cancel the hold (the server
sees the drop and removes + drains the source).

A held connection that ends on its own (notifier restart, blip) is re-opened with
full-jitter-free exponential backoff until the source is explicitly deregistered.
``hold`` is injected so the supervisor is unit-testable without a real notifier.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import httpx

from remo_cli.notifier.logging_setup import get_logger
from remo_cli.notifier.models import SourceRegistration

_log = get_logger("remo_notifier.controller.presence")

# Holds a presence connection open until it ends (server close) or is cancelled.
HoldConnection = Callable[[SourceRegistration], Awaitable[None]]


def make_http_hold(client: httpx.AsyncClient) -> HoldConnection:
    """Real hold: stream `POST /v1/sources` and consume keepalives until closed."""

    async def hold(reg: SourceRegistration) -> None:
        async with client.stream("POST", "/v1/sources", json=reg.model_dump()) as resp:
            resp.raise_for_status()
            async for _line in resp.aiter_lines():
                pass  # drain keepalive ticks; returns when the server closes the stream

    return hold


class PresenceRegistrar:
    def __init__(
        self,
        hold: HoldConnection,
        *,
        backoff_base: float = 1.0,
        backoff_cap: float = 30.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._hold = hold
        self._base = float(backoff_base)
        self._cap = float(backoff_cap)
        self._sleep = sleep
        self._tasks: dict[str, asyncio.Task] = {}

    async def register(self, reg: SourceRegistration) -> bool:
        if reg.source_id in self._tasks:
            return True  # idempotent: already holding a connection for this source
        self._tasks[reg.source_id] = asyncio.create_task(self._supervise(reg))
        return True

    async def deregister(self, source_id: str) -> None:
        task = self._tasks.pop(source_id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def aclose(self) -> None:
        for source_id in list(self._tasks):
            await self.deregister(source_id)

    async def _supervise(self, reg: SourceRegistration) -> None:
        """Hold the connection; re-open with backoff until deregistered."""
        delay = self._base
        while True:
            try:
                await self._hold(reg)
                delay = self._base  # clean close → reset backoff before reconnect
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure → back off and retry
                _log.warning("presence_hold_error", source_id=reg.source_id, error=str(exc))
            await self._sleep(delay)
            delay = min(self._cap, delay * 2)
