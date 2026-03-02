"""remo hetzner commands - Manage Hetzner Cloud VMs."""

from __future__ import annotations

import sys

import click


@click.group()
def hetzner() -> None:
    """Manage Hetzner Cloud VMs."""


@hetzner.command()
@click.option("--name", default="", help="Server name (default: remote-coding-server).")
@click.option("--type", "server_type", default="", help="Server type (default: cx22).")
@click.option("--location", default="", help="Location (default: hel1).")
@click.option("--volume-size", default="", help="Volume size in GB (default: 10).")
@click.option("--only", multiple=True, help="Only install these tools.")
@click.option("--skip", multiple=True, help="Skip these tools.")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompts.")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def create(
    name: str,
    server_type: str,
    location: str,
    volume_size: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
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
        verbose=verbose,
    )
    sys.exit(rc)


@hetzner.command()
@click.option("--name", default="", help="Server name (default: remote-coding-server).")
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
@click.option("--name", default="", help="Server name (default: remote-coding-server).")
@click.option("--only", multiple=True, help="Only install these tools.")
@click.option("--skip", multiple=True, help="Skip these tools.")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def update(
    name: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    verbose: bool,
) -> None:
    """Update dev tools on an existing VM."""
    from remo_cli.providers.hetzner import update as do_update

    rc = do_update(
        name=name,
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
def sync() -> None:
    """Discover VMs and update registry."""
    from remo_cli.providers.hetzner import sync as do_sync

    do_sync()
