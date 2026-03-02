"""remo self-update command - Update remo to a newer version."""

from __future__ import annotations

import click


@click.command("self-update")
@click.option("--version", "target_version", default=None, help="Specific version to install.")
@click.option("--pre-release", is_flag=True, default=False, help="Include pre-release versions.")
@click.option("--check", "check_only", is_flag=True, default=False, help="Only check for updates, don't install.")
def self_update(
    target_version: str | None,
    pre_release: bool,
    check_only: bool,
) -> None:
    """Update remo to a newer version via PyPI."""
    from remo_cli.core.version import handle_self_update

    handle_self_update(
        version=target_version,
        check_only=check_only,
        pre_release=pre_release,
    )
