"""Minimal fifth-provider fixture — proves SC-001 (contracts/provider-protocol.md).

A descriptor + a free-function module satisfying the full ``Provider``
Protocol, registered only for the duration of a test via
``provider_registry.temporary_registration()``. No existing CLI/provider
file is touched to make this provider's full ``remo fake ...`` command
group appear.
"""

from __future__ import annotations

from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.core.provider_registry import (
    ArgumentSpec,
    CommandSpec,
    ConnectionSpec,
    NameFormat,
    OptionSpec,
    ProviderDescriptor,
)
from remo_cli.core.reconcile import ProbeResult, SyncScope
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot

FAKE_EXTRA_OPTION = OptionSpec(name="--widget", param="widget", default="", help="Fake extra option.")
FAKE_RESIZE_DIMENSION = OptionSpec(
    name="--volume-size", param="volume_size", default="", help="Fake volume size."
)
FAKE_HOST_OPTION = OptionSpec(name="--flavor", param="flavor", default="", help="Fake host option.")

DESCRIPTOR = ProviderDescriptor(
    type_name="fake",
    display_name="Fake",
    default_instance_name="fake1",
    name_format=NameFormat.FLAT,
    registry_fields=(),
    connection=ConnectionSpec(),
    implementation="tests.unit.providers.fake_provider",
    create_options=(FAKE_EXTRA_OPTION,),
    resize_dimensions=(FAKE_RESIZE_DIMENSION,),
    supports_managed_marker=True,
    host_commands=(
        CommandSpec(
            name="prep",
            help="Fake host-plane command.",
            impl="host_prep",
            options=(FAKE_HOST_OPTION,),
            target=ArgumentSpec("host", default="localhost", required=False),
        ),
    ),
    sdk_extra=None,
)

# In-memory "registry" for this fake provider's instances.
_INSTANCES: dict[str, KnownHost] = {}
_TAGGED: set[str] = set()


def reset() -> None:
    _INSTANCES.clear()
    _TAGGED.clear()


# ---------------------------------------------------------------------------
# Heterogeneous, descriptor-declared verbs (Part B)
# ---------------------------------------------------------------------------


def create(
    name: str,
    widget: str = "",
    volume_size: str = "",
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> int:
    _INSTANCES[name] = KnownHost(type="fake", name=name, host="127.0.0.1", user="remo")
    return 0


def destroy(name: str, auto_confirm: bool = False, verbose: bool = False) -> int:
    _INSTANCES.pop(name, None)
    return 0


def upgrade(
    name: str,
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> None:
    if name not in _INSTANCES:
        raise PreconditionError(f"No fake instance named '{name}'.")


def resize(
    name: str,
    volume_size: str = "",
    verbose: bool = False,
) -> None:
    if name not in _INSTANCES:
        raise PreconditionError(f"No fake instance named '{name}'.")


def tag(name: str) -> None:
    if name not in _INSTANCES:
        raise PreconditionError(f"No fake instance named '{name}'.")
    _TAGGED.add(name)


def host_prep(host: str = "localhost", flavor: str = "") -> None:
    return None


def list_hosts() -> None:
    return None


def info(name: str) -> int:
    return 0 if name in _INSTANCES else 1


def sync(include_all: bool = False, auto_confirm: bool = False, dry_run: bool = False) -> int:
    return 0


# ---------------------------------------------------------------------------
# Provider Protocol Part A (uniform, entry-based)
# ---------------------------------------------------------------------------


def update_entry(entry: KnownHost, *, verbose: bool = False) -> None:
    upgrade(name=entry.name, verbose=verbose)


def teardown(entry: KnownHost, *, verbose: bool = False, **provider_opts: object) -> None:
    _INSTANCES.pop(entry.name, None)


def probe(scope: SyncScope, **opts: object) -> ProbeResult:
    return ProbeResult(hosts=[], complete=True)


def snapshot_create(entry: KnownHost, snapshot_name: str, *, description: str = "") -> None:
    if entry.name not in _INSTANCES:
        raise OperationFailedError(f"No fake instance named '{entry.name}'.")


def snapshot_restore(entry: KnownHost, snapshot_name: str) -> None:
    if entry.name not in _INSTANCES:
        raise OperationFailedError(f"No fake instance named '{entry.name}'.")


def snapshot_delete(entry: KnownHost, snapshot_name: str) -> None:
    if entry.name not in _INSTANCES:
        raise OperationFailedError(f"No fake instance named '{entry.name}'.")


def snapshot_list(entry: KnownHost) -> list[Snapshot]:
    return []
