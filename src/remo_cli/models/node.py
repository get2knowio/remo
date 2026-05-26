"""Data model for an Incus/Proxmox node registered in ~/.config/remo/nodes.yml."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_FNOX_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VALID_PROVIDERS = frozenset({"incus", "proxmox"})


class NodeValidationError(ValueError):
    """Raised when a Node field fails validation."""


@dataclass
class Node:
    """A registered self-hosted node (Incus host or Proxmox VE).

    Persisted to ~/.config/remo/nodes.yml (mode 0600). Never contains
    secret values; admin_sa_fnox_key is a *reference* into the developer's
    laptop-side fnox keystore.
    """

    name: str
    provider: str
    host: str
    ssh_user: str
    admin_sa_fnox_key: str
    registered_at: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not _NAME_RE.match(self.name):
            raise NodeValidationError(
                f"invalid node name {self.name!r}: must match ^[a-z][a-z0-9-]{{0,31}}$"
            )
        if self.provider not in _VALID_PROVIDERS:
            raise NodeValidationError(
                f"invalid provider {self.provider!r}: must be one of "
                f"{sorted(_VALID_PROVIDERS)}"
            )
        if not self.host:
            raise NodeValidationError("host must be non-empty")
        if not self.ssh_user:
            raise NodeValidationError("ssh_user must be non-empty")
        if not _FNOX_KEY_RE.match(self.admin_sa_fnox_key):
            raise NodeValidationError(
                f"invalid admin_sa_fnox_key {self.admin_sa_fnox_key!r}: "
                "must match ^[a-z][a-z0-9_]{0,63}$"
            )
        if not self.registered_at:
            raise NodeValidationError("registered_at must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "host": self.host,
            "ssh_user": self.ssh_user,
            "admin_sa_fnox_key": self.admin_sa_fnox_key,
            "registered_at": self.registered_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Node:
        required = ("name", "provider", "host", "ssh_user", "admin_sa_fnox_key", "registered_at")
        missing = [k for k in required if k not in data]
        if missing:
            raise NodeValidationError(f"node entry missing fields: {missing}")
        return cls(
            name=str(data["name"]),
            provider=str(data["provider"]),
            host=str(data["host"]),
            ssh_user=str(data["ssh_user"]),
            admin_sa_fnox_key=str(data["admin_sa_fnox_key"]),
            registered_at=str(data["registered_at"]),
        )
