"""Configuration-state detection + service identity (011-web-adopt, T004/T005).

The service's self-knowledge of its mode -- `unconfigured` / `adopted` /
`mount_configured` / `broken` -- is derived from pure filesystem probes
(research R2), computed on demand and never stored, so there is no mode flag
that can drift out of sync with reality. All probes are EACCES-safe in the
style of `web/health.py`: `Path.exists()`/`Path.is_file()` raise (rather than
swallow) `PermissionError` on an untraversable path, so every probe catches
`OSError` and reports "unreadable" instead of crashing.

This module also owns the `ServiceIdentity` lifecycle (research R3): the
service-scoped ed25519 keypair under `<REMO_HOME>/web-identity/`, generated
once via `ssh-keygen` and NEVER regenerated while the key files exist
(FR-002) -- replacing it is exclusively a state-volume reset.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from remo_cli.core.config import get_known_hosts_path_readonly, get_remo_home_readonly
from remo_cli.web.config import WebSettings

logger = logging.getLogger("remo_cli.web.state")

# The user-identity resolution mount-configured detection relies on -- the
# explicit override env var plus the conventional ~/.ssh filenames. Mirrors
# `health._check_ssh_identity()` exactly (deliberately NOT the service
# keypair paths: a user identity here means the operator mounted one).
_SSH_IDENTITY_CANDIDATES = ("id_ed25519", "id_ecdsa", "id_rsa", "id_dsa")

_KEY_COMMENT_PREFIX = "remo-web@"


class ConfigurationState(str, Enum):
    UNCONFIGURED = "unconfigured"
    ADOPTED = "adopted"
    MOUNT_CONFIGURED = "mount_configured"
    BROKEN = "broken"


# ---------------------------------------------------------------------------
# Filesystem probes (EACCES-safe)
# ---------------------------------------------------------------------------


def _probe_file(path: Path) -> str:
    """Classify a required artifact as ``absent`` / ``ok`` / ``unreadable``.

    "unreadable" covers both an existing file this process cannot read and a
    path it cannot even stat (EACCES on a parent directory) -- either way the
    artifact cannot be used, which is what callers care about.
    """
    try:
        if not path.is_file():
            return "absent"
        if not os.access(path, os.R_OK):
            return "unreadable"
    except OSError:
        return "unreadable"
    return "ok"


def _home_writable(home: Path) -> bool:
    """Whether the service can write into (or create) ``REMO_HOME``.

    A missing directory counts as writable when its nearest existing
    ancestor is -- the adoption flow creates it on demand -- while a `:ro`
    bind mount (the mounted deployment mode) fails `os.access(W_OK)`.
    """
    try:
        probe = home
        while not probe.exists():
            parent = probe.parent
            if parent == probe:
                return False
            probe = parent
        return probe.is_dir() and os.access(probe, os.W_OK)
    except OSError:
        return False


def _user_identity_present() -> bool:
    """A user SSH identity resolvable via today's mechanism (see health.py)."""
    explicit = os.environ.get("REMO_WEB_SSH_IDENTITY_FILE")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    ssh_dir = Path.home() / ".ssh"
    candidates.extend(ssh_dir / name for name in _SSH_IDENTITY_CANDIDATES)

    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(candidate, os.R_OK):
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
# State detection (research R2)
# ---------------------------------------------------------------------------


def detect_state(settings: WebSettings | None = None) -> ConfigurationState:
    """Derive the configuration state from filesystem probes, on demand.

    Derivation (research R2):

    - ``broken``: any required artifact present but unreadable, or a
      half-pair service keypair (exactly one of the two key files).
    - ``mount_configured``: registry present AND (``REMO_HOME`` not writable
      OR a user SSH identity resolves). Explicit mounts are the operator's
      stated intent, so this wins even when a service keypair also exists
      (the precedence rule).
    - ``adopted``: ``REMO_HOME`` writable + service keypair + registry.
    - ``unconfigured``: ``REMO_HOME`` writable, no registry (a service
      keypair may or may not exist yet -- generated, awaiting first push).
    """
    settings = settings or WebSettings()

    registry = _probe_file(get_known_hosts_path_readonly())
    private = _probe_file(settings.service_private_key_path)
    public = _probe_file(settings.service_public_key_path)

    # Broken first: artifacts that exist but cannot be used are never a
    # healthy mode, whatever else is present.
    if "unreadable" in (registry, private, public):
        return ConfigurationState.BROKEN
    if (private == "ok") != (public == "ok"):
        return ConfigurationState.BROKEN

    keypair = private == "ok"  # implies public == "ok" after the gate above
    writable = _home_writable(get_remo_home_readonly())

    if registry == "ok":
        if not writable or _user_identity_present():
            return ConfigurationState.MOUNT_CONFIGURED
        if keypair:
            return ConfigurationState.ADOPTED
        # A registry on a writable volume with nothing able to authenticate
        # (no service keypair, no user identity): a damaged/interrupted
        # adoption. Unusable -> broken; re-adopt (or volume reset) fixes it.
        return ConfigurationState.BROKEN

    # No registry: awaiting adoption -- but only when the volume can
    # actually be adopted. A read-only mount without a registry is the old
    # "nothing mounted" failure shape.
    if writable:
        return ConfigurationState.UNCONFIGURED
    return ConfigurationState.BROKEN


