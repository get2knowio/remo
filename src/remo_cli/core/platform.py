"""Platform support gate.

remo is POSIX-only, and without this gate the failure is inscrutable:
``core/registry.py`` imports ``fcntl`` at module scope, so a native-Windows
install dies with ``ModuleNotFoundError: No module named 'fcntl'`` while
``cli/main.py`` is still importing its command modules -- before Click parses
anything. No command reports the real problem, not even ``--help``.

Deliberately a pure predicate plus a constant, importing nothing from
``remo_cli``: this runs at the earliest point of CLI startup, and anything it
imported could itself be the thing that fails on Windows. The exit belongs to
the cli layer (Principle III), so ``cli/main.py`` does the echoing and exiting.
"""

from __future__ import annotations

import sys

# ``sys.platform`` values remo cannot run on. Only native Windows qualifies:
# Cygwin ("cygwin") and MSYS2 do provide fcntl, so they are left alone rather
# than blessed -- untested is not the same as known-broken.
UNSUPPORTED_PLATFORMS: frozenset[str] = frozenset({"win32"})

WSL_DOCS_URL = "https://learn.microsoft.com/windows/wsl/install"

UNSUPPORTED_PLATFORM_MESSAGE = f"""\
Error: remo requires Linux or macOS and cannot run natively on Windows.

Windows Python has no 'fcntl' module, which remo's registry locking needs, and
remo drives Ansible, which does not support Windows as a control node either.

Run remo inside WSL2 instead:

    wsl --install -d Ubuntu

then, in the Ubuntu shell:

    uv tool install remo-cli

More on WSL2: {WSL_DOCS_URL}"""


def is_supported_platform(platform: str | None = None) -> bool:
    """True when remo can run here. ``platform`` defaults to ``sys.platform``
    and exists so tests can check both sides without mutating interpreter
    state (faking ``sys.platform`` before ``import click`` sends Click down its
    own Windows branch and into ``msvcrt``)."""
    resolved = sys.platform if platform is None else platform
    return resolved not in UNSUPPORTED_PLATFORMS
