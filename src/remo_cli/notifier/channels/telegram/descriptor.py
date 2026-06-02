"""Telegram ``ChannelDescriptor`` (spec 008, contracts/channel-descriptor.md).

Import-light: this module imports only ``channels.base`` — no FastAPI/telegram.
The rendered ``[transport.telegram]`` TOML is byte-identical to spec 007.
"""

from __future__ import annotations

from remo_cli.notifier.channels.base import ChannelDescriptor, RequiredEnv

# In-container path the bot-token secret is mounted to. Owned here (the channel)
# and referenced by both the rendered TOML and the descriptor's secret_mount so
# the Ansible role stays channel-agnostic. Byte-identical to spec 007.
_SECRET_MOUNT = "/run/secrets/telegram_bot_token"


def _render_transport_toml(values: dict[str, str]) -> str:
    """Render the ``[transport]`` + ``[transport.telegram]`` fragment.

    ``values`` carries the non-secret env values (here, the chat id). The bot
    token is a secret written to the secret file and never appears here.
    """
    chat_id = values["REMO_NOTIFIER_TELEGRAM_CHAT_ID"]
    parse_mode = values.get("REMO_NOTIFIER_TELEGRAM_PARSE_MODE", "MarkdownV2")
    return (
        '[transport]\n'
        'type = "telegram"\n'
        "\n"
        "[transport.telegram]\n"
        f'bot_token_file = "{_SECRET_MOUNT}"\n'
        f"authorized_chat_id = {int(chat_id)}\n"
        f'message_parse_mode = "{parse_mode}"\n'
    )


TELEGRAM = ChannelDescriptor(
    id="telegram",
    label="Telegram",
    image_name="remo-notifier-telegram",
    required_env=[
        RequiredEnv(
            name="REMO_NOTIFIER_TELEGRAM_BOT_TOKEN",
            secret=True,
            purpose="Bot API token from @BotFather",
        ),
        RequiredEnv(
            name="REMO_NOTIFIER_TELEGRAM_CHAT_ID",
            secret=False,
            purpose="Authorized chat id that may approve",
        ),
    ],
    transport_factory="remo_cli.notifier.channels.telegram.transport:build",
    render_transport_toml=_render_transport_toml,
    secret_mount=_SECRET_MOUNT,
)
