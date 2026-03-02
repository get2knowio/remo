"""remo init command - Initialize remo environment."""

from __future__ import annotations

import click


@click.command("init")
@click.option("--force", is_flag=True, default=False, help="Remove and recreate the virtual environment.")
def init(force: bool) -> None:
    """Initialize remo (install dependencies)."""
    from remo_cli.core.init import handle_init

    handle_init(force=force)
