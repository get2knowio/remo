"""remo incus commands - Manage Incus containers."""

from __future__ import annotations

import sys

import click

from remo_cli.core.completion import incus_name as _complete_name
from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.output import print_error
from remo_cli.core.snapshot import (
    format_snapshot_table,
    generate_default_name,
    validate_name as _validate_snap,
)
from remo_cli.providers import incus as providers_incus


@click.group()
def incus() -> None:
    """Manage Incus containers (local or remote host)."""


@incus.command("add-node")
@click.argument("name")
@click.option("--host", required=True, help="SSH-reachable hostname or IP of the Incus node.")
@click.option("--ssh-user", default="incus", help="SSH user on the node (default: incus).")
@click.option(
    "--admin-sa-fnox-key",
    required=True,
    help="fnox key under which this developer's admin SA token is stored.",
)
def add_node_cmd(name: str, host: str, ssh_user: str, admin_sa_fnox_key: str) -> None:
    """Register an Incus node and install the broker token-manager helper.

    Idempotent: re-running with identical fields prints `already registered` and exits 0.
    Conflicting fields exit 6.
    """
    from remo_cli.core import nodes as nodes_mod
    from remo_cli.core.output import print_info, print_success

    try:
        node = providers_incus.add_node(
            name=name,
            host=host,
            ssh_user=ssh_user,
            admin_sa_fnox_key=admin_sa_fnox_key,
        )
    except nodes_mod.NodesError as exc:
        if "already registered" in str(exc):
            print_error(str(exc))
            sys.exit(6)
        print_error(str(exc))
        sys.exit(1)
    except RuntimeError as exc:
        print_error(str(exc))
        sys.exit(1)
    print_info(f"Registered Incus node {name} (host={host}).")
    print_success(f"Wrote ~/.config/remo/nodes.yml entry. admin_sa_fnox_key={admin_sa_fnox_key}.")
    _ = node  # unused — exit message is enough


