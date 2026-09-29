"""Entry-point provider discovery — core/provider_plugins.py (027).

An installed distribution contributes a provider by declaring an entry point
in the ``remo.providers`` group whose object is a ``ProviderDescriptor`` or a
zero-argument callable returning one (contracts/plugin-contract.md). This
module loads those entry points defensively: one warning per failure, never an
exception to the caller, first registration wins on a duplicate ``type_name``,
and ``REMO_DISABLE_PROVIDER_PLUGINS=1`` skips the whole step. It is called
exactly once per process from ``provider_registry._ensure_discovered()``.

It knows no provider's name (Principle I/II): everything it learns comes from
package metadata. Only the standard library is used.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Literal

from remo_cli.core.provider_registry import (
    PROVIDER_API_VERSION,
    ProviderDescriptor,
    descriptor_source,
    is_provider_type,
    register,
)

ENTRY_POINT_GROUP = "remo.providers"
DISABLE_ENV_VAR = "REMO_DISABLE_PROVIDER_PLUGINS"
API_VERSION_ATTR = "REMO_PROVIDER_API_VERSION"
_TRUTHY = frozenset({"1", "true", "yes"})


@dataclass(frozen=True)
class PluginLoadRecord:
    """What happened to one ``remo.providers`` entry point (data-model E2)."""

    distribution: str
    version: str
    entry_point: str
    type_name: str | None
    status: Literal["loaded", "skipped"]
    reason: str = ""
    api_version: int | None = None

    @property
    def source(self) -> str:
        return f"{self.distribution} {self.version}"


_RECORDS: list[PluginLoadRecord] = []
_disabled_at_discovery = False


def plugins_disabled() -> bool:
    """True when ``REMO_DISABLE_PROVIDER_PLUGINS`` is ``1``/``true``/``yes`` (FR-004)."""
    return os.environ.get(DISABLE_ENV_VAR, "").strip().lower() in _TRUTHY


def plugin_load_records() -> tuple[PluginLoadRecord, ...]:
    """Every entry point seen by the last discovery, loaded or skipped (FR-012/13)."""
    return tuple(_RECORDS)


def discovery_was_disabled() -> bool:
    """True when the last discovery was skipped by the disable variable."""
    return _disabled_at_discovery


def _reset_records() -> None:
    global _disabled_at_discovery
    _RECORDS.clear()
    _disabled_at_discovery = False


def _iter_entry_points() -> Iterable[Any]:
    """The ``remo.providers`` entry points, sorted by name then value so the
    registration order is deterministic (FR-002). Patched by tests."""
    return sorted(
        metadata.entry_points(group=ENTRY_POINT_GROUP), key=lambda ep: (ep.name, ep.value)
    )


def _dist_name_version(entry_point: Any) -> tuple[str, str]:
    dist = getattr(entry_point, "dist", None)
    if dist is None:
        return "unknown-distribution", "unknown"
    name = getattr(dist, "name", None) or "unknown-distribution"
    version = getattr(dist, "version", None) or "unknown"
    return str(name), str(version)


def _declared_api_version(entry_point: Any) -> int | None:
    module_name = getattr(entry_point, "module", None) or str(entry_point.value).split(":", 1)[0]
    module = importlib.import_module(module_name)
    declared = getattr(module, API_VERSION_ATTR, None)
    return declared if isinstance(declared, int) and not isinstance(declared, bool) else None


def _resolve_descriptor(loaded: object) -> ProviderDescriptor:
    obj = loaded
    if not isinstance(obj, ProviderDescriptor) and callable(obj):
        obj = obj()
    if isinstance(obj, ProviderDescriptor):
        return obj
    raise TypeError(
        f"expected a ProviderDescriptor (or a callable returning one), got {type(obj).__name__}"
    )


def discover_entry_points(warn: Callable[[str], None] | None = None) -> list[PluginLoadRecord]:
    """Load every ``remo.providers`` entry point into the provider registry.

    Never raises for a bad plugin: each failure becomes one warning line and a
    ``skipped`` record (FR-003). Returns the records (also kept for
    ``plugin_load_records()``).
    """
    global _disabled_at_discovery
    _RECORDS.clear()
    if warn is None:
        from remo_cli.core.output import print_warning  # noqa: PLC0415

        warn = print_warning

    if plugins_disabled():
        _disabled_at_discovery = True
        return []
    _disabled_at_discovery = False

    for entry_point in _iter_entry_points():
        dist_name, dist_version = _dist_name_version(entry_point)
        ep_name = str(getattr(entry_point, "name", "?"))
        label = f"provider plugin {ep_name!r} from {dist_name} {dist_version}"
        api_version: int | None = None
        try:
            api_version = _declared_api_version(entry_point)
            if api_version != PROVIDER_API_VERSION:
                declared = "undeclared" if api_version is None else str(api_version)
                warn(
                    f"{label} targets provider API version {declared}; this remo provides "
                    f"{PROVIDER_API_VERSION}. Loading it anyway — pin the remo version the "
                    f"plugin was built against if it misbehaves."
                )
            descriptor = _resolve_descriptor(entry_point.load())
            if is_provider_type(descriptor.type_name):
                raise ValueError(
                    f"provider type {descriptor.type_name!r} is already registered by "
                    f"{descriptor_source(descriptor.type_name)}"
                )
            register(descriptor, source=f"{dist_name} {dist_version}")
        except Exception as exc:  # noqa: BLE001 — a broken plugin must never brick the CLI
            reason = f"{type(exc).__name__}: {exc}"
            warn(f"Skipping {label}: {reason}")
            _RECORDS.append(
                PluginLoadRecord(
                    distribution=dist_name,
                    version=dist_version,
                    entry_point=ep_name,
                    type_name=None,
                    status="skipped",
                    reason=reason,
                    api_version=api_version,
                )
            )
            continue
        _RECORDS.append(
            PluginLoadRecord(
                distribution=dist_name,
                version=dist_version,
                entry_point=ep_name,
                type_name=descriptor.type_name,
                status="loaded",
                api_version=api_version,
            )
        )
    return list(_RECORDS)
