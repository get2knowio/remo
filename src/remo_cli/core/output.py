"""Terminal output helpers matching the remo bash script's output style."""

from __future__ import annotations

import sys

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
