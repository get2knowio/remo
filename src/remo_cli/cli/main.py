"""Root CLI group for remo."""

from __future__ import annotations

import click

import remo_cli


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(
    version=remo_cli.__version__, prog_name="remo", message="%(prog)s %(version)s"
)
def cli() -> None:
    """Remote development environment CLI."""


@cli.result_callback()
def _post_command_hook(result: object, **kwargs: object) -> None:
    """Run passive update check after every command."""
    try:
        from remo_cli.core.version import check_for_updates_passive
        from remo_cli.core.output import print_info

        hint = check_for_updates_passive()
        if hint:
            print()
            print_info(hint)
    except Exception:
        pass


def _register_commands() -> None:
    """Register all subcommands and groups. Called at import time."""
    # Import lazily to avoid circular imports and to keep startup fast
    # when only --version or --help is requested.
    from remo_cli.cli.shell import shell  # noqa: F811
    from remo_cli.cli.cp import cp  # noqa: F811
    from remo_cli.cli.self_update import self_update  # noqa: F811
    from remo_cli.cli.providers.incus import incus  # noqa: F811
    from remo_cli.cli.providers.hetzner import hetzner  # noqa: F811
    from remo_cli.cli.providers.aws import aws  # noqa: F811

    cli.add_command(shell)
    cli.add_command(cp)
    cli.add_command(self_update, "self-update")
    cli.add_command(incus)
    cli.add_command(hetzner)
    cli.add_command(aws)


_register_commands()
