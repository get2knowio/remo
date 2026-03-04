"""remo incus commands - Manage Incus containers."""

from __future__ import annotations

import sys

import click

from remo_cli.providers import incus as providers_incus


@click.group()
def incus() -> None:
    """Manage Incus containers (local or remote host)."""


@incus.command()
@click.option("--name", default="dev1", help="Container name (default: dev1).")
@click.option("--host", default="localhost", help="Incus host (default: localhost).")
@click.option("--user", default="", help="SSH user for remote Incus host.")
@click.option("--domain", default="", help="Domain name for the container.")
@click.option("--image", default="", help="Container image to use.")
@click.option("--only", multiple=True, help="Only install these tools.")
@click.option("--skip", multiple=True, help="Skip these tools.")
@click.option("--yes", "-y", is_flag=True, help="Auto-confirm prompts.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
def create(
    name: str,
    host: str,
    user: str,
    domain: str,
    image: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
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
        tools_only=only,
        tools_skip=skip,
        verbose=verbose,
    )
    sys.exit(rc)


@incus.command()
@click.option("--name", default="dev1", help="Container name (default: dev1).")
@click.option("--host", default="", help="Incus host (default: auto-detect).")
@click.option("--user", default="", help="SSH user for remote Incus host.")
@click.option("--remove-storage", is_flag=True, help="Also remove storage volume.")
@click.option("--yes", "-y", is_flag=True, help="Auto-confirm prompts.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
def destroy(
    name: str,
    host: str,
    user: str,
    remove_storage: bool,
    yes: bool,
    verbose: bool,
) -> None:
    """Destroy an Incus container."""
    rc = providers_incus.destroy(
        name=name,
        host=host,
        user=user,
        auto_confirm=yes,
        verbose=verbose,
    )
    sys.exit(rc)


@incus.command()
@click.option("--name", default="dev1", help="Container name (default: dev1).")
@click.option("--host", default="", help="Incus host (default: auto-detect).")
@click.option("--user", default="", help="SSH user for remote Incus host.")
@click.option("--only", multiple=True, help="Only install these tools.")
@click.option("--skip", multiple=True, help="Skip these tools.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
def update(
    name: str,
    host: str,
    user: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    verbose: bool,
) -> None:
    """Update tools on an Incus container."""
    rc = providers_incus.update(
        name=name,
        host=host,
        user=user,
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
@click.option("--host", default="localhost", help="Incus host (default: localhost).")
@click.option("--user", default="", help="SSH user for remote Incus host.")
def sync(host: str, user: str) -> None:
    """Discover containers from an Incus host."""
    providers_incus.sync(host=host, user=user)


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
