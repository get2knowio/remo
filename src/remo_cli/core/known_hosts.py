"""Flat-file registry of registered development environments."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from remo_cli.core.config import get_known_hosts_path
from remo_cli.models.host import KnownHost


def save_known_host(host: KnownHost) -> None:
    """Add or replace a host entry in the registry.

    Ensures the registry file and its parent directory exist.  Any existing
    entry with the same (type, name) pair is removed before the new entry is
    appended, so each (type, name) pair remains unique.

    The write is performed atomically: lines are filtered into a temp file in
    the same directory, then renamed over the registry file.
    """
    registry_path = get_known_hosts_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    if not registry_path.exists():
        registry_path.touch()

    # Read all existing lines, dropping any entry that matches (type, name).
    kept_lines: list[str] = []
    with registry_path.open() as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if not line:
                kept_lines.append(line)
                continue
            try:
                existing = KnownHost.from_line(line)
            except ValueError:
                # Preserve lines that cannot be parsed.
                kept_lines.append(line)
                continue
            if existing.type == host.type and existing.name == host.name:
                # Drop the stale entry; the new one will be appended below.
                continue
            kept_lines.append(line)

    kept_lines.append(host.to_line())

    _write_lines_atomically(registry_path, kept_lines)


def remove_known_host(type: str, name: str) -> None:
    """Remove the entry matching (type, name) from the registry.

    Does nothing if the registry file does not exist or if no matching entry
    is found.
    """
    registry_path = get_known_hosts_path()
    if not registry_path.exists():
        return

    kept_lines: list[str] = []
    with registry_path.open() as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if not line:
                kept_lines.append(line)
                continue
            try:
                existing = KnownHost.from_line(line)
            except ValueError:
                kept_lines.append(line)
                continue
            if existing.type == type and existing.name == name:
                continue
            kept_lines.append(line)

    _write_lines_atomically(registry_path, kept_lines)


def get_known_hosts(type_filter: str | None = None) -> list[KnownHost]:
    """Return all registered hosts, optionally filtered by type.

    Returns an empty list if the registry file does not exist.  Lines that are
    empty or that fail to parse are silently skipped.
    """
    registry_path = get_known_hosts_path()
    if not registry_path.exists():
        return []

    hosts: list[KnownHost] = []
    with registry_path.open() as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                host = KnownHost.from_line(line)
            except ValueError:
                continue
            if type_filter is not None and host.type != type_filter:
                continue
            hosts.append(host)

    return hosts


def clear_known_hosts_by_type(type: str) -> None:
    """Remove all entries whose type equals *type*.

    Does nothing if the registry file does not exist.
    """
    registry_path = get_known_hosts_path()
    if not registry_path.exists():
        return

    kept_lines: list[str] = []
    with registry_path.open() as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if not line:
                kept_lines.append(line)
                continue
            try:
                existing = KnownHost.from_line(line)
            except ValueError:
                kept_lines.append(line)
                continue
            if existing.type == type:
                continue
            kept_lines.append(line)

    _write_lines_atomically(registry_path, kept_lines)


def clear_known_hosts_by_prefix(type: str, prefix: str) -> None:
    """Remove entries where type matches and name starts with *prefix*.

    Used during incus sync to remove stale container entries for a particular
    Incus host before re-populating them.  For example::

        clear_known_hosts_by_prefix("incus", "myhost/")

    Does nothing if the registry file does not exist.
    """
    registry_path = get_known_hosts_path()
    if not registry_path.exists():
        return

    kept_lines: list[str] = []
    with registry_path.open() as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if not line:
                kept_lines.append(line)
                continue
            try:
                existing = KnownHost.from_line(line)
            except ValueError:
                kept_lines.append(line)
                continue
            if existing.type == type and existing.name.startswith(prefix):
                continue
            kept_lines.append(line)

    _write_lines_atomically(registry_path, kept_lines)


def get_aws_region(name: str) -> str:
    """Return the AWS region for the named host.

    Resolution order:
    1. ``region`` field of the matching AWS entry in the registry (if non-empty)
    2. ``AWS_REGION`` environment variable
    3. ``AWS_DEFAULT_REGION`` environment variable
    4. Hard-coded fallback ``"us-west-2"``
    """
    for host in get_known_hosts(type_filter="aws"):
        if host.name == name and host.region:
            return host.region

    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-west-2"
    )


def resolve_remo_host_by_name(name: str) -> KnownHost:
    """Find a registered host by name, matching across all types.

    For *incus* entries whose name is in ``"host/container"`` form, this
    function also matches when *name* equals the container part alone (the
    portion after ``"/"``).

    Raises :exc:`SystemExit` with a descriptive error message when no match is
    found, listing the available environment names so the user can correct the
    typo.
    """
    all_hosts = get_known_hosts()

    # First pass: exact name match.
    for host in all_hosts:
        if host.name == name:
            return host

    # Second pass: incus short-name match (container part of "host/container").
    for host in all_hosts:
        if host.type == "incus" and "/" in host.name:
            _, container = host.name.split("/", maxsplit=1)
            if container == name:
                return host

    # Nothing matched — build a helpful error message.
    available = [h.display_name for h in all_hosts]
    if available:
        listing = "\n  ".join(available)
        sys.exit(
            f"Error: no environment named '{name}' found in the registry.\n"
            f"Available environments:\n  {listing}"
        )
    else:
        sys.exit(
            f"Error: no environment named '{name}' found in the registry.\n"
            "The registry is empty. Use 'remo add' to register an environment."
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_lines_atomically(path: Path, lines: list[str]) -> None:
    """Write *lines* to *path* atomically via a temp file + rename.

    A temporary file is created in the same directory as *path* so that the
    ``os.replace`` rename is guaranteed to be on the same filesystem (and
    therefore atomic on POSIX systems).
    """
    dir_ = path.parent
    fd, tmp_path_str = tempfile.mkstemp(dir=dir_, prefix=".known_hosts_tmp_")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w") as fh:
            for line in lines:
                fh.write(line + "\n")
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
