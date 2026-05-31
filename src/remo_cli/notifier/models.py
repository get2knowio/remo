"""Pydantic v2 models for the notifier wire protocol.

These define the durable contract between agentsh (or any future emitter) and
the notifier. See specs/007-notifier-sidecar/contracts/openapi.yaml and
data-model.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OperationKind(str, Enum):
    command = "command"
    file = "file"
    network = "network"
    signal = "signal"


class OperationContext(str, Enum):
    direct = "direct"
    nested = "nested"


class Decision(str, Enum):
    allow = "allow"
    deny = "deny"


class Operation(BaseModel):
    """The operation agentsh is asking a human to approve."""

    model_config = ConfigDict(extra="forbid")

    kind: OperationKind
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    path: str | None = None
    remote_host: str | None = None
    remote_port: int | None = Field(default=None, ge=1, le=65535)
    context: OperationContext = OperationContext.direct
    depth: int = Field(default=0, ge=0)


class ApprovalRequest(BaseModel):
    """Inbound approval request (POST /v1/approve body)."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str | None = None
    session_id: str | None = None
    operation: Operation
    policy_rule_name: str
    policy_message: str
    workspace: str | None = None
    instance_id: str | None = None
    project: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)
    submitted_at: str | None = None

    @field_validator("approval_id")
    @classmethod
    def _validate_uuid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # Raises ValueError -> 422/400 if not a valid UUID string.
        uuid.UUID(v)
        return v


class ApprovalDecision(BaseModel):
    """Internal resolution value carried by a pending approval's Future."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    responder: str
    reason: str = ""
    decided_at: datetime = Field(default_factory=_utcnow)


class ApprovalResponse(BaseModel):
    """Outbound decision (POST /v1/approve response body)."""

    approval_id: str
    decision: Decision
    responder: str
    reason: str = ""
    decided_at: datetime
    latency_ms: int = Field(ge=0)


class HealthResponse(BaseModel):
    """GET /v1/health body."""

    status: str = "ok"
    version: str
    transport: str
    uptime_seconds: int = Field(ge=0)
    pending_approvals: int = Field(ge=0)


class ErrorResponse(BaseModel):
    """4xx / 503 error body (where not the 408 fail-secure deny shape)."""

    error: str
    detail: str = ""
    approval_id: str | None = None
