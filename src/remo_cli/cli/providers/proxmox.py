"""remo proxmox commands - Manage Proxmox VE LXC containers."""

from __future__ import annotations

import sys

import click

from remo_cli.core.completion import proxmox_name as _complete_name
from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.output import print_error
from remo_cli.core.snapshot import (
    format_snapshot_table,
    generate_default_name,
    validate_name as _validate_snap,
)
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
@click.option(
    "--volume-size",
    default="",
    help="Rootfs size in GiB (default: 20). When the container exists and the requested size is larger, the rootfs is grown via `pct resize`.",
)
@click.option(
    "--unprivileged/--privileged",
    default=True,
    help="Run as unprivileged container (default: unprivileged).",
)
@click.option("--domain", default="", help="Domain name for the container.")
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
    node: str,
    bridge: str,
    storage: str,
    template: str,
    cores: int,
    memory: int,
    volume_size: str,
    unprivileged: bool,
    domain: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    use_ip: bool,
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
        volume_size=volume_size,
        unprivileged=unprivileged,
        domain=domain,
        tools_only=only,
        tools_skip=skip,
        use_ip=use_ip,
        verbose=verbose,
    )
    sys.exit(rc)


@proxmox.command()
@click.option("--name", default="dev1", help="Container hostname.", shell_complete=_complete_name)
@click.option("--host", default="", help="Proxmox host (default: auto-detect).")
@click.option("--user", default="", help="SSH user for the Proxmox host.")
@click.option(
    "--purge",
    is_flag=True,
    help="Also remove the container from backup/replication/HA job configs (pct destroy --purge). The rootfs is destroyed regardless.",
)
@click.option("--yes", "-y", is_flag=True, help="Auto-confirm prompts.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output.")
def destroy(
    name: str,
    host: str,
    user: str,
    purge: bool,
    yes: bool,
    verbose: bool,
) -> None:
    """Destroy a Proxmox LXC container."""
    rc = providers_proxmox.destroy(
        name=name,
        host=host,
        user=user,
        purge=purge,
        auto_confirm=yes,
        verbose=verbose,
    )
    sys.exit(rc)


