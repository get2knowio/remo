"""remo proxmox commands - Manage Proxmox VE LXC containers."""

from __future__ import annotations

import sys

import click

from remo_cli.providers import proxmox as providers_proxmox


@click.group()
def proxmox() -> None:
    """Manage Proxmox VE LXC containers."""


@proxmox.command()
@click.option("--name", default="dev1", help="Container hostname (default: dev1).")
@click.option("--host", required=True, help="Proxmox node SSH host.")
@click.option("--user", default="", help="SSH user for the Proxmox host.")
@click.option("--node", default="", help="Proxmox cluster node name (default: --host).")
@click.option("--bridge", default="", help="Linux bridge to attach to (default: vmbr0).")
@click.option("--storage", default="", help="Rootfs storage (default: local-lvm).")
@click.option("--template", default="", help="LXC template path (storage:vztmpl/<file>).")
@click.option("--cores", default=0, type=int, help="CPU cores (default: 2).")
@click.option("--memory", default=0, type=int, help="RAM in MiB (default: 2048).")
@click.option("--disk", default=0, type=int, help="Rootfs size in GiB (default: 20).")
@click.option(
    "--unprivileged/--privileged",
    default=True,
    help="Run as unprivileged container (default: unprivileged).",
)
@click.option("--domain", default="", help="Domain name for the container.")
@click.option("--only", multiple=True, help="Only install these tools.")
@click.option("--skip", multiple=True, help="Skip these tools.")
@click.option("--yes", "-y", is_flag=True, help="Auto-confirm prompts.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
def create(
    name: str,
    host: str,
    user: str,
    node: str,
    bridge: str,
    storage: str,
    template: str,
    cores: int,
    memory: int,
    disk: int,
    unprivileged: bool,
    domain: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    yes: bool,
    verbose: bool,
) -> None:
    """Create a Proxmox LXC container."""
    rc = providers_proxmox.create(
        name=name,
        host=host,
        user=user,
        node=node,
        bridge=bridge,
        storage=storage,
        template=template,
        cores=cores,
        memory=memory,
        disk=disk,
        unprivileged=unprivileged,
        domain=domain,
        tools_only=only,
        tools_skip=skip,
        verbose=verbose,
    )
    sys.exit(rc)


@proxmox.command()
@click.option("--name", default="dev1", help="Container hostname.")
@click.option("--host", default="", help="Proxmox host (default: auto-detect).")
@click.option("--user", default="", help="SSH user for the Proxmox host.")
@click.option("--remove-storage", is_flag=True, help="Also purge rootfs volume.")
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
    """Destroy a Proxmox LXC container."""
    rc = providers_proxmox.destroy(
        name=name,
        host=host,
        user=user,
        remove_storage=remove_storage,
        auto_confirm=yes,
        verbose=verbose,
    )
    sys.exit(rc)


@proxmox.command()
@click.option("--name", default="dev1", help="Container hostname.")
@click.option("--host", default="", help="Proxmox host (default: auto-detect).")
@click.option("--user", default="", help="SSH user for the Proxmox host.")
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
    """Update tools on a Proxmox LXC container."""
    rc = providers_proxmox.update(
        name=name,
        host=host,
        user=user,
        tools_only=only,
        tools_skip=skip,
        verbose=verbose,
    )
    sys.exit(rc)


@proxmox.command("list")
def list_cmd() -> None:
    """List registered Proxmox containers."""
    providers_proxmox.list_hosts()


@proxmox.command()
@click.option("--host", required=True, help="Proxmox host to scan.")
@click.option("--user", default="", help="SSH user for the Proxmox host.")
def sync(host: str, user: str) -> None:
    """Discover containers from a Proxmox host."""
    providers_proxmox.sync(host=host, user=user)


@proxmox.command()
@click.option("--host", required=True, help="Proxmox host SSH target.")
@click.option("--user", default="", help="SSH user for the Proxmox host.")
@click.option("--bridge", default="", help="Bridge to verify (default: vmbr0).")
@click.option("--storage", default="", help="Storage to verify (default: local-lvm).")
@click.option("--template", default="", help="LXC template to download.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
def bootstrap(
    host: str,
    user: str,
    bridge: str,
    storage: str,
    template: str,
    verbose: bool,
) -> None:
    """Verify a Proxmox node and download the default LXC template."""
    rc = providers_proxmox.bootstrap(
        host=host,
        user=user,
        bridge=bridge,
        storage=storage,
        template=template,
        verbose=verbose,
    )
    sys.exit(rc)
