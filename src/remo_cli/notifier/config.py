"""Notifier configuration: Pydantic models + strict TOML loader.

Secrets (the channel token, the agentsh approver key) never appear in the config
file; they are read from separate secret files at startup. Unknown keys are
rejected (FR-018) except inside the dynamic ``[transport.<channel>]`` sub-table,
which the active channel validates strictly via its own model (spec 008 R3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


class TransportConfig(BaseModel):
    """Channel-agnostic transport selector.

    ``type`` names the active channel; the matching ``[transport.<type>]``
    sub-table is captured as an extra field and validated by that channel's own
    Pydantic model (the core never imports a channel model). Each channel's
    sub-table shape is owned by that channel and preserved verbatim
    (FR-017/FR-018).
    """

    model_config = ConfigDict(extra="allow")

    type: str

    @model_validator(mode="after")
    def _check_subtable(self) -> TransportConfig:
        extra = self.__pydantic_extra__ or {}
        sub = extra.get(self.type)
        if not isinstance(sub, dict):
            raise ValueError(
                f"[transport.{self.type}] section is required when type = {self.type!r}"
            )
        return self

    def settings(self) -> dict[str, Any]:
        """Return the active channel's raw settings sub-mapping."""
        extra = self.__pydantic_extra__ or {}
        sub = extra.get(self.type)
        if not isinstance(sub, dict):
            raise ValueError(f"[transport.{self.type}] section is missing")
        return dict(sub)


class SourcesConfig(BaseModel):
    """Dynamic source-registry settings (``[sources]``, spec 009 R5).

    Bounds the in-memory registry and the per-source presence/poll behaviour.
    All values are operator-tunable and validated fail-fast (Constitution IV).
    """

    model_config = ConfigDict(extra="forbid")

    max_sources: int = Field(default=64, ge=1)
    keepalive_interval_seconds: int = Field(default=15, ge=1)
    idle_timeout_seconds: int = Field(default=45, ge=1)
    poll_base_interval_seconds: int = Field(default=5, ge=1)
    poll_backoff_factor: float = Field(default=2.0, ge=1.0)
    poll_backoff_cap_seconds: int = Field(default=300, ge=1)
    poll_backoff_jitter: float = Field(default=0.2, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_bounds(self) -> SourcesConfig:
        if self.idle_timeout_seconds <= self.keepalive_interval_seconds:
            raise ValueError(
                "idle_timeout_seconds must be > keepalive_interval_seconds "
                f"({self.idle_timeout_seconds} <= {self.keepalive_interval_seconds})"
            )
        if self.poll_backoff_cap_seconds < self.poll_base_interval_seconds:
            raise ValueError(
                "poll_backoff_cap_seconds must be >= poll_base_interval_seconds "
                f"({self.poll_backoff_cap_seconds} < {self.poll_base_interval_seconds})"
            )
        return self


class AgentshConfig(BaseModel):
    """Connection to agentsh's approval REST API (spec 008, FR-020).

    Optional in 009: when present it seeds one permanent ``seed`` source; when
    absent the registry starts empty and serves only dynamic sources (R7).
    """

    model_config = ConfigDict(extra="forbid")

    api_url: str
    api_key_file: str = "/run/secrets/agentsh_api_key"
    poll_interval_seconds: int = Field(default=5, ge=1)
    webhook_enabled: bool = False
    source_id: str = "seed"

    def read_api_key(self) -> str:
        """Read the approver ``X-API-Key`` from ``api_key_file`` (fail-fast)."""
        path = Path(self.api_key_file)
        if not path.is_file():
            raise ValueError(f"agentsh api key file not found: {self.api_key_file}")
        key = path.read_text(encoding="utf-8").strip()
        if not key:
            raise ValueError(f"agentsh api key file is empty: {self.api_key_file}")
        return key


class InstanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str


class NotifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: ServerConfig = Field(default_factory=ServerConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    grants: GrantsConfig = Field(default_factory=GrantsConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    transport: TransportConfig
    agentsh: AgentshConfig | None = None
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