# ---------------------------------------------------------------------------
# Service identity (research R3, FR-002)
# ---------------------------------------------------------------------------


class ServiceIdentityError(RuntimeError):
    """The service keypair is unusable or could not be generated."""


@dataclass
class ServiceIdentity:
    deployment_id: str
    public_key: str
    private_key_path: Path
    created_at: str | None


def _mint_deployment_id() -> str:
    # 6 random bytes -> exactly 8 URL-safe base64 characters.
    return secrets.token_urlsafe(6)


def load_service_identity(settings: WebSettings | None = None) -> ServiceIdentity | None:
    """Load the existing identity with no side effects.

    Returns ``None`` unless a complete, readable keypair exists.
    ``deployment_id`` comes from ``state.json``; when that file is missing or
    corrupt it falls back to the ``remo-web@<id>`` comment embedded in the
    public key (the durable copy, research R3).
    """
    settings = settings or WebSettings()
    private = settings.service_private_key_path
    public = settings.service_public_key_path
    if _probe_file(private) != "ok" or _probe_file(public) != "ok":
        return None

    try:
        public_key = public.read_text().strip()
    except OSError:
        return None

    deployment_id = ""
    created_at: str | None = None
    try:
        state = json.loads(settings.service_state_path.read_text())
        deployment_id = str(state.get("deployment_id") or "")
        raw_created = state.get("created_at")
        created_at = str(raw_created) if raw_created else None
    except (OSError, ValueError):
        pass

    if not deployment_id:
        comment = public_key.rsplit(" ", 1)[-1]
        if comment.startswith(_KEY_COMMENT_PREFIX):
            deployment_id = comment[len(_KEY_COMMENT_PREFIX) :]

    return ServiceIdentity(
        deployment_id=deployment_id,
        public_key=public_key,
        private_key_path=private,
        created_at=created_at,
    )


def ensure_service_identity(settings: WebSettings | None = None) -> ServiceIdentity:
    """Return the service identity, generating it on first call.

    NEVER regenerates while the key files exist (FR-002): a complete pair is
    loaded as-is; a half-pair or unreadable pair raises (that is the
    ``broken`` state -- resolution is a documented state-volume reset, never
    a silent overwrite).
    """
    settings = settings or WebSettings()
    private = settings.service_private_key_path
    public = settings.service_public_key_path

    private_probe = _probe_file(private)
    public_probe = _probe_file(public)
    if private_probe == "ok" and public_probe == "ok":
        identity = load_service_identity(settings)
        if identity is None:  # pragma: no cover -- raced away between probes
            raise ServiceIdentityError("service keypair vanished while loading")
        return identity
    if private_probe != "absent" or public_probe != "absent":
        raise ServiceIdentityError(
            "service identity is unusable (half-pair or unreadable key files); "
            f"reset the state volume to regenerate ({private}, {public})"
        )

    identity_dir = settings.web_identity_dir
    identity_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # mkdir's mode is umask-filtered and skipped entirely for a pre-existing
    # directory; enforce explicitly.
    identity_dir.chmod(0o700)

    deployment_id = _mint_deployment_id()
    comment = f"{_KEY_COMMENT_PREFIX}{deployment_id}"
    result = subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(private)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ServiceIdentityError(
            f"ssh-keygen failed (rc={result.returncode}): {result.stderr.strip()}"
        )

    private.chmod(0o600)
    public.chmod(0o644)

    created_at = datetime.now(UTC).isoformat()
    settings.service_state_path.write_text(
        json.dumps({"deployment_id": deployment_id, "created_at": created_at}, indent=2) + "\n"
    )

    # The comment (not the key!) is safe and useful to log: it is the marker
    # operators grep for in instances' authorized_keys (SC-008).
    logger.info("generated service identity %s", comment)

    return ServiceIdentity(
        deployment_id=deployment_id,
        public_key=public.read_text().strip(),
        private_key_path=private,
        created_at=created_at,
    )
