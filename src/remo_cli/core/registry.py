"""Versioned structured host registry accessor (Registry v2).

Single module owning parse/serialize/validate/lock/migrate for the registry
(FR-012). Every consumer — CLI, providers, web service — goes through this
surface. See specs/015-registry-v2/contracts/registry-accessor-api.md for the
authoritative contract.

This module never raises :class:`SystemExit` (FR-013); callers at the CLI or
web boundary translate the error taxonomy below into user-facing behavior.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from remo_cli.core.config import (
    get_known_hosts_path,
    get_known_hosts_path_readonly,
    get_registry_backup_path,
    get_registry_lock_path,
    get_registry_path,
    get_registry_path_readonly,
)
from remo_cli.models.host import KnownHost

SUPPORTED_VERSION = 2
KNOWN_TYPES = frozenset({"incus", "proxmox", "aws", "hetzner", "ssh"})


# ---------------------------------------------------------------------------
# Error taxonomy (FR-013: never SystemExit)
# ---------------------------------------------------------------------------


class RegistryError(Exception):
    """Base class for all registry accessor errors."""


class RegistryReadError(RegistryError):
    """The registry file is unreadable or its top-level document is invalid."""


class RegistryValidationError(RegistryError):
    """A write would violate validation rules V1-V6; disk is left untouched."""


class RegistryBusyError(RegistryError):
    """The advisory lock could not be acquired within the timeout."""


class RegistryNewerVersionError(RegistryError):
    """The registry file was written by a newer, unsupported format version."""


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistryView:
    """The result of a registry read."""

    hosts: list[KnownHost]
    warnings: list[str]
    source_format: str  # "v2" | "legacy" | "empty"
    unknown_entries: int


@dataclass(frozen=True)
class MigrationReport:
    """The result of a completed lazy migration (S1 -> S2)."""

    migrated_count: int
    backup_path: Path
    skipped_lines: list[str]


@dataclass
class _LegacyParseResult:
    hosts: list[KnownHost]
    unknown_raw: list[dict[str, Any]]
    skipped_lines: list[str]
    warnings: list[str]


@dataclass
class _ParsedDocument:
    hosts: list[KnownHost]
    unknown_raw: list[dict[str, Any]]
    warnings: list[str]
    source_format: str


# ---------------------------------------------------------------------------
# Entry <-> KnownHost mapping (data-model.md §2/§3)
# ---------------------------------------------------------------------------


def known_host_to_entry(host: KnownHost) -> dict[str, Any]:
    """Serialize a :class:`KnownHost` into a v2 hostEntry dict (key order fixed)."""
    entry: dict[str, Any] = {
        "type": host.type,
        "name": host.name,
        "host": host.host,
        "user": host.user,
        "access": host.access_mode or "direct",
    }

    nested: dict[str, Any] = {}
    if host.type == "incus":
        if host.instance_id:
            nested["host_user"] = host.instance_id
    elif host.type == "proxmox":
        if host.instance_id:
            nested["vmid"] = host.instance_id
        if host.region:
            nested["node_user"] = host.region
    elif host.type == "aws":
        if host.instance_id:
            nested["instance_id"] = host.instance_id
        if host.region:
            nested["region"] = host.region
    elif host.type == "ssh":
        if host.instance_id:
            try:
                nested["port"] = int(host.instance_id)
            except ValueError:
                pass
        if host.region:
            nested["identity_file"] = host.region
    # hetzner: no nested fields today.

    if nested:
        entry[host.type] = nested

    return entry


def entry_to_known_host(entry: dict[str, Any]) -> KnownHost | None:
    """Parse a KNOWN-type hostEntry dict into a :class:`KnownHost`.

    Returns ``None`` if the entry's shape does not match the contract (the
    caller turns this into a tolerant-read warning, never an exception).
    """
    type_ = entry.get("type")
    name = entry.get("name")
    host = entry.get("host")
    user = entry.get("user")
    access = entry.get("access")

    if not (
        isinstance(type_, str)
        and isinstance(name, str)
        and isinstance(host, str)
        and isinstance(user, str)
        and isinstance(access, str)
    ):
        return None
    if not (type_ and name and host and user):
        return None
    if access not in ("direct", "ssm"):
        return None
    if type_ not in KNOWN_TYPES:
        return None

    instance_id = ""
    region = ""

    if type_ == "incus":
        nested = entry.get("incus")
        if isinstance(nested, dict):
            instance_id = str(nested.get("host_user", "") or "")
    elif type_ == "proxmox":
        nested = entry.get("proxmox")
        if isinstance(nested, dict):
            instance_id = str(nested.get("vmid", "") or "")
            region = str(nested.get("node_user", "") or "")
    elif type_ == "aws":
        nested = entry.get("aws")
        if isinstance(nested, dict):
            instance_id = str(nested.get("instance_id", "") or "")
            region = str(nested.get("region", "") or "")
    elif type_ == "ssh":
        nested = entry.get("ssh")
        if isinstance(nested, dict):
            port = nested.get("port", "")
            instance_id = str(port) if port != "" else ""
            region = str(nested.get("identity_file", "") or "")
    # hetzner: nothing further.

    return KnownHost(
        type=type_,
        name=name,
        host=host,
        user=user,
        instance_id=instance_id,
        access_mode=access,
        region=region,
    )


def legacy_fields_to_entry(
    type_: str,
    name: str,
    host: str,
    user: str,
    instance_id: str,
    access_mode: str,
    region: str,
) -> dict[str, Any]:
    """Map legacy 7-field values to a v2 hostEntry dict.

    Keyed on ``type_`` FIRST (research R5): ``instance_id`` is meaningless
    without knowing the type. Shared by CLI migration and setup-API v1
    payload mapping (research R8/R9) — the single legacy->v2 mapper.
    """
    if type_ not in KNOWN_TYPES:
        entry: dict[str, Any] = {
            "type": type_,
            "name": name,
            "host": host,
            "user": user,
            "access": "direct",
        }
        legacy_fields = [instance_id, access_mode, region]
        if any(legacy_fields):
            entry["_legacy_fields"] = legacy_fields
        return entry

    access = "direct"
    if type_ == "aws" and (access_mode == "ssm" or (instance_id and not access_mode)):
        access = "ssm"

    entry = {
        "type": type_,
        "name": name,
        "host": host,
        "user": user,
        "access": access,
    }

    nested: dict[str, Any] = {}
    if type_ == "incus":
        if instance_id:
            nested["host_user"] = instance_id
    elif type_ == "proxmox":
        if instance_id:
            nested["vmid"] = instance_id
        if region:
            nested["node_user"] = region
    elif type_ == "aws":
        if instance_id:
            nested["instance_id"] = instance_id
        if region:
            nested["region"] = region
    elif type_ == "ssh":
        if instance_id:
            try:
                nested["port"] = int(instance_id)
            except ValueError:
                pass
        if region:
            nested["identity_file"] = region

    if nested:
        entry[type_] = nested

    return entry


# ---------------------------------------------------------------------------
# Validation (data-model.md §5, rules V1-V6)
# ---------------------------------------------------------------------------


def _has_forbidden_chars(value: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)


def _validate_single_host(h: KnownHost) -> str | None:
    """Validate one host's fields (rules V2-V6). Returns an error message
    naming the field + entry, or ``None`` when valid. Does NOT check
    cross-entry uniqueness (V1) — that is the caller's responsibility.
    """
    entry_label = f"{h.type}:{h.name}"
    for field_name, value in (
        ("type", h.type),
        ("name", h.name),
        ("host", h.host),
        ("user", h.user),
    ):
        if not value:
            return f"{field_name} must not be empty (entry: {entry_label})"
        if _has_forbidden_chars(value):
            return (
                f"{field_name} contains control characters or newlines "
                f"(entry: {entry_label})"
            )
    for field_name, value in (
        ("instance_id", h.instance_id),
        ("region", h.region),
    ):
        if value and _has_forbidden_chars(value):
            return (
                f"{field_name} contains control characters or newlines "
                f"(entry: {entry_label})"
            )

    # An empty access_mode is KnownHost's dataclass default and has always
    # meant "direct" (matches known_host_to_entry's own `or "direct"`
    # normalization) — accept it here rather than rejecting every call site
    # that relies on the default instead of setting it explicitly.
    effective_access = h.access_mode or "direct"
    if effective_access not in ("direct", "ssm"):
        return (
            f"access must be 'direct' or 'ssm', got {h.access_mode!r} "
            f"(entry: {entry_label})"
        )
    if effective_access == "ssm" and h.type != "aws":
        return f"access 'ssm' is only valid for type 'aws' (entry: {entry_label})"

    if h.type == "ssh" and h.instance_id:
        try:
            port = int(h.instance_id)
        except ValueError:
            return f"ssh port must be an integer (entry: {entry_label})"
        if not (1 <= port <= 65535):
            return f"ssh port {port} is out of range 1-65535 (entry: {entry_label})"

    return None


def validate_hosts(hosts: list[KnownHost]) -> None:
    """Validate rules V1-V6. Raises :class:`RegistryValidationError` naming
    field + entry on the first violation found; callers must not write to
    disk when this raises (FR-016).
    """
    seen: set[tuple[str, str]] = set()
    for h in hosts:
        error = _validate_single_host(h)
        if error is not None:
            raise RegistryValidationError(error)
        key = (h.type, h.name)
        if key in seen:
            raise RegistryValidationError(
                f"duplicate entry for type={h.type!r} name={h.name!r}"
            )
        seen.add(key)


# ---------------------------------------------------------------------------
# Legacy codec (tolerant colon-line parse)
# ---------------------------------------------------------------------------


def _parse_legacy_lines(lines: list[str]) -> _LegacyParseResult:
    hosts: list[KnownHost] = []
    unknown_raw: list[dict[str, Any]] = []
    skipped_lines: list[str] = []
    warnings: list[str] = []

    for raw_line in lines:
        line = raw_line.rstrip("\n")
        if not line.strip():
            continue
        try:
            kh = KnownHost.from_line(line)
        except ValueError:
            skipped_lines.append(line)
            warnings.append(f"skipped unparseable legacy line: {line!r}")
            continue

        if not (kh.type and kh.name and kh.host and kh.user):
            skipped_lines.append(line)
            warnings.append(
                f"skipped legacy line with an empty required field: {line!r}"
            )
            continue

        entry = legacy_fields_to_entry(
            kh.type, kh.name, kh.host, kh.user, kh.instance_id, kh.access_mode, kh.region
        )
        if kh.type in KNOWN_TYPES:
            known_host = entry_to_known_host(entry)
            if known_host is None:
                skipped_lines.append(line)
                warnings.append(f"skipped malformed legacy line: {line!r}")
                continue
            hosts.append(known_host)
        else:
            unknown_raw.append(entry)

    return _LegacyParseResult(
        hosts=hosts, unknown_raw=unknown_raw, skipped_lines=skipped_lines, warnings=warnings
    )


def _read_legacy_file(path: Path) -> _ParsedDocument:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RegistryReadError(f"could not read {path}: {e}") from e
    result = _parse_legacy_lines(text.splitlines())
    return _ParsedDocument(
        hosts=result.hosts,
        unknown_raw=result.unknown_raw,
        warnings=result.warnings,
        source_format="legacy",
    )


# ---------------------------------------------------------------------------
# v2 file codec
# ---------------------------------------------------------------------------


def _read_v2_file(path: Path) -> _ParsedDocument:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RegistryReadError(f"could not read {path}: {e}") from e

    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise RegistryReadError(f"{path} is not valid JSON: {e}") from e

    if not isinstance(doc, dict):
        raise RegistryReadError(f"{path}: top-level document must be a JSON object")

    version = doc.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise RegistryReadError(f"{path}: missing or invalid 'version' field")
    if version > SUPPORTED_VERSION:
        raise RegistryNewerVersionError(
            f"{path} was written by a newer version of remo (format {version}); "
            f"upgrade remo, or restore the {get_registry_backup_path().name} backup"
        )
    if version < SUPPORTED_VERSION:
        raise RegistryReadError(f"{path}: unsupported registry version {version}")

    raw_hosts = doc.get("hosts")
    if not isinstance(raw_hosts, list):
        raise RegistryReadError(f"{path}: 'hosts' field must be a list")

    hosts: list[KnownHost] = []
    unknown_raw: list[dict[str, Any]] = []
    warnings: list[str] = []

    for i, raw_entry in enumerate(raw_hosts):
        if not isinstance(raw_entry, dict):
            warnings.append(f"skipped malformed entry at index {i}: not an object")
            continue
        type_ = raw_entry.get("type")
        if not isinstance(type_, str) or not type_:
            warnings.append(f"skipped entry at index {i}: missing or invalid 'type'")
            continue
        if type_ not in KNOWN_TYPES:
            unknown_raw.append(raw_entry)
            continue
        known_host = entry_to_known_host(raw_entry)
        if known_host is None:
            name = raw_entry.get("name", "?")
            warnings.append(
                f"skipped malformed {type_} entry {name!r}: does not match the "
                f"expected shape"
            )
            continue
        hosts.append(known_host)

    return _ParsedDocument(
        hosts=hosts, unknown_raw=unknown_raw, warnings=warnings, source_format="v2"
    )


def _write_v2_file(path: Path, hosts: list[KnownHost], unknown_raw: list[dict[str, Any]]) -> None:
    entries = [known_host_to_entry(h) for h in hosts] + list(unknown_raw)
    entries.sort(key=lambda e: (str(e.get("type", "")), str(e.get("name", ""))))
    doc = {"version": SUPPORTED_VERSION, "hosts": entries}
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    _atomic_write_text(path, text)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".registry_tmp_")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _non_clobbering_backup_path() -> Path:
    base = get_registry_backup_path()
    if not base.exists():
        return base
    i = 1
    while True:
        candidate = base.with_name(f"{base.name}.{i}")
        if not candidate.exists():
            return candidate
        i += 1


def _canonical_entries(hosts: list[KnownHost], unknown_raw: list[dict[str, Any]]) -> set[str]:
    canon = {json.dumps(known_host_to_entry(h), sort_keys=True) for h in hosts}
    canon |= {json.dumps(u, sort_keys=True) for u in unknown_raw}
    return canon


def _entries_equivalent(legacy_result: _LegacyParseResult, v2_parsed: _ParsedDocument) -> bool:
    """Data-model §6 footnote: equivalence excludes unparseable lines/warnings."""
    legacy_set = _canonical_entries(legacy_result.hosts, legacy_result.unknown_raw)
    v2_set = _canonical_entries(v2_parsed.hosts, v2_parsed.unknown_raw)
    return legacy_set == v2_set


_DIVERGENCE_WARNING = (
    "both registry.json and known_hosts are present and their contents differ; "
    "registry.json is authoritative and known_hosts is being ignored (never "
    "merged). Delete known_hosts if it is stale, or re-add any hosts that are "
    "missing from registry.json, to resolve this."
)
_BOTH_PRESENT_READONLY_NOTE = (
    "known_hosts is also present alongside registry.json; registry.json is "
    "authoritative and known_hosts is being ignored."
)


def _resolve_both_present(
    v2_parsed: _ParsedDocument, legacy_path: Path, *, readonly: bool
) -> _ParsedDocument:
    if readonly:
        # Read-only callers never reconcile or rename; they only note that a
        # legacy file is being ignored. So there is no need to read it at all —
        # skipping the read also removes any window for a concurrent migration
        # (which renames the legacy file under the lock) to turn a pure read
        # into a `RegistryReadError` crash.
        return replace(v2_parsed, warnings=[*v2_parsed.warnings, _BOTH_PRESENT_READONLY_NOTE])

    # The `legacy_exists` check in `_read_document` ran unlocked, so the file
    # may have been renamed away by a concurrent migration before we read it.
    # Treat a vanished/unreadable legacy file as "nothing to reconcile" —
    # registry.json is authoritative regardless — rather than crashing.
    legacy_result = _try_parse_legacy_lines_from_path(legacy_path)
    if legacy_result is None:
        return v2_parsed

    if _entries_equivalent(legacy_result, v2_parsed):
        try:
            with registry_lock():
                if legacy_path.exists() and get_registry_path().exists():
                    os.rename(legacy_path, _non_clobbering_backup_path())
        except RegistryBusyError:
            pass  # best-effort; will retry to complete on a future command
        return v2_parsed

    return replace(v2_parsed, warnings=[*v2_parsed.warnings, _DIVERGENCE_WARNING])


def _resolve_both_present_locked(v2_parsed: _ParsedDocument, legacy_path: Path) -> _ParsedDocument:
    """Same as :func:`_resolve_both_present` for a non-readonly caller that
    already holds ``registry_lock()`` (avoids a self-deadlock on re-entry)."""
    legacy_result = _try_parse_legacy_lines_from_path(legacy_path)
    if legacy_result is None:
        return v2_parsed
    if _entries_equivalent(legacy_result, v2_parsed):
        if legacy_path.exists():
            os.rename(legacy_path, _non_clobbering_backup_path())
        return v2_parsed
    return replace(v2_parsed, warnings=[*v2_parsed.warnings, _DIVERGENCE_WARNING])


def _parse_legacy_lines_from_path(path: Path) -> _LegacyParseResult:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise RegistryReadError(f"could not read {path}: {e}") from e
    return _parse_legacy_lines(text.splitlines())


def _try_parse_legacy_lines_from_path(path: Path) -> _LegacyParseResult | None:
    """Like :func:`_parse_legacy_lines_from_path`, but returns ``None`` when the
    file cannot be read (e.g. a concurrent migration renamed it away, or it is
    momentarily unreadable). Used only by both-present reconciliation, where an
    unreadable legacy file simply means "nothing to reconcile against".
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _parse_legacy_lines(text.splitlines())


