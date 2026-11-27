"""``remo self-update`` — Click wiring only; the work is in providers/self_update.py."""

from __future__ import annotations

import click

from remo_cli.cli.providers.factory import provider_command


@click.command("self-update")
@click.option("--check", is_flag=True, help="Report what would change; install nothing.")
@click.option("--yes", "-y", "assume_yes", is_flag=True, help="Do not ask before installing.")
@provider_command
def self_update(check: bool, assume_yes: bool) -> None:
    """Update remo to the newest release on its install channel.

    Re-runs the installer with the set of plugins and extras recorded when remo
    was installed.
    """
    from remo_cli.providers import self_update as provider  # noqa: PLC0415

    provider.run_self_update(check=check, assume_yes=assume_yes)
