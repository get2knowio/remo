"""remo aws commands - Manage AWS EC2 instances."""

from __future__ import annotations

import sys

import click

from remo_cli.core.completion import aws_name as _complete_name
from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.output import print_error
from remo_cli.core.snapshot import (
    format_snapshot_table,
    generate_default_name,
    validate_name as _validate_snap,
)


@click.group()
def aws() -> None:
    """Manage AWS EC2 instances with EBS storage."""


@aws.command()
@click.option("--name", default="", help="Instance name (defaults to $USER).")
@click.option("--type", "instance_type", default="", help="EC2 instance type.")
@click.option("--region", default="", help="AWS region.")
@click.option("--volume-size", default="", help="EBS volume size in GB.")
@click.option("--spot", is_flag=True, default=False, help="Use spot instance.")
@click.option("--iam-profile", default="", help="IAM instance profile name.")
@click.option("--only", multiple=True, help="Only configure these tools.")
@click.option("--skip", multiple=True, help="Skip configuring these tools.")
@click.option("--yes", "-y", "auto_confirm", is_flag=True, default=False, help="Skip confirmation prompts.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Verbose output.")
def create(
    name: str,
    instance_type: str,
    region: str,
    volume_size: str,
    spot: bool,
    iam_profile: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    auto_confirm: bool,
    verbose: bool,
) -> None:
    """Create a new AWS EC2 instance."""
    from remo_cli.providers.aws import create as aws_create

    rc = aws_create(
        name=name,
        instance_type=instance_type,
        region=region,
        volume_size=volume_size,
        use_spot=spot,
        iam_profile=iam_profile,
        tools_only=only,
        tools_skip=skip,
        verbose=verbose,
    )
    sys.exit(rc)


@aws.command()
@click.option("--name", default="", help="Instance name (defaults to $USER).", shell_complete=_complete_name)
@click.option("--remove-storage", is_flag=True, default=False, help="Also remove EBS storage volume.")
@click.option("--yes", "-y", "auto_confirm", is_flag=True, default=False, help="Skip confirmation prompts.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Verbose output.")
def destroy(
    name: str,
    remove_storage: bool,
    auto_confirm: bool,
    verbose: bool,
) -> None:
    """Destroy an AWS EC2 instance."""
    from remo_cli.providers.aws import destroy as aws_destroy

    rc = aws_destroy(
        name=name,
        auto_confirm=auto_confirm,
        remove_storage=remove_storage,
        verbose=verbose,
    )
    sys.exit(rc)


@aws.command()
@click.option("--name", default="", help="Instance name (defaults to $USER).", shell_complete=_complete_name)
@click.option(
    "--volume-size",
    default="",
    help="Grow the persistent EBS volume to this size in GB and grow the filesystem in place. AWS only supports growing.",
)
@click.option("--only", multiple=True, help="Only configure these tools.")
@click.option("--skip", multiple=True, help="Skip configuring these tools.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Verbose output.")
def update(
    name: str,
    volume_size: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    verbose: bool,
) -> None:
    """Re-configure dev tools on an existing AWS instance."""
    from remo_cli.providers.aws import update as aws_update

    rc = aws_update(
        name=name,
        volume_size=volume_size,
        tools_only=only,
        tools_skip=skip,
        verbose=verbose,
    )
    sys.exit(rc)


@aws.command("list")
def list_cmd() -> None:
    """List registered AWS instances."""
    from remo_cli.providers.aws import list_hosts

    list_hosts()


@aws.command()
@click.option("--region", default="", help="AWS region to sync.")
def sync(region: str) -> None:
    """Sync local registry with running AWS instances."""
    from remo_cli.providers.aws import sync as aws_sync

    aws_sync(region=region)


@aws.command()
@click.option("--name", default="", help="Instance name (defaults to $USER).", shell_complete=_complete_name)
@click.option("--yes", "-y", "auto_confirm", is_flag=True, default=False, help="Skip confirmation prompts.")
def stop(name: str, auto_confirm: bool) -> None:
    """Stop an AWS EC2 instance."""
    from remo_cli.providers.aws import stop as aws_stop

    aws_stop(name=name, auto_confirm=auto_confirm)


