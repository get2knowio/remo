"""Public host-registry API — thin delegates onto :mod:`core.registry`.

Every existing call site in ``providers/*`` and ``cli/*`` keeps working
unchanged (FR-015); the accessor in :mod:`remo_cli.core.registry` owns all
parsing, serialization, validation, locking, and migration.
"""

from __future__ import annotations

import os
import sys

from remo_cli.core.registry import (
    MigrationReport,
    migrate_if_needed,
    mutate_registry,
    read_registry,
)
from remo_cli.models.host import KnownHost

_migration_notice_shown = False


def _print_migration_notice(report: MigrationReport) -> None:
    """Print the one-time plain-language migration notice (FR-025/FR-026)."""
    from remo_cli.core.output import print_info, print_warning

    global _migration_notice_shown
    if _migration_notice_shown:
        return
    _migration_notice_shown = True

    print_info(
        f"Migrated {report.migrated_count} registry entr"
        f"{'y' if report.migrated_count == 1 else 'ies'} to the new registry.json "
        f"format (backup saved as {report.backup_path.name})."
    )
    if report.skipped_lines:
        print_warning(
            f"Skipped {len(report.skipped_lines)} unrecognized line(s) during "
            f"migration (left untouched in the backup):"
        )
        for line in report.skipped_lines:
            print_warning(f"  {line!r}")
    print_info(
        "Note: the next `remo web push` will re-verify all instances (the "
        "registry format changed)."
    )


def _migrate_and_notify() -> None:
    report = migrate_if_needed()
    if report is not None:
        _print_migration_notice(report)


def save_known_host(host: KnownHost) -> None:
    """Add or replace a host entry in the registry (upsert by (type, name))."""
    _migrate_and_notify()

    def _upsert(hosts: list[KnownHost]) -> list[KnownHost]:
        kept = [h for h in hosts if not (h.type == host.type and h.name == host.name)]
        kept.append(host)
        return kept

    mutate_registry(_upsert)


def remove_known_host(type: str, name: str) -> None:
    """Remove the entry matching (type, name) from the registry, if present."""
    _migrate_and_notify()

    def _drop(hosts: list[KnownHost]) -> list[KnownHost]:
        return [h for h in hosts if not (h.type == type and h.name == name)]

    mutate_registry(_drop)


def get_known_hosts(type_filter: str | None = None) -> list[KnownHost]:
    """Return all registered hosts, optionally filtered by type."""
    _migrate_and_notify()
    hosts = read_registry(readonly=False).hosts
    if type_filter is not None:
        hosts = [h for h in hosts if h.type == type_filter]
    return hosts


def clear_known_hosts_by_type(type: str) -> None:
    """Remove all entries whose type equals *type*."""
    _migrate_and_notify()

    def _filter(hosts: list[KnownHost]) -> list[KnownHost]:
        return [h for h in hosts if h.type != type]

    mutate_registry(_filter)


def clear_known_hosts_by_prefix(type: str, prefix: str) -> None:
    """Remove entries where type matches and name starts with *prefix*."""
    _migrate_and_notify()

    def _filter(hosts: list[KnownHost]) -> list[KnownHost]:
        return [h for h in hosts if not (h.type == type and h.name.startswith(prefix))]

    mutate_registry(_filter)


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


def guard_not_added_ssh_host(name: str, provider: str) -> None:
    """FR-012: fail clearly when *name* is a manually-registered SSH host.

    Provider lifecycle operations (``destroy``, ``snapshot`` create/restore/
    delete, resize via ``update``) resolve a host by *name* within their own
    inventory. A ``type="ssh"`` host added via ``remo add`` has no managed
    *provider* infrastructure, so such an operation would otherwise silently
    mis-target (e.g. an Incus teardown against ``localhost``) or emit an opaque
    "not found" / "run sync" error that never tells the user what is wrong.

    When *name* matches an added SSH host — and no host of *provider*'s own type
    also matches it — exit with a clear message pointing the user at
    ``remo remove``. When a same-type managed host also matches (e.g. an Incus
    container that happens to share the name), the operation legitimately
    targets that instance and is allowed through.
    """
    all_hosts = get_known_hosts()

    if not any(h.type == "ssh" and h.name == name for h in all_hosts):
        return

    for host in all_hosts:
        if host.type != provider:
            continue
        if host.name == name:
            return
        # incus/proxmox short-name match (container part of "host/container").
        if provider in {"incus", "proxmox"} and "/" in host.name:
            if host.name.split("/", maxsplit=1)[1] == name:
                return

    sys.exit(
        f"Error: '{name}' is a manually-registered SSH host (added via "
        f"'remo add') with no managed {provider} infrastructure. "
        f"Use 'remo remove {name}' to deregister it."
    )


def resolve_remo_host_by_name(name: str) -> KnownHost:
    """Find a registered host by name, matching across all types.

    For *incus* and *proxmox* entries whose name is in ``"host/container"``
    form, this function also matches when *name* equals the container part
    alone (the portion after ``"/"``).

    Raises :exc:`SystemExit` with a descriptive error message when no match is
    found, listing the available environment names so the user can correct the
    typo.
    """
    all_hosts = get_known_hosts()

    # First pass: exact name match.
    for host in all_hosts:
        if host.name == name:
            return host

    # Second pass: incus/proxmox short-name match (container part of "host/container").
    for host in all_hosts:
        if host.type in {"incus", "proxmox"} and "/" in host.name:
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
