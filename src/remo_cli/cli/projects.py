"""``remo projects`` — manage any host's project list from here (spec 030, R8).

Click wiring only; the work is in ``providers/projects.py``.
"""

from __future__ import annotations

import click

from remo_cli.cli.providers.factory import provider_command


@click.group("projects")
def projects() -> None:
    """Manage the projects on a host: list, add, remove.

    HOST resolves like `remo shell HOST` (including the `local` host). Projects
    are added in place — a folder is never copied or moved.
    """


@projects.command("list")
@click.argument("host")
@provider_command
def list_(host: str) -> None:
    """List HOST's projects."""
    from remo_cli.providers import projects as provider  # noqa: PLC0415

    target = provider.resolve_host(host)
    click.echo(provider.render_projects(provider.list_projects(target), target))


@projects.command("add")
@click.argument("host")
@click.argument("path")
@click.option("--name", default=None, help="Project name (default: the folder name).")
@provider_command
def add(host: str, path: str, name: str | None) -> None:
    """Add an existing folder PATH on HOST as a project, in place.

    PATH is a path on HOST. Quote a leading ~ ('~/code/app') so HOST expands
    it: unquoted, your shell expands it to this machine's home first.
    """
    from remo_cli.providers import projects as provider  # noqa: PLC0415

    provider.add_project(provider.resolve_host(host), path, name)


@projects.command("remove")
@click.argument("host")
@click.argument("name")
@provider_command
def remove(host: str, name: str) -> None:
    """Remove NAME from HOST's project list. The folder is left untouched."""
    from remo_cli.providers import projects as provider  # noqa: PLC0415

    provider.remove_project(provider.resolve_host(host), name)
