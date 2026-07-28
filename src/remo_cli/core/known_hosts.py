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


def _is_host_scoped_type(type_name: str) -> bool:
    """True when *type_name* is a registered provider using "host/container"
    names (018 T048 — replaces the literal ``{"incus", "proxmox"}`` checks).
    ``False`` for the ``ssh`` pseudo-type and any unregistered/unknown type.
    """
    from remo_cli.core.provider_registry import NameFormat, get_descriptor, is_provider_type  # noqa: PLC0415

    return is_provider_type(type_name) and get_descriptor(type_name).name_format is NameFormat.HOST_SCOPED


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
    _print_tagging_notice(report)


def _print_tagging_notice(report: MigrationReport) -> None:
    """Point at the command that backfills managed tags (feature 013).

    Managed tagging and registry v2 are unrelated features that ship in the
    same release, so every instance in a migrating registry predates tagging
    and a default `sync` will list it as unmarked. Migration is the one moment
    that reaches exactly that population exactly once, so the notice rides
    along here rather than being discovered later.

    This replaces the implicit backfill that used to run whenever `remo shell`
    offered a tools update: tagging is a provider-side write (an SSH hop to the
    hypervisor for incus/proxmox), and `remo shell` should touch the instance
    only. Only explicit `remo <type> tag` and `remo <type> create` write the
    managed marker — `sync` never does.

    Says "may not be" rather than "are not": we cannot know an instance's tag
    state without reaching the provider, which is the very thing being avoided.
    """
    from remo_cli.core.output import print_info  # noqa: PLC0415
    from remo_cli.core.provider_registry import get_descriptor, is_provider_type  # noqa: PLC0415

    taggable = [
        t
        for t in report.migrated_types
        if is_provider_type(t) and get_descriptor(t).supports_managed_marker
    ]
    if not taggable:
        return

    print_info(
        "Note: instances created before this release may not be tagged as "
        "remo-managed, so a default `sync` will list them as unmarked. Tag "
        "them with:"
    )
    for type_name in taggable:
        scope = " --host <host>" if _is_host_scoped_type(type_name) else ""
        print_info(f"  remo {type_name} tag <name>{scope}")


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
        # HOST_SCOPED-type short-name match (container part of "host/container").
        if _is_host_scoped_type(provider) and "/" in host.name:
            if host.name.split("/", maxsplit=1)[1] == name:
                return

    from remo_cli.core.errors import PreconditionError  # noqa: PLC0415

    raise PreconditionError(
        f"'{name}' is a manually-registered SSH host (added via "
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

    # Second pass: HOST_SCOPED-type short-name match (container part of "host/container").
    for host in all_hosts:
        if _is_host_scoped_type(host.type) and "/" in host.name:
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
