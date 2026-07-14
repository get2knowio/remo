"""Resize clamping + TIOCSWINSZ correctness against a real PTY (T032, FR-060).

No SSH needed: these exercise the resize path against a real ``pty.openpty()``
pair and read the window size back with ``TIOCGWINSZ`` to prove the ioctl
actually took effect.
"""

from __future__ import annotations

import fcntl
import os
import struct
import termios

import pytest

from remo_cli.web.terminal import (
    MAX_DIMENSION,
    MIN_DIMENSION,
    TerminalSession,
    apply_winsize,
    clamp_dimension,
)


def _read_winsize(fd: int) -> tuple[int, int]:
    """Return ``(cols, rows)`` currently set on *fd*."""
    packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
    rows, cols, _xp, _yp = struct.unpack("HHHH", packed)
    return cols, rows


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, MIN_DIMENSION),
        (-5, MIN_DIMENSION),
        (1, 1),
        (80, 80),
        (1000, 1000),
        (1001, MAX_DIMENSION),
        (999999, MAX_DIMENSION),
        ("not-an-int", MIN_DIMENSION),
        (None, MIN_DIMENSION),
    ],
)
def test_clamp_dimension(value, expected):
    assert clamp_dimension(value) == expected


def test_apply_winsize_sets_real_pty_window():
    master_fd, slave_fd = os.openpty() if hasattr(os, "openpty") else (None, None)
    try:
        applied_cols, applied_rows = apply_winsize(master_fd, 120, 40)
        assert (applied_cols, applied_rows) == (120, 40)
        assert _read_winsize(master_fd) == (120, 40)
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def test_apply_winsize_clamps_out_of_bounds_on_real_pty():
    master_fd, slave_fd = os.openpty()
    try:
        # Zero rows and an extreme col count both clamp before hitting the ioctl.
        applied_cols, applied_rows = apply_winsize(master_fd, 999999, 0)
        assert applied_cols == MAX_DIMENSION
        assert applied_rows == MIN_DIMENSION
        assert _read_winsize(master_fd) == (MAX_DIMENSION, MIN_DIMENSION)
    finally:
        os.close(master_fd)
        os.close(slave_fd)


@pytest.mark.asyncio
async def test_session_resize_applies_to_live_pty():
    # `cat` is a trivial stand-in for the ssh child: same PTY plumbing, no SSH.
    session = TerminalSession(["cat"], cols=80, rows=24)
    await session.start()
    try:
        session.resize(133, 55)
        # The session's master fd should now report the resized window.
        assert _read_winsize(session._master_fd) == (133, 55)  # noqa: SLF001

        # Out-of-bounds resize is clamped, never propagated raw.
        session.resize(0, 100000)
        assert _read_winsize(session._master_fd) == (MIN_DIMENSION, MAX_DIMENSION)  # noqa: SLF001
    finally:
        await session.close()
