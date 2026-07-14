"""Data model for a per-instance discovery result.

A :class:`DiscoverySnapshot` is the typed result of running discovery
against one registered instance (see
``specs/010-web-session-interface/data-model.md``, section
"InstanceStatus / DiscoverySnapshot"). Discovery never produces an empty
success: unreachable/incompatible/malformed hosts get a typed
:class:`InstanceStatus` and :class:`TypedError` instead (FR-006).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .capability import RemoteCapability
from .session_target import SessionTarget


class InstanceStatus(str, Enum):
    """Typed outcome of discovery against a single instance."""

    OK = "ok"
    UNREACHABLE = "unreachable"
    AUTH_FAILED = "auth_failed"
    NO_REMO_HOST = "no_remo_host"
    INCOMPATIBLE_PROTOCOL = "incompatible_protocol"
    MALFORMED = "malformed"
    TIMEOUT = "timeout"


@dataclass
class TypedError:
    """A classified, actionable error surfaced for a non-``ok`` discovery result."""

    code: str
    message: str
    retryable: bool
    remediation: str


@dataclass
class DiscoverySnapshot:
    """Per-instance discovery result; immutable and wholesale-replaced on refresh."""

    instance_id: str
    instance_type: str
    instance_name: str
    status: InstanceStatus
    capability: RemoteCapability | None = None
    targets: list[SessionTarget] = field(default_factory=list)
    error: TypedError | None = None
    refreshed_at: str = ""
    # Provider region from the Remo registry (`KnownHost.region`), surfaced so
    # the UI can label an instance as `provider · name · region`. Registry-side
    # only — no remote round-trip; empty string when the registry omits it.
    region: str = ""
