"""remo local — this machine as a remo host (spec 029). Click wiring only."""

from __future__ import annotations

import click

from remo_cli.cli.providers.factory import provider_command


@click.group("local")
def local() -> None:
    """Use this machine as a remo host — no SSH, no VM.

    Run 'remo local setup' once, add existing checkouts with 'remo local add',
    then work with 'remo shell'. Nothing is installed system-wide and no shell
    startup file is touched.
    """


@local.command("setup")
@click.option("--name", default=None, help="Name for the local host (default: local).")
@click.option(
    "--projects-dir",
    default=None,
    metavar="DIR",
    help="A folder whose subfolders are all projects; '' clears it.",
)
@click.option("--yes", "assume_yes", is_flag=True, default=False, help="Do not ask for confirmation.")
@click.option(
    "--allow-ssh-resume/--no-allow-ssh-resume",
    "ssh_resume",
    default=None,
    help=(
        "Let `remo resume` work when this machine is reached over SSH, by adding "
        "(or, with --no-, removing) an sshd AcceptEnv drop-in. The one step that "
        "uses sudo; default: SSH configuration is not touched."
    ),
)
@click.option("--verbose", "-v", is_flag=True, default=False, help="Stream raw ansible-playbook output.")
@provider_command
def setup(
    name: str | None,
    projects_dir: str | None,
    assume_yes: bool,
    ssh_resume: bool | None,
    verbose: bool,
) -> None:
    """Install remo's host scripts for this machine and register it.

    Detects (never installs) the tools it needs, installs scripts under
    ~/.local/bin and state under ~/.local/state/remo, and registers the one
    local host. Safe to re-run: a second run changes nothing.
    """
    from remo_cli.core.validation import validate_name  # noqa: PLC0415
    from remo_cli.providers import local as provider  # noqa: PLC0415

    if name is not None:
        validate_name(name)
    provider.setup(
        name=name,
        projects_dir=projects_dir,
        assume_yes=assume_yes,
        verbose=verbose,
        ssh_resume=ssh_resume,
    )


@local.command("check")
@provider_command
def check() -> int:
    """Report which prerequisites are present. Read-only."""
    from remo_cli.providers import local as provider  # noqa: PLC0415

    results = provider.check_prerequisites()
    click.echo(provider.render_prerequisites(results))
    click.echo()
    click.echo(provider.local_setup_state())
    return 1 if provider.missing_required(results) else 0


@local.command("add")
@click.argument("path", type=click.Path(file_okay=False))
@click.option("--name", default=None, help="Project name (default: the folder name).")
@provider_command
def add(path: str, name: str | None) -> None:
    """Add an existing checkout in place — it is never copied or moved."""
    from remo_cli.providers import local as provider  # noqa: PLC0415

    provider.add_project(path, name)


@local.command("remove")
@click.argument("name")
@provider_command
def remove(name: str) -> None:
    """Remove NAME from the project list. The folder is left untouched."""
    from remo_cli.providers import local as provider  # noqa: PLC0415

    provider.remove_project(name)


@local.command("list")
@provider_command
def list_() -> None:
    """List this machine's projects."""
    from remo_cli.providers import local as provider  # noqa: PLC0415

    click.echo(provider.render_projects(provider.list_projects()))
