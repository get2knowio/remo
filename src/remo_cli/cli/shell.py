"""remo shell command - Connect to a remote environment."""

from __future__ import annotations

import click


@click.command()
@click.option(
    "-L",
    "tunnels",
    multiple=True,
    help="Forward port: PORT or LOCAL:REMOTE",
)
@click.option(
    "--no-open",
    is_flag=True,
    default=False,
    help="Skip auto-opening browser for tunneled ports",
)
def shell(tunnels: tuple[str, ...], no_open: bool) -> None:
    """Connect to a remo environment (auto-detects or picker)."""
    from remo_cli.core.ssh import resolve_remo_host, shell_connect  # noqa: PLC0415
    from remo_cli.providers.aws import auto_start_aws_if_stopped  # noqa: PLC0415

    host = resolve_remo_host()

    # Auto-start stopped AWS instances before connecting
    host = auto_start_aws_if_stopped(host)

    shell_connect(host, list(tunnels), no_open)
