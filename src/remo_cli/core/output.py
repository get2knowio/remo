"""Terminal output helpers matching the remo bash script's output style."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from remo_cli.models.host import KnownHost

# ANSI color constants
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"  # No Color / Reset

# Affirmative responses accepted by confirm()
_AFFIRMATIVE = {"yes", "y", "ye", "yeah", "yep", "yup", "sure", "ok"}


def print_error(msg: str) -> None:
    """Print an error message in red to stderr, prefixed with 'Error:'."""
    sys.stderr.write(f"{RED}Error:{NC} {msg}\n")


def print_success(msg: str) -> None:
    """Print a success message in green to stdout."""
    print(f"{GREEN}{msg}{NC}")


def print_info(msg: str) -> None:
    """Print an informational message in blue to stdout."""
    print(f"{BLUE}{msg}{NC}")


def print_warning(msg: str) -> None:
    """Print a warning message in yellow to stdout."""
    print(f"{YELLOW}{msg}{NC}")


def confirm(prompt: str, default: bool = False) -> bool:
    """Ask the user for yes/no confirmation.

    Displays the prompt with ``[Y/n]`` when *default* is ``True`` or
    ``[y/N]`` when *default* is ``False``.  An empty response returns
    *default*.  Any affirmative word (yes, y, ye, yeah, yep, yup, sure,
    ok — case-insensitive) returns ``True``; anything else returns
    ``False``.
    """
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{prompt} {suffix} ").strip().lower()
    except EOFError:
        return default

    if not answer:
        return default

    return answer in _AFFIRMATIVE


@dataclass(frozen=True)
class Column:
    """One `render_host_table` column: a header plus how to extract its
    value from a `KnownHost` entry."""

    header: str
    value: Callable[[KnownHost], str]
    width: int = 20


def render_host_table(
    entries: list[KnownHost], columns: tuple[Column, ...], *, empty_message: str = "No instances registered."
) -> None:
    """Print *entries* as a column-aligned table (FR-016; replaces the four
    per-provider `list_hosts()` renderers). The last column is left
    unpadded (free-form width)."""
    if not entries:
        print(empty_message)
        return

    headers = [c.header for c in columns]
    rows = [[c.value(entry) for c in columns] for entry in entries]

    widths = [c.width for c in columns[:-1]]
    header_line = "  ".join(
        h.ljust(widths[i]) if i < len(widths) else h for i, h in enumerate(headers)
    )
    print(header_line)
    print("  ".join("-" * len(h) if i >= len(widths) else ("-" * len(h)).ljust(widths[i]) for i, h in enumerate(headers)))

    for row in rows:
        line = "  ".join(cell.ljust(widths[i]) if i < len(widths) else cell for i, cell in enumerate(row))
        print(line)
