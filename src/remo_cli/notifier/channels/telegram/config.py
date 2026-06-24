"""Telegram channel config model (moved from core ``config.py``, spec 008 R3).

The core no longer references this model; the channel owns and validates its
own ``[transport.telegram]`` slice. Fields and ``read_token()`` are byte-for-byte
the spec-007 behavior (FR-017).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_token_file: str = "/run/secrets/telegram_bot_token"
    authorized_chat_id: int
    message_parse_mode: str = "MarkdownV2"

    def read_token(self) -> str:
        """Read and return the bot token from ``bot_token_file``.

        Raises a clear error if the file is missing or empty (fail-fast,
        Constitution IV / FR-023).
        """
        path = Path(self.bot_token_file)
        if not path.is_file():
            raise ValueError(f"bot token file not found: {self.bot_token_file}")
        token = path.read_text(encoding="utf-8").strip()
        if not token:
            raise ValueError(f"bot token file is empty: {self.bot_token_file}")
        return token
