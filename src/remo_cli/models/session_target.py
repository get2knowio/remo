"""Data model for a discovered, openable (instance, project) session target.

A :class:`SessionTarget` is the `(instance, project)` pair that can be opened
in a browser terminal (see ``specs/010-web-session-interface/data-model.md``,
section "SessionTarget"). Its ``id`` is an opaque public identifier — never a
command or path — derived server-side so the client never supplies raw
targets (FR-002/FR-015).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum


class ZellijState(str, Enum):
    """Zellij session state for a project, as reported by ``remo-host``."""

    ACTIVE = "active"
    EXITED = "exited"
    ABSENT = "absent"


class DevcontainerRunning(str, Enum):
    """Devcontainer running state for a project.

    ``UNKNOWN`` covers hosts where docker is unavailable and the state
    cannot be determined (see :class:`~remo_cli.models.capability.RemoteCapability.docker`).
    """

    RUNNING = "running"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


@dataclass
class SessionTarget:
    """An `(instance, project)` pair openable in a terminal."""

    id: str
    instance_type: str
    instance_name: str
    project: str
    has_devcontainer: bool
    zellij_state: ZellijState
    devcontainer_running: DevcontainerRunning
    discovered_at: str
    # Read-only git status of the project's working tree, reported by
    # `remo-host` (never a `git fetch`, so ahead/behind reflect the
    # last-known upstream and may be stale — FR-010). Defaults keep older
    # hosts (whose payloads omit these keys) parseable: git_tracked=False
    # means "not a git repo / unknown", and the UI shows no git glyphs.
    git_tracked: bool = False
    git_dirty: bool = False
    git_ahead: int = 0
    git_behind: int = 0


def derive_session_target_id(instance_type: str, instance_name: str, project: str) -> str:
    """Derive a stable, opaque public ID for a `(type, name, project)` triple.

    Deterministic (same inputs -> same id), so discovery refresh produces
    stable IDs across cycles. This is intentionally NOT security-sensitive
    beyond opacity: real authorization happens server-side via the discovery
    cache lookup (id -> (instance, project)), not via secrecy of the ID
    itself. FR-002 requires that the id not expose a command/path, not that
    it be a secret — so a plain SHA-256 digest (rather than a keyed HMAC) is
    sufficient for the MVP.
    """
    stable = f"{instance_type}\x1f{instance_name}\x1f{project}".encode()
    return hashlib.sha256(stable).hexdigest()[:32]
