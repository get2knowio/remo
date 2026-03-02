"""remo aws commands - Manage AWS EC2 instances."""

from __future__ import annotations

import sys

import click


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
@click.option("--name", default="", help="Instance name (defaults to $USER).")
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
@click.option("--name", default="", help="Instance name (defaults to $USER).")
@click.option("--only", multiple=True, help="Only configure these tools.")
@click.option("--skip", multiple=True, help="Skip configuring these tools.")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Verbose output.")
def update(
    name: str,
    only: tuple[str, ...],
    skip: tuple[str, ...],
    verbose: bool,
) -> None:
    """Re-configure dev tools on an existing AWS instance."""
    from remo_cli.providers.aws import update as aws_update

    rc = aws_update(
        name=name,
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
@click.option("--name", default="", help="Instance name (defaults to $USER).")
@click.option("--yes", "-y", "auto_confirm", is_flag=True, default=False, help="Skip confirmation prompts.")
def stop(name: str, auto_confirm: bool) -> None:
    """Stop an AWS EC2 instance."""
    from remo_cli.providers.aws import stop as aws_stop

    aws_stop(name=name, auto_confirm=auto_confirm)


@aws.command()
@click.option("--name", default="", help="Instance name (defaults to $USER).")
def start(name: str) -> None:
    """Start a stopped AWS EC2 instance."""
    from remo_cli.providers.aws import start as aws_start

    aws_start(name=name)


@aws.command()
@click.option("--name", default="", help="Instance name (defaults to $USER).")
@click.option("--yes", "-y", "auto_confirm", is_flag=True, default=False, help="Skip confirmation prompts.")
def reboot(name: str, auto_confirm: bool) -> None:
    """Reboot an AWS EC2 instance."""
    from remo_cli.providers.aws import reboot as aws_reboot

    aws_reboot(name=name, auto_confirm=auto_confirm)


@aws.command()
@click.option("--name", default="", help="Instance name (defaults to $USER).")
def info(name: str) -> None:
    """Show detailed info about an AWS EC2 instance."""
    from remo_cli.providers.aws import info as aws_info

    aws_info(name=name)
