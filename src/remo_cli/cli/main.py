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
    """Run passive update check + overdue-rotation reminder after every command."""
    try:
        from remo_cli.core.version import check_for_updates_passive
        from remo_cli.core.output import print_info, print_warning

        hint = check_for_updates_passive()
        if hint:
            print()
            print_info(hint)

        # 005-credential-broker T083a: passive overdue-rotation reminder.
        from remo_cli.core.broker_revoke import overdue_reminders
        for reminder in overdue_reminders():
            print_warning(reminder)
    except Exception:
        pass


@cli.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion(shell: str) -> None:
    """Print the shell-completion activation script for SHELL.

    Add the output to your shell rc file to enable tab completion. For
    bash and zsh:

        remo completion bash >> ~/.bashrc
        remo completion zsh  >> ~/.zshrc

    For fish:

        remo completion fish > ~/.config/fish/completions/remo.fish
    """
    from click.shell_completion import get_completion_class

    completer_cls = get_completion_class(shell)
    if completer_cls is None:
        raise click.ClickException(f"Unsupported shell: {shell}")

    ctx = click.get_current_context()
    instance = completer_cls(ctx.find_root().command, {}, "remo", "_REMO_COMPLETE")
    source = instance.source()

    if shell == "fish":
        # Click's completion handler emits a bare newline when there are no
        # matches (e.g., `remo upd<TAB>` — no such command). Some fish versions
        # then iterate the for-loop once with an empty $completion, and
        # `string split "," ""` errors out. Guard the loop body so empty
        # candidates are skipped.
        source = source.replace(
            "for completion in $response;\n        set -l metadata",
            "for completion in $response;\n"
            "        if test -z \"$completion\";\n"
            "            continue;\n"
            "        end;\n"
            "        set -l metadata",
        )

    click.echo(source)


def _register_commands() -> None:
    """Register all subcommands and groups. Called at import time."""
    # Import lazily to avoid circular imports and to keep startup fast
    # when only --version or --help is requested.
    from remo_cli.cli.shell import shell  # noqa: F811
    from remo_cli.cli.cp import cp  # noqa: F811
    from remo_cli.cli.init import init_command  # noqa: F811
    from remo_cli.cli.audit import audit_command  # noqa: F811
    from remo_cli.cli.rotate import rotate_command  # noqa: F811
    from remo_cli.cli.providers.incus import incus  # noqa: F811
    from remo_cli.cli.providers.proxmox import proxmox  # noqa: F811
    from remo_cli.cli.providers.hetzner import hetzner  # noqa: F811
    from remo_cli.cli.providers.aws import aws  # noqa: F811

    cli.add_command(shell)
    cli.add_command(cp)
    cli.add_command(init_command)
    cli.add_command(audit_command)
    cli.add_command(rotate_command)
    cli.add_command(incus)
    cli.add_command(proxmox)
    cli.add_command(hetzner)
    cli.add_command(aws)


_register_commands()
