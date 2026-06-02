"""`remo-notifier` entry point — runs the notifier service.

Usage: ``remo-notifier serve --config /etc/notifier/notifier.toml``. This runs
inside the channel image, so it may lazily import channel packages (resolved via
the catalog by ``config.transport.type``).
"""

from __future__ import annotations

import signal
import sys

import click

from remo_cli.notifier import __version__
from remo_cli.notifier.agentsh_client import AgentshClient
from remo_cli.notifier.config import NotifierConfig, load_config
from remo_cli.notifier.logging_setup import configure_logging, get_logger
from remo_cli.notifier.transports.base import NotificationTransport


def build_transport(config: NotifierConfig) -> NotificationTransport:
    """Resolve the configured channel via the catalog and build its transport.

    The channel's transport factory is imported lazily (only here, in-container),
    keeping the catalog/laptop CLI free of channel delivery deps (FR-019).
    """
    from remo_cli.notifier.channels.catalog import get

    descriptor = get(config.transport.type)
    if descriptor is None:
        from remo_cli.notifier.channels.catalog import list_channels

        available = ", ".join(d.id for d in list_channels())
        raise click.ClickException(
            f"unknown channel '{config.transport.type}'; available: {available}"
        )
    factory = descriptor.load_transport_factory()
    return factory(config)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="remo-notifier")
def main() -> None:
    """Remo notifier — channel-based approval bridge for agentsh."""


@main.command()
@click.option(
    "--config",
    "config_path",
    default="/etc/notifier/notifier.toml",
    help="Path to the notifier TOML config.",
)
def serve(config_path: str) -> None:
    """Start the notifier HTTP server."""
    import uvicorn

    from remo_cli.notifier.server import create_app

    try:
        config = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        # Fail fast with a clear message (Constitution IV / FR-018).
        click.echo(f"Error: invalid notifier config: {exc}", err=True)
        sys.exit(1)

    configure_logging(config.server.log_level)
    log = get_logger("remo_notifier")

    try:
        transport = build_transport(config)
        api_key = config.agentsh.read_api_key()
    except (ValueError, click.ClickException) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    agentsh = AgentshClient(api_url=config.agentsh.api_url, api_key=api_key)

    # SIGHUP re-reads the channel secret so credentials can be rotated without a
    # full redeploy (research R6). Generic: any transport may expose
    # ``reread_secret()``; channels that don't, skip.
    def _on_sighup(signum: int, frame: object) -> None:  # noqa: ARG001
        try:
            if hasattr(transport, "reread_secret"):
                transport.reread_secret()  # type: ignore[attr-defined]
            log.info("sighup_secret_reread")
        except Exception as exc:  # noqa: BLE001
            log.error("sighup_secret_reread_failed", error=str(exc))

    try:
        signal.signal(signal.SIGHUP, _on_sighup)
    except (ValueError, AttributeError):  # pragma: no cover - non-main thread / no SIGHUP
        pass

    app = create_app(config, transport, agentsh)
    log.info(
        "starting",
        version=__version__,
        host=config.server.listen_host,
        port=config.server.listen_port,
        channel=config.transport.type,
    )
    uvicorn.run(
        app,
        host=config.server.listen_host,
        port=config.server.listen_port,
        log_level=config.server.log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
