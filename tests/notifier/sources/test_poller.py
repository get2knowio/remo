"""Tests for SourcePoller: backoff growth/cap/reset, fail-secure (no dispatch
while failing), per-source in-flight dedup (spec 009 T011)."""

from __future__ import annotations

import asyncio

from remo_cli.notifier.agentsh_client import AgentshError
from remo_cli.notifier.sources.poller import SourcePoller
from remo_cli.notifier.sources.source import PollHealth, Source

from ..conftest import FakeAgentsh, make_request


def _source(client) -> Source:
    return Source(
        source_id="a",
        api_url="http://a:8080",
        api_key="k",
        epoch=1,
        client=client,
        health=PollHealth(),
    )


def _poller(source, dispatch, *, base=5.0, factor=2.0, cap=300.0, jitter=0.0) -> SourcePoller:
    return SourcePoller(
        source,
        dispatch=dispatch,
        base_interval=base,
        backoff_factor=factor,
        backoff_cap=cap,
        backoff_jitter=jitter,
        uniform=lambda lo, hi: lo,  # deterministic
    )


async def _noop_dispatch(source, request) -> None:  # pragma: no cover - default stub
    return None


async def test_backoff_grows_then_caps() -> None:
    src = _source(FakeAgentsh([]))
    p = _poller(src, _noop_dispatch, base=5.0, factor=2.0, cap=40.0)
    delays = [p._on_failure() for _ in range(8)]
    # 5, 10, 20, 40, then capped at 40.
    assert delays[:4] == [5.0, 10.0, 20.0, 40.0]
    assert all(d == 40.0 for d in delays[3:])
    assert src.health.poll_state == "backing_off"
    assert src.health.consecutive_failures == 8


async def test_success_resets_backoff() -> None:
    src = _source(FakeAgentsh([]))
    p = _poller(src, _noop_dispatch, base=5.0)
    p._on_failure()
    p._on_failure()
    assert src.health.consecutive_failures == 2
    delay = p._on_success()
    assert delay == 5.0
    assert src.health.consecutive_failures == 0
    assert src.health.poll_state == "polling"
    assert src.health.last_success_at is not None


async def test_jitter_band_applied() -> None:
    src = _source(FakeAgentsh([]))
    # uniform returns the *upper* bound here, so first failure (raw=5) -> 5*1.2
    p = SourcePoller(
        src, dispatch=_noop_dispatch, base_interval=5.0, backoff_factor=2.0,
        backoff_cap=300.0, backoff_jitter=0.2, uniform=lambda lo, hi: hi,
    )
    delay = p._on_failure()
    assert delay == 6.0  # 5 * (1 + 0.2)


async def test_failing_poll_never_dispatches() -> None:
    client = FakeAgentsh([make_request()])
    client.fail_poll = True
    dispatched: list = []

    async def dispatch(source, request):
        dispatched.append(request.id)

    src = _source(client)
    p = _poller(src, dispatch, base=0.01)
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.05)
    task.cancel()
    assert dispatched == []  # fail-secure: nothing delivered while failing
    assert src.health.poll_state == "backing_off"


async def test_polls_and_dispatches_once_per_id() -> None:
    req = make_request()
    client = FakeAgentsh([req])  # same pending id returned every poll
    dispatched: list = []
    started = asyncio.Event()

    async def dispatch(source, request):
        dispatched.append(request.id)
        started.set()
        await asyncio.sleep(0.2)  # stays "in flight" across several polls

    src = _source(client)
    p = _poller(src, dispatch, base=0.01)
    task = asyncio.create_task(p.run())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await asyncio.sleep(0.05)  # several more poll cycles
    task.cancel()
    assert dispatched == [req.id]  # in-flight dedup: delivered exactly once
    assert src.health.poll_state == "polling"