@aws.command()
@click.option("--name", default="", help="Instance name (defaults to $USER).", shell_complete=_complete_name)
def start(name: str) -> None:
    """Start a stopped AWS EC2 instance."""
    from remo_cli.providers.aws import start as aws_start

    aws_start(name=name)


@aws.command()
@click.option("--name", default="", help="Instance name (defaults to $USER).", shell_complete=_complete_name)
@click.option("--yes", "-y", "auto_confirm", is_flag=True, default=False, help="Skip confirmation prompts.")
def reboot(name: str, auto_confirm: bool) -> None:
    """Reboot an AWS EC2 instance."""
    from remo_cli.providers.aws import reboot as aws_reboot

    aws_reboot(name=name, auto_confirm=auto_confirm)


@aws.command()
@click.option("--name", default="", help="Instance name (defaults to $USER).", shell_complete=_complete_name)
def info(name: str) -> None:
    """Show detailed info about an AWS EC2 instance."""
    from remo_cli.providers.aws import info as aws_info

    aws_info(name=name)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _validate_snap_callback(ctx, param, value):  # noqa: ANN001
    if value is None:
        return value
    _validate_snap(value)
    return value


@aws.group()
def snapshot() -> None:
    """Create / restore / delete snapshots of AWS EC2 instances."""


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
@click.option("--region", default="", help="AWS region (defaults to instance's region).")
def snapshot_create_cmd(
    instance: str,
    name: str | None,
    description: str,
    region: str,
) -> None:
    """Take an EBS snapshot of an AWS instance's root volume."""
    from remo_cli.providers.aws import snapshot_create

    snap_name = name or generate_default_name()
    rc = snapshot_create(
        instance_name=instance,
        snap_name=snap_name,
        description=description,
        region=region,
    )
    sys.exit(rc)


@snapshot.command("restore")
@click.argument("instance", shell_complete=_complete_name)
@click.argument("snap_name")
@click.option("--yes", "-y", is_flag=True, help="Bypass the confirmation prompt.")
@click.option("--region", default="", help="AWS region (defaults to instance's region).")
def snapshot_restore_cmd(
    instance: str, snap_name: str, yes: bool, region: str
) -> None:
    """Restore an AWS instance via in-place EBS volume swap."""
    from remo_cli.providers.aws import snapshot_restore

    rc = snapshot_restore(
        instance_name=instance,
        snap_name=snap_name,
        region=region,
        auto_confirm=yes,
    )
    sys.exit(rc)


@snapshot.command("delete")
@click.argument("instance", shell_complete=_complete_name)
@click.argument("snap_name")
@click.option("--yes", "-y", is_flag=True, help="Bypass the confirmation prompt.")
@click.option("--region", default="", help="AWS region (defaults to instance's region).")
def snapshot_delete_cmd(instance: str, snap_name: str, yes: bool, region: str) -> None:
    """Delete an EBS snapshot from an AWS instance's root volume."""
    from remo_cli.providers.aws import snapshot_delete

    rc = snapshot_delete(
        instance_name=instance,
        snap_name=snap_name,
        region=region,
        auto_confirm=yes,
    )
    sys.exit(rc)


@snapshot.command("list")
@click.argument("instance", required=False, default=None, shell_complete=_complete_name)
@click.option("--region", default="", help="AWS region (defaults to instance's region).")
def snapshot_list_cmd(instance: str | None, region: str) -> None:
    """List EBS snapshots for an AWS instance (or all registered)."""
    from remo_cli.providers.aws import snapshot_list

    if instance is not None:
        try:
            snaps = snapshot_list(instance_name=instance, region=region)
        except RuntimeError as e:
            print_error(str(e))
            sys.exit(1)
        click.echo(
            format_snapshot_table(snaps, show_status=True, instance_label=instance)
        )
        sys.exit(0)

    all_snaps: list = []
    any_failure = False
    for entry in get_known_hosts(type_filter="aws"):
        try:
            all_snaps.extend(snapshot_list(instance_name=entry.name, region=region or entry.region))
        except RuntimeError as e:
            print_error(f"{entry.name}: {e}")
            any_failure = True
    click.echo(format_snapshot_table(all_snaps, show_status=True))
    sys.exit(1 if any_failure else 0)
