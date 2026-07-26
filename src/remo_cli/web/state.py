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

from remo_cli.core.config import (
    get_known_hosts_path_readonly,
    get_registry_path_readonly,
    get_remo_home_readonly,
)
from remo_cli.core.registry import RegistryError, read_registry
from remo_cli.web.config import WebSettings

logger = logging.getLogger("remo_cli.web.state")

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


def _probe_registry() -> str:
    """Classify registry presence as ``absent`` / ``ok`` / ``unreadable``.

    Per data-model.md §6, the registry can be EITHER `registry.json` OR the
    legacy `known_hosts` file — either present satisfies "registry present".
    Byte-level readability is checked first (cheap, and matches
    `_probe_file`'s EACCES-safety); only once both candidate files are at
    least byte-readable (or absent) do we actually parse via
    `core.registry.read_registry(readonly=True)`, so a file that exists and
    is readable as bytes but is semantically invalid (e.g. `{"version": 99,
    ...}`, a newer-format file) is still classified ``unreadable`` here —
    the same bucket `detect_state` maps to `BROKEN`.
    """
    registry_probe = _probe_file(get_registry_path_readonly())
    legacy_probe = _probe_file(get_known_hosts_path_readonly())

    if "unreadable" in (registry_probe, legacy_probe):
        return "unreadable"
    if registry_probe == "absent" and legacy_probe == "absent":
        return "absent"

    # Also catches a plain `OSError`: the accessor's own `Path.exists()`/
    # `read_text()` calls raise on EACCES rather than swallowing it, so an
    # untraversable directory that slips past the byte-level probes above
    # (e.g. a TOCTOU race) must still classify as "unreadable", never crash
    # `detect_state`.
    try:
        read_registry(readonly=True)
    except (RegistryError, OSError):
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


# ---------------------------------------------------------------------------
# State detection (research R2)
# ---------------------------------------------------------------------------


def detect_state(settings: WebSettings | None = None) -> ConfigurationState:
    """Derive the configuration state from filesystem probes, on demand.

    Derivation (research R5, data-model.md §6), in strict precedence order:

    1. ``broken`` guard: any required artifact (registry / service private
       key / service public key) present but unreadable, or a half-pair
       service keypair (exactly one of the two key files). A registry file
       that parses at the byte level but is structurally invalid per
       `core.registry` (e.g. a `registry.json` written by a newer,
       unsupported format version -- `RegistryNewerVersionError`) is also
       classified here (015-registry-v2). These guards ALWAYS win, even over
       an explicit ``REMO_WEB_MODE`` override.
    2. Explicit override: when ``settings.mode_override`` (env
       ``REMO_WEB_MODE``) is a valid non-empty value (``adopted`` /
       ``mount_configured``) and no broken guard above fired, it forces that
       mode deterministically (017-web-adopt-simplify, US6).
    3. ``mount_configured``: registry present AND ``REMO_HOME`` NOT writable.
       A non-writable ``REMO_HOME`` (the Docker ``:ro`` bind mount) is now
       the *only* heuristic mount signal -- a readable personal
       ``~/.ssh/id_*`` no longer influences the mode (R5), so bare-metal
       ``remo web serve`` on a writable volume classifies as ``adopted``.
    4. ``adopted``: registry present AND ``REMO_HOME`` writable AND a service
       keypair is present.
    5. ``broken``: registry present AND ``REMO_HOME`` writable AND NO service
       keypair -- a damaged/interrupted adoption with nothing to
       authenticate; re-adopt (or a volume reset) fixes it.
    6. ``unconfigured``: no registry AND ``REMO_HOME`` writable (a service
       keypair may or may not exist yet -- generated, awaiting first push).
    7. ``broken``: no registry AND ``REMO_HOME`` NOT writable -- the old
       "nothing mounted" failure shape.
    """
    settings = settings or WebSettings()

    registry = _probe_registry()
    private = _probe_file(settings.service_private_key_path)
    public = _probe_file(settings.service_public_key_path)

    # (1) Broken guards first: artifacts that exist but cannot be used, or a
    # half-pair service keypair, are never a healthy mode -- and they win
    # even over an explicit REMO_WEB_MODE override.
    if "unreadable" in (registry, private, public):
        return ConfigurationState.BROKEN
    if (private == "ok") != (public == "ok"):
        return ConfigurationState.BROKEN

    # (2) Explicit override: a valid REMO_WEB_MODE now that the broken guards
    # have cleared. `mode_override` is validated at WebSettings construction,
    # so it is either "" or an exact ConfigurationState value here.
    if settings.mode_override:
        return ConfigurationState(settings.mode_override)

    keypair = private == "ok"  # implies public == "ok" after the gate above
    writable = _home_writable(get_remo_home_readonly())

    if registry == "ok":
        # (3) A read-only REMO_HOME is the authoritative mounted-deployment
        # signal; a personal user identity is deliberately ignored (R5).
        if not writable:
            return ConfigurationState.MOUNT_CONFIGURED
        # (4) Writable volume + service keypair -> adopted (bare-metal serve
        # lands here even with a personal ~/.ssh/id_* present).
        if keypair:
            return ConfigurationState.ADOPTED
        # (5) Writable volume, registry, but nothing to authenticate: a
        # damaged/interrupted adoption. Unusable -> broken.
        return ConfigurationState.BROKEN

    # (6) No registry on a writable volume: awaiting adoption.
    if writable:
        return ConfigurationState.UNCONFIGURED
    # (7) No registry on a read-only mount: the old "nothing mounted" shape.
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
