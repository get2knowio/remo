"""Data model for the capabilities reported by a remote ``remo-host`` instance.

Produced by parsing the JSON payload of ``remo-host capabilities --json`` (see
``specs/010-web-session-interface/contracts/remo-host-protocol.md``). This is
external/remote input, so :meth:`RemoteCapability.from_dict` performs boundary
validation on the fields the client depends on for compatibility negotiation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RemoteCapability:
    """Capabilities and metadata reported by a remote instance's ``remo-host``.

    Additive-compatible (R2): unknown extra keys in the wire payload are
    ignored rather than rejected, so newer hosts remain usable by older
    clients within the same major protocol version.
    """

    protocol_version: int
    host_tools_version: str
    projects_root: str
    operations: list[str] = field(default_factory=list)
    zellij: bool = False
    docker: bool = False

    # ------------------------------------------------------------------
    # Deserialization
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> RemoteCapability:
        """Parse a JSON-decoded ``capabilities --json`` payload.

        Unknown extra keys are ignored (additive-compatible per R2).
        Raises :class:`ValueError` if ``protocol_version`` is missing or is
        not a positive int — this is boundary validation of untrusted
        remote input, not a value error appropriate for the caller to skip.
        """
        protocol_version = data.get("protocol_version")
        if not isinstance(protocol_version, int) or isinstance(protocol_version, bool):
            raise ValueError(
                "capabilities payload has invalid 'protocol_version': "
                f"expected a positive int, got {protocol_version!r}"
            )
        if protocol_version <= 0:
            raise ValueError(
                "capabilities payload has invalid 'protocol_version': "
                f"expected a positive int, got {protocol_version!r}"
            )

        return cls(
            protocol_version=protocol_version,
            host_tools_version=str(data.get("host_tools_version", "")),
            projects_root=str(data.get("projects_root", "")),
            operations=list(data.get("operations", []) or []),
            zellij=bool(data.get("zellij", False)),
            docker=bool(data.get("docker", False)),
        )
