"""Data model for a registered remote development environment."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KnownHost:
    """A registered development environment in the local registry.

    Serialized to a colon-delimited line in the known-hosts registry file.

    Format:
        TYPE:NAME:HOST:USER[:INSTANCE_ID[:ACCESS_MODE[:REGION]]]

    Examples:
        incus:myhost/devcontainer:192.168.1.50:remo
        aws:devbox:3.14.15.92:remo:i-0abc123def:ssm:us-west-2
        hetzner:webserver:5.6.7.8:remo
    """

    type: str
    name: str
    host: str
    user: str
    instance_id: str = field(default="")
    access_mode: str = field(default="")
    region: str = field(default="")

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_line(self) -> str:
        """Serialize to colon-delimited registry format.

        Only optional fields that carry a value are appended.  When
        ``instance_id`` is set but ``access_mode`` is empty, ``access_mode``
        defaults to ``"ssm"`` so the six-field form remains unambiguous.
        Region is only appended when it is non-empty.
        """
        parts: list[str] = [self.type, self.name, self.host, self.user]

        if self.instance_id or self.access_mode:
            effective_access_mode = self.access_mode if self.access_mode else "ssm"
            parts.append(self.instance_id)
            parts.append(effective_access_mode)

        if self.region:
            # Region requires the six-field prefix to already be present.
            if len(parts) == 4:
                # instance_id / access_mode were both empty; pad with empty
                # fields so the position of region is unambiguous.
                parts.append("")
                parts.append("")
            parts.append(self.region)

        return ":".join(parts)

    # ------------------------------------------------------------------
    # Deserialization
    # ------------------------------------------------------------------

    @classmethod
    def from_line(cls, line: str) -> KnownHost:
        """Parse a colon-delimited registry line into a :class:`KnownHost`.

        Handles 4-field, 6-field, and 7-field formats gracefully; extra
        fields are silently ignored.
        """
        parts = line.strip().split(":")
        if len(parts) < 4:
            raise ValueError(
                f"Registry line has fewer than 4 fields: {line!r}"
            )

        type_ = parts[0]
        name = parts[1]
        host = parts[2]
        user = parts[3]
        instance_id = parts[4] if len(parts) > 4 else ""
        access_mode = parts[5] if len(parts) > 5 else ""
        region = parts[6] if len(parts) > 6 else ""

        return cls(
            type=type_,
            name=name,
            host=host,
            user=user,
            instance_id=instance_id,
            access_mode=access_mode,
            region=region,
        )

    # ------------------------------------------------------------------
    # Manually-added SSH host accessors (feature 014-register-ssh-host)
    #
    # For the ``ssh`` type, ``remo add`` stores the SSH port in ``instance_id``
    # and the optional identity path in ``region`` (access_mode is ``direct``).
    # These type-gated readers localize that field-overloading so the rest of
    # the codebase never reinterprets provider slots: for every non-``ssh``
    # type they return neutral values, leaving all other providers untouched.
    # ------------------------------------------------------------------

    @property
    def ssh_port(self) -> int:
        """SSH port for an added (``ssh``-type) host; ``22`` otherwise/default.

        Non-``ssh`` types return the default so a provider's ``instance_id``
        (e.g. a Proxmox vmid or an AWS instance id) is never read as a port.
        """
        from remo_cli.core.config import DEFAULT_SSH_PORT

        if self.type == "ssh" and self.instance_id:
            try:
                return int(self.instance_id)
            except ValueError:
                return DEFAULT_SSH_PORT
        return DEFAULT_SSH_PORT

    @property
    def ssh_identity(self) -> str | None:
        """Stored SSH identity path for an added (``ssh``-type) host, else ``None``."""
        if self.type == "ssh" and self.region:
            return self.region
        return None

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    @property
    def display_name(self) -> str:
        """Human-friendly name for picker UIs.

        For *incus* and *proxmox* hosts the name encodes both the host node
        and the container (``host/container``); this property formats that as
        ``"container (on host)"`` for readability.  For every other
        provider the name is returned unchanged.
        """
        if self.type in {"incus", "proxmox"} and "/" in self.name:
            node, container = self.name.split("/", maxsplit=1)
            return f"{container} (on {node})"
        return self.name
