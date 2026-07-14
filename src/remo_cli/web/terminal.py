"""PTY + SSH attachment lifecycle for a single browser terminal (T036).

Each browser terminal is brokered by one :class:`TerminalSession`:

    browser WS  <->  server PTY (pty.openpty)  <->  ssh -tt <target>
                                                      "remo-host sessions attach --project X"

Mechanics (research.md R4):

* ``pty.openpty()`` gives a master/slave fd pair. The child ``ssh`` process
  gets the slave fd as its stdin/stdout/stderr; the server reads/writes the
  master fd. ``-tt`` (added by :func:`build_ssh_base_cmd`) forces a remote TTY
  so the Zellij/devcontainer flow behaves exactly like ``remo shell -p``.
* ``start_new_session=True`` puts the child in its own process group, so
  teardown can ``killpg`` the whole group (SIGTERM -> SIGKILL) — killing the
  local ssh only *detaches*; the remote Zellij session keeps running (FR-019).
* The master fd is read non-blocking via ``loop.add_reader`` (idiomatic for a
  raw fd). Output flows through a byte-bounded buffer: when the browser stalls
  and the buffer crosses the high-water mark, the reader is *paused*
  (``remove_reader``) rather than buffering unboundedly (FR-021); it resumes
  once the consumer drains below the low-water mark.
* Resize clamps to documented bounds (FR-060) and issues ``TIOCSWINSZ``.

Environment: the child inherits ``os.environ`` with ``TERM`` overridden to
``xterm-256color``. Passing the parent env is acceptable here because the
child is ``ssh`` itself (not a shell that would evaluate env into commands),
this is a trusted-LAN home-lab tool, and ``ssh`` only forwards env it is
explicitly told to via ``SendEnv`` — so no web-service secret leaks to the
remote command's environment.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios
from enum import Enum

from remo_cli.core.remo_host_client import build_remo_host_shell_cmd
from remo_cli.core.ssh import build_ssh_base_cmd
from remo_cli.core.validation import validate_project_name
from remo_cli.models.host import KnownHost

__all__ = [
    "MAX_DIMENSION",
    "MIN_DIMENSION",
    "ErrorClass",
    "TerminalSession",
    "apply_winsize",
    "build_attach_argv",
    "clamp_dimension",
    "classify_exit",
]

# ---------------------------------------------------------------------------
# Dimension clamping (FR-060)
# ---------------------------------------------------------------------------

#: Documented safe resize bounds (research.md R4: 1-1000 cols/rows).
MIN_DIMENSION = 1
MAX_DIMENSION = 1000

# Buffering / backpressure (FR-021).
_READ_CHUNK = 65536
_DEFAULT_HIGH_WATER = 1 << 20  # 1 MiB buffered -> pause the PTY reader.
_DEFAULT_LOW_WATER = 1 << 18  # 256 KiB -> resume once drained below this.
_DEFAULT_STALL_TIMEOUT_S = 30.0
_DEFAULT_TERM_GRACE_S = 3.0
_RECENT_OUTPUT_CAP = 4096  # bytes kept for exit classification.

# EOF sentinel pushed onto the output queue when the PTY closes.
_EOF = object()


class ErrorClass(str, Enum):
    """Classified terminal exit/setup errors surfaced to *this* terminal only.

    Matches the WS control-frame ``error.class`` enum (FR-023):
    ``auth|network|remote_capability|missing_project|remote_launch``.
    """

    AUTH = "auth"
    NETWORK = "network"
    REMOTE_CAPABILITY = "remote_capability"
    MISSING_PROJECT = "missing_project"
    REMOTE_LAUNCH = "remote_launch"


_AUTH_MARKERS: tuple[bytes, ...] = (
    b"permission denied",
    b"authentication failed",
    b"publickey",
    b"host key verification failed",
    b"too many authentication failures",
)


def clamp_dimension(value: object) -> int:
    """Clamp *value* into ``[MIN_DIMENSION, MAX_DIMENSION]`` (FR-060).

    Non-integer / unparseable input clamps to :data:`MIN_DIMENSION` rather
    than raising, so a malformed resize frame can never crash the pump.
    """
    try:
        ivalue = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return MIN_DIMENSION
    return max(MIN_DIMENSION, min(MAX_DIMENSION, ivalue))


def apply_winsize(master_fd: int, cols: object, rows: object) -> tuple[int, int]:
    """Clamp dims and set the PTY window size via ``TIOCSWINSZ``.

    Returns the ``(cols, rows)`` actually applied. ``struct.pack("HHHH", ...)``
    matches ``struct winsize {ws_row, ws_col, ws_xpixel, ws_ypixel}`` — rows
    first, then cols.
    """
    c = clamp_dimension(cols)
    r = clamp_dimension(rows)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", r, c, 0, 0))
    return c, r


def classify_exit(returncode: int | None, recent_output: bytes) -> ErrorClass | None:
    """Best-effort classification of a nonzero exit (FR-023).

    ``returncode``/``recent_output`` are the ssh exit status and a tail of the
    merged PTY output (stderr is on the same TTY, so it is not separable —
    classification of ``auth`` vs ``network`` is therefore heuristic and
    documented as best-effort). Returns ``None`` for a clean (0) exit.
    """
    if not returncode:  # None or 0
        return None
    low = recent_output.lower()
    if returncode == 255:  # ssh's own transport-failure code
        if any(marker in low for marker in _AUTH_MARKERS):
            return ErrorClass.AUTH
        return ErrorClass.NETWORK
    if returncode == 127:  # remote shell: remo-host not found
        return ErrorClass.REMOTE_CAPABILITY
    if returncode == 3:  # remo-host: invalid/missing project
        return ErrorClass.MISSING_PROJECT
    if returncode == 4:  # remo-host: unsupported subcommand (capability drift)
        return ErrorClass.REMOTE_CAPABILITY
    # Any other nonzero exit surfaced after the interactive stream began: the
    # project-launch itself most likely failed. We cannot pin it down further
    # (no separable stderr), so this is the documented best-effort fallback.
    return ErrorClass.REMOTE_LAUNCH


def build_attach_argv(host: KnownHost, project: str, *, control_dir: str | None = None) -> list[str]:
    """Build the ``ssh -tt ... "remo-host sessions attach --project X"`` argv.

    Reuses the shared :func:`build_ssh_base_cmd` (so SSM ProxyCommand, direct
    targeting, timezone SendEnv and ControlMaster multiplexing all match the
    CLI), inserts ``-o BatchMode=yes`` for non-interactive local auth (FR-025),
    and appends the shlex-quoted remote command as a single argv element.

    *project* is checked against the same :func:`~remo_cli.core.validation.
    validate_project_name` the CLI's :func:`~remo_cli.core.ssh.
    build_project_launch_remote_cmd` runs (T059) — defense-in-depth here,
    since a *project* reaching this function is normally already
    cache-trusted (it comes from a live ``SessionTarget`` populated by real
    ``sessions list`` results), but running the identical validator on both
    surfaces is what proves the CLI and web paths share validation/quoting
    (US5 scenario 3).

    Raises
    ------
    ValueError
        If *project* fails validation (propagated from
        :func:`validate_project_name`).
    """
    validate_project_name(project)
    base = build_ssh_base_cmd(host, tty=True, multiplex=True, control_dir=control_dir)
    remote_cmd = build_remo_host_shell_cmd("sessions attach", project=project)
    # base == ["ssh", *opts, "-tt", target]; keep BatchMode right after "ssh".
    return [base[0], "-o", "BatchMode=yes", *base[1:], remote_cmd]


class TerminalSession:
    """One PTY + ssh subprocess brokered for a single browser terminal.

    Construction takes an explicit *argv* (rather than a ``KnownHost``) so the
    same class is reusable and unit-testable with a trivial stand-in command
    (e.g. ``["cat"]``) that exercises the identical PTY/backpressure/reap paths
    without needing real ssh (see ``tests/unit/web/test_*``). Production call
    sites build *argv* via :func:`build_attach_argv`.
    """

    def __init__(
        self,
        argv: list[str],
        *,
        cols: int,
        rows: int,
        env: dict[str, str] | None = None,
        output_high_water: int = _DEFAULT_HIGH_WATER,
        output_low_water: int = _DEFAULT_LOW_WATER,
        stall_timeout_s: float = _DEFAULT_STALL_TIMEOUT_S,
        term_grace_s: float = _DEFAULT_TERM_GRACE_S,
    ) -> None:
        self._argv = list(argv)
        self._cols = clamp_dimension(cols)
        self._rows = clamp_dimension(rows)
        self._env = env
        self._high_water = output_high_water
        self._low_water = output_low_water
        self._stall_timeout_s = stall_timeout_s
        self._term_grace_s = term_grace_s

        self._loop: asyncio.AbstractEventLoop | None = None
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._returncode: int | None = None

        self._queue: asyncio.Queue[object] = asyncio.Queue()
        self._buffered = 0
        self._paused = False
        self._paused_since: float | None = None
        self._reader_installed = False
        self._recent = bytearray()
        self._closed = False
        self._eof_sent = False

        # Input write buffer. The master fd is non-blocking, so os.write() can
        # do a short write or raise BlockingIOError when the PTY is full (a
        # slow/not-reading remote under a large bracketed paste). We keep the
        # unwritten tail here and drain it via a loop.add_writer callback,
        # rather than dropping it (FR: browser input / bracketed paste).
        self._write_buf = bytearray()
        self._writer_installed = False

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Open the PTY and spawn the child. Reaps partial state on failure."""
        self._loop = asyncio.get_running_loop()
        try:
            self._master_fd, self._slave_fd = pty.openpty()
            apply_winsize(self._master_fd, self._cols, self._rows)

            child_env = dict(os.environ if self._env is None else self._env)
            child_env["TERM"] = "xterm-256color"

            self._proc = await asyncio.create_subprocess_exec(
                *self._argv,
                stdin=self._slave_fd,
                stdout=self._slave_fd,
                stderr=self._slave_fd,
                start_new_session=True,
                env=child_env,
                close_fds=True,
            )
            # The child now owns its own dup of the slave; the parent must
            # close its copy so EOF propagates when the child exits.
            os.close(self._slave_fd)
            self._slave_fd = None

            os.set_blocking(self._master_fd, False)
            self._install_reader()
        except Exception:
            # Partial-start cleanup: never leak a PTY fd or a half-spawned child.
            await self.close()
            raise

    def _install_reader(self) -> None:
        if (
            self._loop is not None
            and self._master_fd is not None
            and not self._reader_installed
            and not self._closed
        ):
            self._loop.add_reader(self._master_fd, self._on_readable)
            self._reader_installed = True

    def _remove_reader(self) -> None:
        if self._reader_installed and self._loop is not None and self._master_fd is not None:
            self._loop.remove_reader(self._master_fd)
        self._reader_installed = False

    def _on_readable(self) -> None:
        assert self._master_fd is not None
        try:
            data = os.read(self._master_fd, _READ_CHUNK)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            # EIO on the master fd means the child closed the slave (exit).
            data = b""

        if not data:
            self._remove_reader()
            self._push_eof()
            return

        self._recent.extend(data)
        if len(self._recent) > _RECENT_OUTPUT_CAP:
            del self._recent[: len(self._recent) - _RECENT_OUTPUT_CAP]

        self._queue.put_nowait(data)
        self._buffered += len(data)

        # Backpressure: pause reading once we've buffered too much unsent data.
        if self._buffered >= self._high_water and not self._paused:
            self._paused = True
            self._paused_since = self._loop.time() if self._loop else None
            self._remove_reader()

    def _push_eof(self) -> None:
        if not self._eof_sent:
            self._eof_sent = True
            self._queue.put_nowait(_EOF)

    # -- I/O --------------------------------------------------------------

    async def read_output(self) -> bytes:
        """Return the next chunk of PTY output, or ``b""`` at EOF.

        Draining below the low-water mark resumes a paused PTY reader.
        """
        item = await self._queue.get()
        if item is _EOF:
            return b""
        assert isinstance(item, bytes)
        self._buffered -= len(item)
        if self._paused and self._buffered <= self._low_water:
            self._paused = False
            self._paused_since = None
            self._install_reader()
        return item

    async def write_input(self, data: bytes) -> None:
        """Queue browser input bytes and flush as much as the PTY will accept.

        The master fd is non-blocking, so a single ``os.write`` may accept only
        part of *data* (short write) or none of it (``BlockingIOError``) when
        the remote isn't draining its stdin. The unwritten tail is retained and
        drained by :meth:`_on_writable` when the fd becomes writable again, so
        input (e.g. a large bracketed paste) is never silently truncated.
        """
        if self._master_fd is None or self._closed:
            return
        self._write_buf.extend(data)
        self._flush_input()

    def _flush_input(self) -> None:
        if self._master_fd is None:
            return
        while self._write_buf:
            try:
                written = os.write(self._master_fd, self._write_buf)
            except (BlockingIOError, InterruptedError):
                break  # PTY full / interrupted -> wait for writability.
            except OSError:
                # fd went away (child exited): input can't be delivered.
                self._write_buf.clear()
                self._remove_writer()
                return
            if written <= 0:
                break
            del self._write_buf[:written]

        if self._write_buf:
            self._install_writer()
        else:
            self._remove_writer()

    def _on_writable(self) -> None:
        self._flush_input()

    def _install_writer(self) -> None:
        if (
            self._loop is not None
            and self._master_fd is not None
            and not self._writer_installed
            and not self._closed
        ):
            self._loop.add_writer(self._master_fd, self._on_writable)
            self._writer_installed = True

    def _remove_writer(self) -> None:
        if self._writer_installed and self._loop is not None and self._master_fd is not None:
            self._loop.remove_writer(self._master_fd)
        self._writer_installed = False

    def resize(self, cols: object, rows: object) -> None:
        """Clamp and apply a resize (FR-060)."""
        self._cols = clamp_dimension(cols)
        self._rows = clamp_dimension(rows)
        if self._master_fd is not None:
            try:
                apply_winsize(self._master_fd, self._cols, self._rows)
            except OSError:
                pass

    # -- state / observability -------------------------------------------

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def buffered_bytes(self) -> int:
        return self._buffered

    @property
    def is_stalled(self) -> bool:
        """True when the reader has been paused longer than the stall timeout.

        The WS handler polls this to close a wedged client with
        ``try_again_later`` (1013).
        """
        if not self._paused or self._paused_since is None or self._loop is None:
            return False
        return (self._loop.time() - self._paused_since) >= self._stall_timeout_s

    @property
    def pid(self) -> int | None:
        """OS pid of the child (ssh) process, or ``None`` before ``start()``.

        Exposed for precise, in-process child-process accounting (e.g. the
        soak test, T061/NFR-004) -- callers should prefer this + the
        registry's own tracked sessions over external process enumeration,
        since it reflects exactly what this class spawned.
        """
        return self._proc.pid if self._proc is not None else None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    @property
    def error_class(self) -> ErrorClass | None:
        return classify_exit(self._returncode, bytes(self._recent))

    async def wait(self) -> int:
        """Await child exit, caching and returning its return code."""
        if self._proc is None:
            return self._returncode if self._returncode is not None else 0
        rc = await self._proc.wait()
        self._returncode = rc
        return rc

    # -- teardown ---------------------------------------------------------

    async def close(self) -> None:
        """Reap the process group and close PTY fds. Idempotent / partial-safe."""
        if self._closed:
            return
        self._closed = True
        self._remove_reader()
        self._remove_writer()

        proc = self._proc
        if proc is not None and proc.returncode is None:
            await self._terminate(proc)
        if proc is not None:
            self._returncode = proc.returncode

        # Unblock any pending read_output() waiter.
        self._push_eof()

        for fd in (self._master_fd, self._slave_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._master_fd = None
        self._slave_fd = None

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        pgid = proc.pid  # start_new_session=True => group leader (pgid == pid).
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(proc.wait(), self._term_grace_s)
            return
        except (TimeoutError, asyncio.TimeoutError):
            pass
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(proc.wait(), 2.0)
        except (TimeoutError, asyncio.TimeoutError):
            pass
