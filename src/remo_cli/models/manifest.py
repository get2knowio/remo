"""Data model for a project's broker manifest (`.devcontainer/remo-broker.toml`)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})
_SECRET_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ManifestValidationError(ValueError):
    """Raised when a manifest fails laptop-side validation."""


@dataclass
class ProjectManifest:
    """Declarative allowlist of backend secret names for one project.

    Fields match the TOML shape in contracts/manifest-schema.md.
    """

    schema_version: int
    secrets: list[str] = field(default_factory=list)
    notes: str | None = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ManifestValidationError(
                f"unsupported schema_version {self.schema_version}; "
                f"supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
        if not isinstance(self.secrets, list):
            raise ManifestValidationError("[mcp].secrets must be an array")
        for s in self.secrets:
            if not isinstance(s, str) or not _SECRET_NAME_RE.match(s):
                raise ManifestValidationError(
                    f"invalid secret name {s!r}: must match ^[a-z][a-z0-9_]{{0,63}}$"
                )
