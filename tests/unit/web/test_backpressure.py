"""Backpressure + clean process reap for TerminalSession (T032, FR-021/FR-019).

No SSH needed: a trivial ``cat``/``sleep`` child exercises the identical PTY,
byte-bounded-buffer, pause/resume, and process-group reap paths that the real
``ssh`` child would.
"""

from __future__ import annotations

import asyncio
import os
import signal

import pytest

from remo_cli.web.terminal import TerminalSession


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.asyncio
async def test_reader_pauses_and_bounds_memory_under_stall():
    """A never-draining consumer must NOT grow buffered output unboundedly.

    `yes` floods stdout continuously on its own, standing in for a remote
    process producing output faster than a stalled browser reads it. With
    `read_output()` never called, the byte-bounded buffer must pause the PTY
    reader instead of accumulating without limit.
    """
    high_water = 64 * 1024
    session = TerminalSession(
        ["yes"],
        cols=80,
        rows=24,
        output_high_water=high_water,
        output_low_water=high_water // 2,
    )
    await session.start()
    try:
        # Let `yes` flood and the reader trip the high-water pause; never read.
        await asyncio.sleep(0.5)

        assert session.is_paused, "PTY reader should pause once buffer is full"
        # Buffered bytes are bounded near the high-water mark (a single read
        # can overshoot by one chunk), never growing without bound.
        assert session.buffered_bytes <= high_water + 65536
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_paused_reader_resumes_after_drain():
    """Draining a paused terminal must resume the PTY reader (flow control).

    If the reader never resumed after pausing, we could only ever read up to
    ~high_water bytes total and then block forever. Successfully reading many
    multiples of high_water proves the reader resumes each time we drain below
    the low-water mark.
    """
    high_water = 64 * 1024
    session = TerminalSession(
        ["yes"],
        cols=80,
        rows=24,
        output_high_water=high_water,
        output_low_water=high_water // 2,
    )
    await session.start()
    try:
        await asyncio.sleep(0.5)
        assert session.is_paused

        drained = 0
        resumed_at_least_once = False
        target = high_water * 4
        while drained < target:
            chunk = await asyncio.wait_for(session.read_output(), timeout=2.0)
            drained += len(chunk)
            if not session.is_paused:
                resumed_at_least_once = True
        assert drained >= target
        assert resumed_at_least_once, "reader never resumed after draining"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_close_reaps_process_group():
    """`close()` must SIGTERM the child's whole process group and reap it."""
    session = TerminalSession(["sleep", "50"], cols=80, rows=24)
    await session.start()
    pid = session._proc.pid  # noqa: SLF001
    assert _pid_alive(pid)

    await session.close()

    # The child is gone (reaped, not a zombie) shortly after close returns.
    for _ in range(50):
        if not _pid_alive(pid):
            break
        await asyncio.sleep(0.05)
    assert not _pid_alive(pid)


@pytest.mark.asyncio
async def test_close_is_idempotent_and_partial_safe():
    session = TerminalSession(["sleep", "50"], cols=80, rows=24)
    await session.start()
    await session.close()
    # Second close must be a no-op, not raise.
    await session.close()

    # close() on a never-started session must also be safe.
    never_started = TerminalSession(["sleep", "50"], cols=80, rows=24)
    await never_started.close()


@pytest.mark.asyncio
async def test_read_output_returns_eof_after_child_exit():
    # `true` exits immediately with code 0; read_output should surface EOF and
    # wait() should report the exit code.
    session = TerminalSession(["true"], cols=80, rows=24)
    await session.start()
    try:
        # Drain until EOF (b"").
        for _ in range(100):
            chunk = await asyncio.wait_for(session.read_output(), timeout=2.0)
            if chunk == b"":
                break
        else:
            pytest.fail("never reached EOF")
        rc = await asyncio.wait_for(session.wait(), timeout=2.0)
        assert rc == 0
        assert session.error_class is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_nonzero_exit_classified():
    # `false` exits 1 -> a non-ssh, non-remo-host code -> best-effort
    # remote_launch classification (documented limitation).
    session = TerminalSession(["false"], cols=80, rows=24)
    await session.start()
    try:
        rc = await asyncio.wait_for(session.wait(), timeout=2.0)
        assert rc == 1
        assert session.error_class is not None
        assert session.error_class.value == "remote_launch"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_signal_module_reachable():
    # Sanity: the SIGTERM path constant exists (guards against a typo import).
    assert signal.SIGTERM