@incus.command()
@click.option("--name", default="dev1", help="Container name (default: dev1).")
@click.option("--host", default="localhost", help="Incus host (default: localhost).")
@click.option("--user", default="", help="SSH user for remote Incus host.")
@click.option("--domain", default="", help="Domain name for the container.")
@click.option("--image", default="", help="Container image to use.")
@click.option(
    "--volume-size",
    default="",
    help="Root disk size in GiB. When set, an instance-level override of the profile root device is applied (or updated, if it already exists).",
)
@click.option(
    "--cores",
    default=0,
    type=int,
    help="Set the CPU core limit (limits.cpu).",
)
@click.option(
    "--memory",
    default=0,
    type=int,
    help="Set the memory limit in MiB (limits.memory).",
)
@click.option("--only", multiple=True, help="Only install these tools.")
@click.option("--skip", multiple=True, help="Skip these tools.")
@click.option(
    "--use-ip",
    is_flag=True,
    help="Store the container's IP address in known_hosts instead of its name (for setups without DNS/MagicDNS).",
)
@click.option("--yes", "-y", is_flag=True, help="Auto-confirm prompts.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
def create(
    name: str,
    host: str,
    user: str,
    domain: str,
    image: str,
    volume_size: str,
    cores: int,
    memory: int,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    use_ip: bool,
    yes: bool,
    verbose: bool,
) -> None:
    """Create an Incus container."""
    rc = providers_incus.create(
        name=name,
        host=host,
        user=user,
        domain=domain,
        image=image,
        volume_size=volume_size,
        cores=cores,
        memory=memory,
        tools_only=only,
        tools_skip=skip,
        use_ip=use_ip,
        verbose=verbose,
    )
    sys.exit(rc)


@incus.command()
@click.option("--name", default="dev1", help="Container name (default: dev1).", shell_complete=_complete_name)
@click.option("--host", default="", help="Incus host (default: auto-detect).")
@click.option("--user", default="", help="SSH user for remote Incus host.")
@click.option(
    "--remove-storage",
    is_flag=True,
    help="Also remove host mount directories (e.g. /home, /workspace) bound into the container.",
)
@click.option("--yes", "-y", is_flag=True, help="Auto-confirm prompts.")
@click.option(
    "--force-broker",
    is_flag=True,
    default=False,
    help="Proceed with destroy even if broker token revocation fails (FR-020).",
)
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
def destroy(
    name: str,
    host: str,
    user: str,
    remove_storage: bool,
    yes: bool,
    force_broker: bool,
    verbose: bool,
) -> None:
    """Destroy an Incus container."""
    rc = providers_incus.destroy(
        name=name,
        host=host,
        user=user,
        remove_storage=remove_storage,
        auto_confirm=yes,
        verbose=verbose,
        force_broker=force_broker,
    )
    sys.exit(rc)


@incus.command()
@click.option("--name", default="dev1", help="Container name (default: dev1).", shell_complete=_complete_name)
@click.option("--host", default="", help="Incus host (default: auto-detect).")
@click.option("--user", default="", help="SSH user for remote Incus host.")
@click.option(
    "--volume-size",
    default="",
    help="Resize the root disk to this size in GiB. The change may require a container restart depending on the storage backend.",
)
@click.option(
    "--cores",
    default=0,
    type=int,
    help="Set the CPU core limit (limits.cpu).",
)
@click.option(
    "--memory",
    default=0,
    type=int,
    help="Set the memory limit in MiB (limits.memory).",
)
@click.option("--only", multiple=True, help="Only install these tools.")
@click.option("--skip", multiple=True, help="Skip these tools.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
def update(
    name: str,
    host: str,
    user: str,
    volume_size: str,
    cores: int,
    memory: int,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    verbose: bool,
) -> None:
    """Update tools on an Incus container."""
    rc = providers_incus.update(
        name=name,
        host=host,
        user=user,
        volume_size=volume_size,
        cores=cores,
        memory=memory,
        tools_only=only,
        tools_skip=skip,
        verbose=verbose,
    )
    sys.exit(rc)


@incus.command("list")
def list_cmd() -> None:
    """List registered Incus containers."""
    providers_incus.list_hosts()


@incus.command()
@click.option("--name", default="dev1", help="Container name (default: dev1).", shell_complete=_complete_name)
@click.option("--host", default="", help="Incus host (default: auto-detect).")
@click.option("--user", default="", help="SSH user for remote Incus host.")
def info(name: str, host: str, user: str) -> None:
    """Show resource details (cores, memory, root size) for an Incus container."""
    rc = providers_incus.info(name=name, host=host, user=user)
    sys.exit(rc)


@incus.command()
@click.option("--host", default="localhost", help="Incus host (default: localhost).")
@click.option("--user", default="", help="SSH user for remote Incus host.")
@click.option(
    "--use-ip",
    is_flag=True,
    help="Store each container's IP address in known_hosts instead of its name (for setups without DNS/MagicDNS).",
)
def sync(host: str, user: str, use_ip: bool) -> None:
    """Discover containers from an Incus host."""
    providers_incus.sync(host=host, user=user, use_ip=use_ip)


@incus.command()
@click.option("--host", default="localhost", help="Incus host (default: localhost).")
@click.option("--user", default="", help="SSH user for remote Incus host.")
@click.option("--network-type", default="", help="Network type for Incus host.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
def bootstrap(host: str, user: str, network_type: str, verbose: bool) -> None:
    """Initialize an Incus host."""
    rc = providers_incus.bootstrap(
        host=host,
        user=user,
        network_type=network_type,
        verbose=verbose,
    )
    sys.exit(rc)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _validate_snap_callback(ctx, param, value):  # noqa: ANN001 — click signature
    """Click parameter callback that runs snapshot-name validation."""
    if value is None:
        return value
    _validate_snap(value)
    return value


@incus.group()
def snapshot() -> None:
    """Create / restore / delete snapshots of Incus containers."""


@snapshot.command("create")
@click.argument("instance", shell_complete=_complete_name)
@click.option(
    "--name",
    default=None,
    callback=_validate_snap_callback,
    help="Snapshot name (default: remo-YYYYMMDD-HHMMSS).",
)
@click.option(
    "--description",
    default="",
    help="Free-text description shown in `snapshot list`.",
)
def snapshot_create_cmd(
    instance: str,
    name: str | None,
    description: str,
) -> None:
    """Take a snapshot of an Incus container."""
    snap_name = name or generate_default_name()
    host, user = providers_incus._lookup_incus_host(instance)  # noqa: SLF001
    rc = providers_incus.snapshot_create(
        container=instance,
        host=host,
        user=user,
        snap_name=snap_name,
        description=description,
    )
    sys.exit(rc)


@snapshot.command("restore")
@click.argument("instance", shell_complete=_complete_name)
@click.argument("snap_name")
@click.option("--yes", "-y", is_flag=True, help="Bypass the confirmation prompt.")
def snapshot_restore_cmd(instance: str, snap_name: str, yes: bool) -> None:
    """Restore an Incus container to a previously created snapshot."""
    host, user = providers_incus._lookup_incus_host(instance)  # noqa: SLF001
    rc = providers_incus.snapshot_restore(
        container=instance,
        host=host,
        user=user,
        snap_name=snap_name,
        auto_confirm=yes,
    )
    sys.exit(rc)


@snapshot.command("delete")
@click.argument("instance", shell_complete=_complete_name)
@click.argument("snap_name")
@click.option("--yes", "-y", is_flag=True, help="Bypass the confirmation prompt.")
def snapshot_delete_cmd(instance: str, snap_name: str, yes: bool) -> None:
    """Delete a snapshot of an Incus container."""
    host, user = providers_incus._lookup_incus_host(instance)  # noqa: SLF001
    rc = providers_incus.snapshot_delete(
        container=instance,
        host=host,
        user=user,
        snap_name=snap_name,
        auto_confirm=yes,
    )
    sys.exit(rc)


@snapshot.command("list")
@click.argument("instance", required=False, default=None, shell_complete=_complete_name)
def snapshot_list_cmd(instance: str | None) -> None:
    """List snapshots for a container (or all registered Incus containers)."""
    if instance is not None:
        host, user = providers_incus._lookup_incus_host(instance)  # noqa: SLF001
        try:
            snaps = providers_incus._list_snapshots_for_container(  # noqa: SLF001
                host=host, container=instance, user=user
            )
        except RuntimeError as e:
            print_error(str(e))
            sys.exit(1)
        click.echo(
            format_snapshot_table(snaps, show_status=False, instance_label=instance)
        )
        sys.exit(0)

    all_snaps: list = []
    any_failure = False
    for entry in get_known_hosts(type_filter="incus"):
        container = entry.name.split("/", maxsplit=1)[-1] if "/" in entry.name else entry.name
        host, user = providers_incus._lookup_incus_host(container)  # noqa: SLF001
        try:
            all_snaps.extend(
                providers_incus._list_snapshots_for_container(  # noqa: SLF001
                    host=host, container=container, user=user
                )
            )
        except RuntimeError as e:
            print_error(f"{container}: {e}")
            any_failure = True
    click.echo(format_snapshot_table(all_snaps, show_status=False))
    sys.exit(1 if any_failure else 0)
