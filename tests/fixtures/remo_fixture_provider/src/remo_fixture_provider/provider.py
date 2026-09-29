"""Fixture provider implementation — the smallest module that satisfies
``remo_cli.core.provider_protocol.Provider`` plus the generated verbs, and
therefore remo's own provider conformance suite
(tests/unit/providers/test_provider_conformance.py), which checks that every
verb's signature matches exactly the parameters the generated CLI passes.

Nothing here touches a real system: ``create`` records a registry entry,
``list_hosts``/``info`` read the registry, everything else is a no-op or
raises a typed ``core/errors`` exception (never ``sys.exit``).
"""

from __future__ import annotations

from remo_cli.core.errors import OperationFailedError
from remo_cli.core.known_hosts import get_known_hosts, save_known_host
from remo_cli.core.reconcile import ProbeResult, SyncScope
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot

TYPE_NAME = "fixture"


# --- generated verbs (signatures mirror cli/providers/factory.py exactly) ----


def create(
    name: str,
    volume_size: str,
    tools_only: tuple[str, ...],
    tools_skip: tuple[str, ...],
    region: str,
    verbose: bool,
) -> None:
    entry = KnownHost(
        type=TYPE_NAME,
        name=name or "fixture1",
        host=f"{name or 'fixture1'}.fixture.invalid",
        user="remo",
        instance_id="box-0001",
        access_mode="direct",
        region=region or "zone-a",
    )
    save_known_host(entry)


def upgrade(name: str, tools_only: tuple[str, ...], tools_skip: tuple[str, ...], verbose: bool) -> None:
    return None


def resize(name: str, volume_size: str, verbose: bool) -> None:
    return None


def list_hosts() -> None:
    for host in get_known_hosts(type_filter=TYPE_NAME):
        print(f"{host.name}\t{host.host}\t{host.region}")


def info(name: str) -> None:
    for host in get_known_hosts(type_filter=TYPE_NAME):
        if host.name == name:
            print(f"{host.name}: {host.host} ({host.instance_id}, {host.region})")
            return
    raise OperationFailedError(f"no fixture instance named {name!r}")


def sync(include_all: bool, auto_confirm: bool, dry_run: bool) -> int:
    return 0


# --- Provider Protocol -------------------------------------------------------


def update_entry(entry: KnownHost, *, verbose: bool = False) -> None:
    return None


def teardown(entry: KnownHost, *, verbose: bool = False, **provider_opts: object) -> None:
    return None


def probe(scope: SyncScope, **opts: object) -> ProbeResult:
    return ProbeResult(hosts=[], complete=True)


def snapshot_create(entry: KnownHost, name: str | None = None, description: str = "") -> Snapshot:
    raise OperationFailedError("the fixture provider has no snapshots")


def snapshot_restore(entry: KnownHost, snapshot_name: str) -> None:
    raise OperationFailedError("the fixture provider has no snapshots")


def snapshot_delete(entry: KnownHost, snapshot_name: str) -> None:
    raise OperationFailedError("the fixture provider has no snapshots")


def snapshot_list(entry: KnownHost) -> list[Snapshot]:
    return []
