"""Pydantic v2 models for the notifier.

The approval object is **agentsh's** ``Request`` (consumed, not defined here):
the notifier polls ``GET /api/v1/approvals`` and resolves via
``POST /api/v1/approvals/{id}``. See specs/008-notifier-channels and
contracts/agentsh-integration.md (verified against agentsh source 2026-06-01).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Decision(str, Enum):
    allow = "allow"
    deny = "deny"


class AgentshRequest(BaseModel):
    """A pending approval fetched from agentsh's ``GET /api/v1/approvals``.

    Mirrors ``internal/approvals/manager.go`` ``Request`` (verified 2026-06-01).
    agentsh owns this schema; we tolerate unknown fields (``extra="ignore"``) so
    a future agentsh field never blocks delivery — the channel renders the
    fields it knows.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    created_at: datetime | None = None
    expires_at: datetime | None = None
    session_id: str = ""
    command_id: str = ""
    kind: str = ""
    target: str = ""
    rule: str = ""
    message: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    """Internal resolution value carried by a pending approval's Future.

    Mapped to agentsh's wire vocabulary at resolve time: ``allow`` -> ``approve``;
    every other terminal state -> ``deny`` (FR-007/FR-008).
    """

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    responder: str
    reason: str = ""
    decided_at: datetime = Field(default_factory=_utcnow)
    # Set when a human chose "Always" — the newly created grant's id (Addendum 001).
    grant_id: str | None = None


class ApprovalResponse(BaseModel):
    """Internal/observability record of a resolved approval.

    Returned by the local ``test`` injection path and used in structured logs.
    """

    approval_id: str
    decision: Decision
    responder: str
    reason: str = ""
    decided_at: datetime
    latency_ms: int = Field(ge=0)
    grant_id: str | None = None


class SourceRegistration(BaseModel):
    """``POST /v1/sources`` request body — a source's presence registration.

    Trust-boundary input from a co-located (unauthenticated) source; validated
    strictly (``extra="forbid"``). ``api_key`` is held in-memory only — never
    logged, never persisted, never echoed in any response (spec 009 data-model).
    """

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")
    api_url: str
    api_key: str = Field(min_length=1)
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("api_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("api_url must be an http(s) URL")
        return v

    @field_validator("labels")
    @classmethod
    def _bound_labels(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 16:
            raise ValueError("labels may have at most 16 entries")
        return v


class SourceStatus(BaseModel):
    """One row of ``GET /v1/sources`` — never includes ``api_key`` or ``api_url``."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    labels: dict[str, str] = Field(default_factory=dict)
    poll_state: str
    last_success_at: datetime | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    permanent: bool = False


class HealthResponse(BaseModel):
    """GET /v1/health body."""

    status: str = "ok"
    version: str
    transport: str
    agentsh_connected: bool = False
    uptime_seconds: int = Field(ge=0)
    pending_approvals: int = Field(ge=0)
    sources: int = Field(default=0, ge=0)


class ErrorResponse(BaseModel):
    """4xx / 503 error body."""

    error: str
    detail: str = ""
    approval_id: str | None = None