# ---------------------------------------------------------------------------
# Locking (research R3)
# ---------------------------------------------------------------------------

_lock_degradation_warned = False


def _warn_lock_unavailable_once() -> None:
    global _lock_degradation_warned
    if _lock_degradation_warned:
        return
    _lock_degradation_warned = True
    from remo_cli.core.output import print_warning

    print_warning(
        "registry locking unavailable on this filesystem; concurrent writes may race."
    )


@contextmanager
def registry_lock(timeout_s: float = 5.0) -> Any:
    """Advisory lock on the sidecar ``registry.lock`` file (FR-017/FR-019).

    ``fcntl.flock(LOCK_EX | LOCK_NB)`` with a 50ms retry loop up to
    *timeout_s*, then :class:`RegistryBusyError`. Degrades to an unlocked
    proceed (with a one-time warning) when the filesystem does not support
    ``flock`` (e.g. some network filesystems).
    """
    lock_path = get_registry_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
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
                    _warn_lock_unavailable_once()
                    break
                if time.monotonic() >= deadline:
                    raise RegistryBusyError(
                        "registry is busy — another remo process is writing; "
                        "retry in a moment"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


# ---------------------------------------------------------------------------
# Migration (research R6)
# ---------------------------------------------------------------------------


def _migrate_locked() -> MigrationReport | None:
    """Perform the migration; caller MUST already hold ``registry_lock()``."""
    registry_path = get_registry_path()
    legacy_path = get_known_hosts_path()

    if registry_path.exists():
        return None
    if not legacy_path.exists():
        return None

    legacy_result = _parse_legacy_lines_from_path(legacy_path)

    # Tolerant migration (FR-009/FR-014): a legacy entry that parses but fails
    # v2 validation (e.g. a duplicate (type, name), or an out-of-range ssh
    # port) is skipped and reported — never fatal. Aborting the whole migration
    # over one bad entry would brick every subsequent CLI command until the
    # user hand-edited known_hosts. The original bytes are preserved verbatim
    # in the renamed backup regardless, so nothing is lost.
    valid_hosts: list[KnownHost] = []
    seen: set[tuple[str, str]] = set()
    skipped = list(legacy_result.skipped_lines)
    for h in legacy_result.hosts:
        error = _validate_single_host(h)
        if error is not None:
            skipped.append(f"{h.type}:{h.name} — {error}")
            continue
        key = (h.type, h.name)
        if key in seen:
            skipped.append(
                f"{h.type}:{h.name} — duplicate entry for type={h.type!r} name={h.name!r}"
            )
            continue
        seen.add(key)
        valid_hosts.append(h)

    _write_v2_file(registry_path, valid_hosts, legacy_result.unknown_raw)
    backup_path = _non_clobbering_backup_path()
    os.rename(legacy_path, backup_path)

    return MigrationReport(
        migrated_count=len(valid_hosts) + len(legacy_result.unknown_raw),
        backup_path=backup_path,
        skipped_lines=skipped,
    )


def migrate_if_needed() -> MigrationReport | None:
    """CLI-only trigger; no-op when ``registry.json`` already exists (FR-010)."""
    if get_registry_path().exists():
        return None
    if not get_known_hosts_path().exists():
        return None
    with registry_lock():
        return _migrate_locked()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def _read_document(*, readonly: bool, allow_migrate: bool) -> _ParsedDocument:
    if readonly:
        registry_path = get_registry_path_readonly()
        legacy_path = get_known_hosts_path_readonly()
    else:
        registry_path = get_registry_path()
        legacy_path = get_known_hosts_path()

    registry_exists = registry_path.exists()
    legacy_exists = legacy_path.exists()

    if not registry_exists and not legacy_exists:
        return _ParsedDocument(hosts=[], unknown_raw=[], warnings=[], source_format="empty")

    if not registry_exists and legacy_exists:
        if allow_migrate:
            migrate_if_needed()
            return _read_document(readonly=readonly, allow_migrate=False)
        return _read_legacy_file(legacy_path)

    v2_parsed = _read_v2_file(registry_path)
    if legacy_exists:
        v2_parsed = _resolve_both_present(v2_parsed, legacy_path, readonly=readonly)
    return v2_parsed


def read_registry(*, readonly: bool = False) -> RegistryView:
    """Read the registry. See contracts/registry-accessor-api.md for semantics."""
    parsed = _read_document(readonly=readonly, allow_migrate=not readonly)
    return RegistryView(
        hosts=parsed.hosts,
        warnings=parsed.warnings,
        source_format=parsed.source_format,
        unknown_entries=len(parsed.unknown_raw),
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def _current_document_locked() -> _ParsedDocument:
    """Read current state while already holding ``registry_lock()``.

    Assumes ``_migrate_locked()`` has already run in this same lock scope.
    """
    registry_path = get_registry_path()
    legacy_path = get_known_hosts_path()

    if not registry_path.exists():
        return _ParsedDocument(hosts=[], unknown_raw=[], warnings=[], source_format="empty")

    v2_parsed = _read_v2_file(registry_path)
    if legacy_path.exists():
        v2_parsed = _resolve_both_present_locked(v2_parsed, legacy_path)
    return v2_parsed


def mutate_registry(mutator: Callable[[list[KnownHost]], list[KnownHost]]) -> RegistryView:
    """The only read-modify-write primitive (FR-017 lost-update-safe)."""
    with registry_lock():
        _migrate_locked()
        current = _current_document_locked()
        new_hosts = mutator(list(current.hosts))
        validate_hosts(new_hosts)
        _write_v2_file(get_registry_path(), new_hosts, current.unknown_raw)
        return RegistryView(
            hosts=new_hosts,
            warnings=[],
            source_format="v2",
            unknown_entries=len(current.unknown_raw),
        )


def replace_registry(hosts: list[KnownHost], *, allow_empty: bool = False) -> RegistryView:
    """Wholesale replacement (web setup PUT apply).

    Web wholesale-replace semantics: this deliberately does NOT migrate or
    merge any legacy ``known_hosts`` file. The only caller (web setup
    ``_apply_payload``) removes the legacy mirror file explicitly afterward
    (contracts/mirror-payload-v2.md §3), so migrating it here would (a) leave a
    stray ``known_hosts.v1.bak`` on the service volume and (b) let a *malformed*
    stale mirror abort an otherwise-valid apply with a validation error. Only
    unknown-type entries already in ``registry.json`` are preserved.
    """
    if not hosts and not allow_empty:
        raise RegistryValidationError(
            "refusing to write an empty registry without allow_empty=True"
        )
    with registry_lock():
        registry_path = get_registry_path()
        unknown_raw = _read_v2_file(registry_path).unknown_raw if registry_path.exists() else []
        validate_hosts(hosts)
        _write_v2_file(registry_path, hosts, unknown_raw)
        return RegistryView(
            hosts=hosts,
            warnings=[],
            source_format="v2",
            unknown_entries=len(unknown_raw),
        )
