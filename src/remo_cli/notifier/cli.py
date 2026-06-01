"""`remo-notifier` entry point — runs the notifier service.

Usage: ``remo-notifier serve --config /etc/notifier/notifier.toml``.
"""

from __future__ import annotations

import signal
import sys

import click

from remo_cli.notifier import __version__
from remo_cli.notifier.config import NotifierConfig, load_config
from remo_cli.notifier.logging_setup import configure_logging, get_logger
from remo_cli.notifier.transports.base import NotificationTransport


def build_transport(config: NotifierConfig) -> NotificationTransport:
    """Construct the configured transport (Telegram only in v1)."""
    if config.transport.type != "telegram" or config.transport.telegram is None:
        raise click.ClickException("only the 'telegram' transport is supported")
    # Imported lazily so the rest of the CLI doesn't require python-telegram-bot.
    from remo_cli.notifier.transports.telegram import TelegramTransport

    tg = config.transport.telegram
    return TelegramTransport(
        token=tg.read_token(),
        authorized_chat_id=tg.authorized_chat_id,
        instance_id=config.instance.id,
        parse_mode=tg.message_parse_mode,
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="remo-notifier")
def main() -> None:
    """Remo notifier — Telegram approval bridge for agentsh."""


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
        # Fail fast with a clear message (Constitution IV / FR-018/023).
        click.echo(f"Error: invalid notifier config: {exc}", err=True)
        sys.exit(1)

    configure_logging(config.server.log_level)
    log = get_logger("remo_notifier")

    try:
        transport = build_transport(config)
    except (ValueError, click.ClickException) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    # SIGHUP re-reads the token file so secrets can be rotated without a full
    # redeploy (research R6). Best-effort: the refreshed token applies on the
    # transport's next (re)start.
    def _on_sighup(signum: int, frame: object) -> None:  # noqa: ARG001
        try:
            new_token = config.transport.telegram.read_token()  # type: ignore[union-attr]
            if hasattr(transport, "set_token"):
                transport.set_token(new_token)  # type: ignore[attr-defined]
            log.info("sighup_token_reread")
        except Exception as exc:  # noqa: BLE001
            log.error("sighup_token_reread_failed", error=str(exc))

    try:
        signal.signal(signal.SIGHUP, _on_sighup)
    except (ValueError, AttributeError):  # pragma: no cover - non-main thread / no SIGHUP
        pass

    app = create_app(config, transport)
    log.info(
        "starting",
        version=__version__,
        host=config.server.listen_host,
        port=config.server.listen_port,
    )
    uvicorn.run(
        app,
        host=config.server.listen_host,
        port=config.server.listen_port,
        log_level=config.server.log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
