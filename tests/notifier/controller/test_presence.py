"""PresenceRegistrar tests (Approach B — issue #46): held connection per source."""

from __future__ import annotations

import asyncio

from remo_cli.notifier.controller.presence import PresenceRegistrar
from remo_cli.notifier.models import SourceRegistration


def _reg(source_id: str) -> SourceRegistration:
    return SourceRegistration(source_id=source_id, api_url=f"http://{source_id}:8080", api_key="k")


class BlockingHold:
    """Hold that signals it started, then blocks until cancelled."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.cancelled = 0

    async def __call__(self, reg: SourceRegistration) -> None:
        self.calls.append(reg.source_id)
        self.started.set()
        try:
            await asyncio.Event().wait()  # block forever
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


def _registrar(hold) -> PresenceRegistrar:
    return PresenceRegistrar(hold, backoff_base=0.0, backoff_cap=0.0)


async def test_register_holds_then_deregister_cancels() -> None:
    hold = BlockingHold()
    reg = _registrar(hold)
    assert await reg.register(_reg("proj-a")) is True
    await asyncio.wait_for(hold.started.wait(), 1.0)
    assert hold.calls == ["proj-a"]
    await reg.deregister("proj-a")
    assert hold.cancelled == 1
    assert reg._tasks == {}  # noqa: SLF001


async def test_register_is_idempotent() -> None:
    hold = BlockingHold()
    reg = _registrar(hold)
    await reg.register(_reg("proj-a"))
    await asyncio.wait_for(hold.started.wait(), 1.0)
    await reg.register(_reg("proj-a"))  # second call: no new connection
    await asyncio.sleep(0)
    assert hold.calls == ["proj-a"]
    await reg.aclose()


async def test_reconnects_until_held() -> None:
    class Reconnecting:
        def __init__(self) -> None:
            self.calls = 0
            self.held = asyncio.Event()

        async def __call__(self, reg: SourceRegistration) -> None:
            self.calls += 1
            if self.calls >= 3:
                self.held.set()
                await asyncio.Event().wait()
            # else: return immediately → simulates a dropped connection

    hold = Reconnecting()
    reg = _registrar(hold)
    await reg.register(_reg("proj-a"))
    await asyncio.wait_for(hold.held.wait(), 1.0)
    assert hold.calls == 3  # dropped twice, re-held on the third
    await reg.aclose()


async def test_aclose_cancels_all() -> None:
    hold = BlockingHold()
    reg = _registrar(hold)
    await reg.register(_reg("a"))
    await reg.register(_reg("b"))
    await asyncio.sleep(0)
    await reg.aclose()
    assert reg._tasks == {}  # noqa: SLF001
    assert hold.cancelled == 2


async def test_deregister_unknown_is_noop() -> None:
    reg = _registrar(BlockingHold())
    await reg.deregister("ghost")  # must not raise