@proxmox.command()
@click.option("--name", default="dev1", help="Container hostname.", shell_complete=_complete_name)
@click.option("--host", default="", help="Proxmox host (default: auto-detect).")
@click.option("--user", default="", help="SSH user for the Proxmox host.")
@click.option(
    "--volume-size",
    default="",
    help="Grow the rootfs to this size in GiB. pct resize only supports growing.",
)
@click.option(
    "--cores",
    default=0,
    type=int,
    help="Set the CPU core count via pct set (live; cgroup v2).",
)
@click.option(
    "--memory",
    default=0,
    type=int,
    help="Set the memory limit in MiB via pct set (live).",
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
    """Update tools on a Proxmox LXC container."""
    rc = providers_proxmox.update(
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


@proxmox.command("list")
def list_cmd() -> None:
    """List registered Proxmox containers."""
    providers_proxmox.list_hosts()


@proxmox.command()
@click.option("--name", default="dev1", help="Container hostname.", shell_complete=_complete_name)
@click.option("--host", default="", help="Proxmox host (default: auto-detect).")
@click.option("--user", default="", help="SSH user for the Proxmox host.")
def info(name: str, host: str, user: str) -> None:
    """Show resource details (cores, memory, rootfs) for a Proxmox container."""
    rc = providers_proxmox.info(name=name, host=host, user=user)
    sys.exit(rc)


@proxmox.command()
@click.option("--host", required=True, help="Proxmox host to scan.")
@click.option("--user", default="", help="SSH user for the Proxmox host.")
@click.option(
    "--use-ip",
    is_flag=True,
    help="Store each container's IP address in known_hosts instead of its name (for setups without DNS/MagicDNS).",
)
def sync(host: str, user: str, use_ip: bool) -> None:
    """Discover containers from a Proxmox host."""
    providers_proxmox.sync(host=host, user=user, use_ip=use_ip)


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


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _validate_snap_callback(ctx, param, value):  # noqa: ANN001
    if value is None:
        return value
    _validate_snap(value)
    return value


def _resolve_proxmox_target(
    instance: str,
) -> tuple[str, str, str] | None:
    """Look up (host, user, vmid) for *instance* in known_hosts.

    Returns ``None`` and prints an error if no matching registry entry is
    found.  The Proxmox registry stores `host/container` in `name`, the
    SSH user in `region`, and the VMID in `instance_id`.
    """
    host, user, vmid = providers_proxmox._lookup_proxmox_host(instance)  # noqa: SLF001
    if not host or not vmid:
        print_error(
            f"No Proxmox registry entry found for '{instance}'. "
            f"Use `remo proxmox sync <host>` to register containers first."
        )
        return None
    if not user:
        user = "root"
    return host, user, vmid


@proxmox.group()
def snapshot() -> None:
    """Create / restore / delete snapshots of Proxmox LXC containers."""


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
    """Take a snapshot of a Proxmox LXC container."""
    target = _resolve_proxmox_target(instance)
    if target is None:
        sys.exit(1)
    host, user, vmid = target
    snap_name = name or generate_default_name()
    rc = providers_proxmox.snapshot_create(
        container=instance,
        host=host,
        user=user,
        vmid=vmid,
        snap_name=snap_name,
        description=description,
    )
    sys.exit(rc)


@snapshot.command("restore")
@click.argument("instance", shell_complete=_complete_name)
@click.argument("snap_name")
@click.option("--yes", "-y", is_flag=True, help="Bypass the confirmation prompt.")
def snapshot_restore_cmd(instance: str, snap_name: str, yes: bool) -> None:
    """Restore a Proxmox LXC container to a previously created snapshot."""
    target = _resolve_proxmox_target(instance)
    if target is None:
        sys.exit(1)
    host, user, vmid = target
    rc = providers_proxmox.snapshot_restore(
        container=instance,
        host=host,
        user=user,
        vmid=vmid,
        snap_name=snap_name,
        auto_confirm=yes,
    )
    sys.exit(rc)


@snapshot.command("delete")
@click.argument("instance", shell_complete=_complete_name)
@click.argument("snap_name")
@click.option("--yes", "-y", is_flag=True, help="Bypass the confirmation prompt.")
def snapshot_delete_cmd(instance: str, snap_name: str, yes: bool) -> None:
    """Delete a snapshot of a Proxmox LXC container."""
    target = _resolve_proxmox_target(instance)
    if target is None:
        sys.exit(1)
    host, user, vmid = target
    rc = providers_proxmox.snapshot_delete(
        container=instance,
        host=host,
        user=user,
        vmid=vmid,
        snap_name=snap_name,
        auto_confirm=yes,
    )
    sys.exit(rc)


@snapshot.command("list")
@click.argument("instance", required=False, default=None, shell_complete=_complete_name)
def snapshot_list_cmd(instance: str | None) -> None:
    """List snapshots for a Proxmox container (or all registered)."""
    if instance is not None:
        target = _resolve_proxmox_target(instance)
        if target is None:
            sys.exit(1)
        host, user, vmid = target
        try:
            snaps = providers_proxmox._list_snapshots_for_vmid(  # noqa: SLF001
                host=host, user=user, vmid=vmid, container_name=instance
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
    for entry in get_known_hosts(type_filter="proxmox"):
        # name format: <host>/<container>
        host, _, container = entry.name.partition("/")
        if not container:
            continue
        user = entry.region or "root"
        vmid = entry.instance_id
        if not vmid:
            continue
        try:
            all_snaps.extend(
                providers_proxmox._list_snapshots_for_vmid(  # noqa: SLF001
                    host=host, user=user, vmid=vmid, container_name=container
                )
            )
        except RuntimeError as e:
            print_error(f"{container}: {e}")
            any_failure = True
    click.echo(format_snapshot_table(all_snaps, show_status=False))
    sys.exit(1 if any_failure else 0)
