"""Notifier configuration: Pydantic models + strict TOML loader.

The config file never contains the bot token; the token is read from a separate
secret file at startup and kept in memory (FR-019). Unknown keys are rejected
(FR-018). See data-model.md and contracts (config-schema.md).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

try:  # Python 3.11+ ships tomllib; tomli is the <3.11 fallback.
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover
    import tomli as _toml  # type: ignore[import-not-found, no-redef]


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=18181, ge=1, le=65535)
    log_level: str = "info"

    @model_validator(mode="after")
    def _check_level(self) -> ServerConfig:
        if self.log_level not in {"debug", "info", "warning", "error"}:
            raise ValueError(
                f"log_level must be one of debug|info|warning|error, got {self.log_level!r}"
            )
        return self


class ApprovalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_timeout_seconds: int = Field(default=300, ge=1)
    max_timeout_seconds: int = Field(default=1800, ge=1)
    max_pending_approvals: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def _check_bounds(self) -> ApprovalConfig:
        if self.max_timeout_seconds < self.default_timeout_seconds:
            raise ValueError(
                "max_timeout_seconds must be >= default_timeout_seconds "
                f"({self.max_timeout_seconds} < {self.default_timeout_seconds})"
            )
        return self


class GrantsConfig(BaseModel):
    """Standing-grant ("Always" auto-approval) settings (Addendum 001)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    default_ttl_seconds: int = Field(default=28800, ge=1)  # 8h; every grant expires
    max_grants: int = Field(default=100, ge=1)
    allow_global_scope: bool = True
    digest_interval_seconds: int = Field(default=3600, ge=0)  # 0 disables the digest


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


class TransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "telegram"
    telegram: TelegramConfig | None = None

    @model_validator(mode="after")
    def _check_transport(self) -> TransportConfig:
        if self.type != "telegram":
            raise ValueError(f"only transport type 'telegram' is supported in v1, got {self.type!r}")
        if self.telegram is None:
            raise ValueError("[transport.telegram] section is required when type = 'telegram'")
        return self


class InstanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str


class NotifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    grants: GrantsConfig = Field(default_factory=GrantsConfig)
    transport: TransportConfig
    instance: InstanceConfig


def load_config(path: str | Path) -> NotifierConfig:
    """Load and strictly validate the notifier TOML config.

    Raises ValueError with a clear message on unknown keys or invalid values
    (FR-018), and FileNotFoundError if the path does not exist.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"config file not found: {path}")
    with p.open("rb") as fh:
        data = _toml.load(fh)
    return NotifierConfig.model_validate(data)
