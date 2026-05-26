"""remo hetzner commands - Manage Hetzner Cloud VMs."""

from __future__ import annotations

import sys

import click

from remo_cli.core.completion import hetzner_name as _complete_name
from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.output import print_error
from remo_cli.core.snapshot import (
    format_snapshot_table,
    generate_default_name,
    validate_name as _validate_snap,
)


@click.group()
def hetzner() -> None:
    """Manage Hetzner Cloud VMs."""


@hetzner.command()
@click.option("--name", default="", help="Server name (default: remo).")
@click.option("--type", "server_type", default="", help="Server type (default: cx22).")
@click.option("--location", default="", help="Location (default: hel1).")
@click.option("--volume-size", default="", help="Volume size in GB (default: 10).")
@click.option("--only", multiple=True, help="Only install these tools.")
@click.option("--skip", multiple=True, help="Skip these tools.")
@click.option(
    "--cadence-days",
    type=int,
    default=None,
    help="Bootstrap-token rotation cadence in days (default 7; 0 disables).",
)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts.")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def create(
    name: str,
    server_type: str,
    location: str,
    volume_size: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    cadence_days: int | None,
    yes: bool,
    verbose: bool,
) -> None:
    """Provision a new Hetzner Cloud VM."""
    from remo_cli.providers.hetzner import create as do_create

    rc = do_create(
        name=name,
        server_type=server_type,
        location=location,
        volume_size=volume_size,
        tools_only=only,
        tools_skip=skip,
        cadence_days=cadence_days,
        verbose=verbose,
    )
    sys.exit(rc)


@hetzner.command()
@click.option("--name", default="", help="Server name (default: remo).", shell_complete=_complete_name)
@click.option("--remove-volume", is_flag=True, help="Also remove persistent volume.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts.")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def destroy(
    name: str,
    remove_volume: bool,
    yes: bool,
    verbose: bool,
) -> None:
    """Tear down a Hetzner Cloud VM."""
    from remo_cli.providers.hetzner import destroy as do_destroy

    rc = do_destroy(
        name=name,
        auto_confirm=yes,
        remove_volume=remove_volume,
        verbose=verbose,
    )
    sys.exit(rc)


@hetzner.command()
@click.option("--name", default="", help="Server name (default: remo).", shell_complete=_complete_name)
@click.option(
    "--volume-size",
    default="",
    help="Grow the persistent Hetzner volume to this size in GB and grow the filesystem in place. Hetzner only supports growing.",
)
@click.option("--only", multiple=True, help="Only install these tools.")
@click.option("--skip", multiple=True, help="Skip these tools.")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def update(
    name: str,
    volume_size: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    verbose: bool,
) -> None:
    """Update dev tools on an existing VM."""
    from remo_cli.providers.hetzner import update as do_update

    rc = do_update(
        name=name,
        volume_size=volume_size,
        tools_only=only,
        tools_skip=skip,
        verbose=verbose,
    )
    sys.exit(rc)


@hetzner.command("list")
def list_cmd() -> None:
    """List registered Hetzner VMs."""
    from remo_cli.providers.hetzner import list_hosts

    list_hosts()


@hetzner.command()
@click.option("--name", default="", help="Server name (default: remo).", shell_complete=_complete_name)
def info(name: str) -> None:
    """Show resource details (type, cores, memory, volume size) for a Hetzner VM."""
    from remo_cli.providers.hetzner import info as do_info

    rc = do_info(name=name)
    sys.exit(rc)


@hetzner.command()
def sync() -> None:
    """Discover VMs and update registry."""
    from remo_cli.providers.hetzner import sync as do_sync

    do_sync()


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _validate_snap_callback(ctx, param, value):  # noqa: ANN001
    if value is None:
        return value
    _validate_snap(value)
    return value


@hetzner.group()
def snapshot() -> None:
    """Create / restore / delete snapshots of Hetzner Cloud VMs."""


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
def snapshot_create_cmd(instance: str, name: str | None, description: str) -> None:
    """Take a snapshot of a Hetzner Cloud VM."""
    from remo_cli.providers.hetzner import snapshot_create

    snap_name = name or generate_default_name()
    rc = snapshot_create(
        server_name=instance, snap_name=snap_name, description=description
    )
    sys.exit(rc)


@snapshot.command("restore")
@click.argument("instance", shell_complete=_complete_name)
@click.argument("snap_name")
@click.option("--yes", "-y", is_flag=True, help="Bypass the confirmation prompt.")
def snapshot_restore_cmd(instance: str, snap_name: str, yes: bool) -> None:
    """Restore a Hetzner Cloud VM by rebuilding from a snapshot image."""
    from remo_cli.providers.hetzner import snapshot_restore

    rc = snapshot_restore(
        server_name=instance, snap_name=snap_name, auto_confirm=yes
    )
    sys.exit(rc)


@snapshot.command("delete")
@click.argument("instance", shell_complete=_complete_name)
@click.argument("snap_name")
@click.option("--yes", "-y", is_flag=True, help="Bypass the confirmation prompt.")
def snapshot_delete_cmd(instance: str, snap_name: str, yes: bool) -> None:
    """Delete a Hetzner snapshot image."""
    from remo_cli.providers.hetzner import snapshot_delete

    rc = snapshot_delete(
        server_name=instance, snap_name=snap_name, auto_confirm=yes
    )
    sys.exit(rc)


@snapshot.command("list")
@click.argument("instance", required=False, default=None, shell_complete=_complete_name)
def snapshot_list_cmd(instance: str | None) -> None:
    """List Hetzner snapshot images for a server (or all registered)."""
    from remo_cli.providers.hetzner import snapshot_list

    if instance is not None:
        try:
            snaps = snapshot_list(server_name=instance)
        except RuntimeError as e:
            print_error(str(e))
            sys.exit(1)
        click.echo(
            format_snapshot_table(snaps, show_status=True, instance_label=instance)
        )
        sys.exit(0)

    all_snaps: list = []
    any_failure = False
    for entry in get_known_hosts(type_filter="hetzner"):
        try:
            all_snaps.extend(snapshot_list(server_name=entry.name))
        except RuntimeError as e:
            print_error(f"{entry.name}: {e}")
            any_failure = True
    click.echo(format_snapshot_table(all_snaps, show_status=True))
    sys.exit(1 if any_failure else 0)
