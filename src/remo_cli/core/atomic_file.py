"""One atomic-write and one advisory-lock primitive for every remo state file.

Issue #244: ``core/registry.py``, ``core/tab_records.py``, ``core/web_adopt.py``,
``web/trust_store.py``, ``web/mirror_meta.py`` and ``web/jobs.py`` each carried
a private temp-file + ``os.replace`` copy (and two an ``flock`` loop), and the
copies had already drifted — the tab-record lock silently proceeded unlocked
where the registry lock warned. Everything here is stdlib-only and knows
nothing about which file it is protecting; callers keep their own error types
by passing a ``busy_error`` factory or translating at the call site.

The write contract: a reader sees either the old file or the new one, never a
torn one, because the temp file lives in the target's own directory (so the
rename cannot cross a filesystem) and is unlinked on any failure — including a
``KeyboardInterrupt`` mid-write. Parent directories are the caller's job, since
callers disagree about their modes (``web-identity/`` is created 0700).
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: The 50ms retry cadence ``registry_lock`` has always used (015 research R3).
_LOCK_POLL_S = 0.05

#: Lock paths that have already warned about missing ``flock`` support, so a
#: process prints the degradation warning once per file, not once per write.
_unsupported_warned: set[str] = set()


class LockBusyError(Exception):
    """Default error when an advisory lock cannot be taken within its timeout."""


def atomic_write_text(
    path: Path,
    text: str,
    *,
    mode: int | None = None,
    prefix: str | None = None,
    suffix: str = "",
    encoding: str = "utf-8",
) -> None:
    """Replace *path* with *text* atomically.

    The temp file is ``mkstemp``-created, so it (and therefore the replaced
    file) is 0600 unless *mode* says otherwise; *mode* is applied to the temp
    file before the rename, so the target never exists with a wider mode.
    *prefix* defaults to ``.<name>.`` so a crash leaves an obviously-owned
    dotfile next to the target rather than an anonymous ``tmp*``.
    """
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=prefix if prefix is not None else f".{path.name}.",
        suffix=suffix,
    )
    try:
        if mode is not None:
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(
    path: Path,
    doc: Any,
    *,
    indent: int | None = None,
    trailing_newline: bool = False,
    ensure_ascii: bool = True,
    mode: int | None = None,
    prefix: str | None = None,
    suffix: str = "",
) -> None:
    """``json.dumps`` *doc* and hand it to :func:`atomic_write_text`.

    The serialization knobs exist because the migrated writers already disagree
    on them (compact vs ``indent=2``, with or without a final newline) and
    their on-disk bytes must not change.
    """
    text = json.dumps(doc, indent=indent, ensure_ascii=ensure_ascii)
    if trailing_newline:
        text += "\n"
    atomic_write_text(path, text, mode=mode, prefix=prefix, suffix=suffix)


def warn_lock_unavailable_once(lock_path: Path) -> None:
    """Default degradation notice: one warning per lock file per process."""
    key = str(lock_path)
    if key in _unsupported_warned:
        return
    _unsupported_warned.add(key)
    from remo_cli.core.output import print_warning  # noqa: PLC0415

    print_warning(
        f"file locking unavailable on this filesystem ({lock_path.name}); "
        "concurrent writes may race."
    )


@contextmanager
def advisory_lock(
    lock_path: Path,
    *,
    timeout_s: float = 5.0,
    file_mode: int = 0o644,
    busy_error: Callable[[], BaseException] | None = None,
    on_unsupported: Callable[[], None] | None = None,
) -> Iterator[None]:
    """Hold an exclusive ``flock`` on the sidecar *lock_path* for the block.

    ``LOCK_EX | LOCK_NB`` polled every 50ms until *timeout_s*, then the
    exception *busy_error* builds (raised ``from None``; default
    :class:`LockBusyError`). On a filesystem without ``flock`` (``ENOLCK`` /
    ``EOPNOTSUPP``, e.g. some network mounts) the block runs unlocked after
    *on_unsupported* (default :func:`warn_lock_unavailable_once`): refusing to
    write at all would be worse than an unlikely race. Any other ``OSError``
    opening the sidecar propagates unchanged.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, file_mode)
    acquired = False
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as e:
                if e.errno in (errno.ENOLCK, errno.EOPNOTSUPP):
                    if on_unsupported is not None:
                        on_unsupported()
                    else:
                        warn_lock_unavailable_once(lock_path)
                    break
                if time.monotonic() >= deadline:
                    if busy_error is not None:
                        raise busy_error() from None
                    raise LockBusyError(
                        f"{lock_path} is busy — another remo process is writing"
                    ) from None
                time.sleep(_LOCK_POLL_S)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)
