"""Snapshot name generation, validation, and table formatting.

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

from remo_cli.models.snapshot import Snapshot

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


def _humanize_size(num_bytes: int | None) -> str:
    """Render bytes as a human-friendly size string (e.g. ``1.2 GiB``).

    Returns ``"—"`` when the provider didn't report a size.
    """
    if num_bytes is None:
        return "—"
    if num_bytes < 0:
        return "—"
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    value = float(num_bytes)
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.1f} {units[idx]}"


def format_snapshot_table(
    snapshots: list[Snapshot],
    *,
    show_status: bool,
    instance_label: str | None = None,
) -> str:
    """Render *snapshots* as a column-aligned text table.

    *show_status* is decided by the caller per provider:
      * ``True``  — AWS and Hetzner (async creation, status meaningful)
      * ``False`` — Incus and Proxmox (creation is synchronous; status
        is always AVAILABLE)

    When *snapshots* is empty, returns the FR-010 empty-state message
    referencing *instance_label* if supplied.
    """
    if not snapshots:
        if instance_label:
            return f"No snapshots found for instance '{instance_label}'."
        return "No snapshots found."

    # Columns: INSTANCE  SNAPSHOT  CREATED  SIZE  [STATUS]  DESCRIPTION
    headers = ["INSTANCE", "SNAPSHOT", "CREATED", "SIZE"]
    if show_status:
        headers.append("STATUS")
    headers.append("DESCRIPTION")

    rows: list[list[str]] = [headers]
    for s in snapshots:
        created = s.created_at.strftime("%Y-%m-%d %H:%M:%S")
        row = [s.instance_name, s.name, created, _humanize_size(s.size_bytes)]
        if show_status:
            row.append(s.status.value)
        row.append(s.description)
        rows.append(row)

    # Compute column widths (last column free-form, not padded)
    widths = [0] * (len(headers) - 1)
    for row in rows:
        for i, cell in enumerate(row[:-1]):
            widths[i] = max(widths[i], len(cell))

    lines: list[str] = []
    for row in rows:
        padded = [cell.ljust(widths[i]) for i, cell in enumerate(row[:-1])]
        lines.append("  ".join(padded + [row[-1]]).rstrip())
    return "\n".join(lines)
