"""Snapshot name generation and validation.

The validation rules use the intersection of provider-side limits so the
same name is portable across Incus / Proxmox / AWS / Hetzner:

* length 1–40 characters
* first character must be alphanumeric (no leading ``-``)
* remaining characters: ``A-Z`` ``a-z`` ``0-9`` ``_`` ``-``
"""

from __future__ import annotations

import re
from datetime import datetime

import click

# Anchored, single-pass: first char alphanumeric, rest alphanumeric/_/-.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_MAX_LEN = 40


def generate_default_name() -> str:
    """Return a timestamp-based default snapshot name.

    Format: ``remo-YYYYMMDD-HHMMSS`` using local time. Lexicographic sort
    order matches creation order.
    """
    return datetime.now().strftime("remo-%Y%m%d-%H%M%S")


def validate_name(name: str) -> None:
    """Raise :class:`click.BadParameter` if *name* violates the rules.

    Returns ``None`` on success. Callers should let the exception propagate
    so Click renders it as a parameter-validation error (exit code 2).
    """
    if not name or len(name) > _MAX_LEN:
        raise click.BadParameter(
            f"snapshot name must be 1–{_MAX_LEN} characters long; got {len(name)}"
        )
    if not _NAME_RE.match(name):
        raise click.BadParameter(
            "snapshot name must start with a letter or digit and contain only "
            "letters, digits, '_' or '-'"
        )
