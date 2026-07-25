"""Pairing-gated setup API router (`/api/v1/setup/*`).

011 gated this surface with a static `REMO_WEB_API_TOKEN` bearer. 012
(web-adopt-pairing) replaces that with an ephemeral **pairing code**: the
surface is **dormant** (`404`) unless a live pairing session exists, and it is
authenticated solely by the live code the CLI carries. Enforced for every route
on this router by the `require_pairing_code` dependency (contracts/setup-api.md,
FR-005/FR-006):

- no live pairing session -> ``404 {"detail": "Not Found"}`` on every setup
  route, byte-identical to an unknown route (dormant surface, fail closed).
- live session + correct ``Authorization: Bearer <code>`` -> the route handles
  the request and the session is touched (sliding idle TTL reset). Comparison
  is constant-time (`hmac.compare_digest`, inside the manager).
- live session + absent/wrong/expired-or-rotated code -> the SAME dormant
  ``404`` (never a distinguishable ``401`` that would reveal a session exists,
  FR-006). The presented code is NEVER logged (FR-016).

Business endpoints (contracts/setup-api.md is the normative wire contract;
T011/T012/T013), all inheriting the router-level token dependency:

- ``GET /status`` -- configuration state + identity presence; cheap, pollable.
  Also advertises ``payload_versions`` (specs/015-registry-v2/contracts/
  mirror-payload-v2.md §1) so the workstation can fail fast on version skew.
- ``GET /identity`` -- deployment id + public key; generates the service
  identity on first call when unconfigured (idempotent, FR-002); ``409
  {"reason": "mount_configured"}`` when the deployment is mount-configured.
- ``PUT /registry`` -- the `AdoptionPayload` mirror. Accepts payload v1 (legacy
  colon-shaped fields) AND v2 (registry-file-v2.md hostEntry shape); v1 entries
  are mapped through the same legacy->v2 mapper the CLI migration uses
  (specs/015-registry-v2/contracts/mirror-payload-v2.md §2). Validates
  EVERYTHING before writing anything (FR-019), then applies atomically:
  service known_hosts file first, registry.json (v2) second, any stale legacy
  mirror file removed last (research R9).
- ``POST /verify`` -- JSON wrapper around `web.check.run_checks()` with
  instance checks included (sync route: FastAPI runs it in a threadpool, so
  the ~5s-per-unreachable-instance round-trips never block the event loop).
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from remo_cli.core import registry
from remo_cli.core.config import get_known_hosts_path
from remo_cli.models.host import KnownHost
from remo_cli.web import check as web_check
from remo_cli.web.config import WebSettings
from remo_cli.web.state import (
    ConfigurationState,
    ServiceIdentityError,
    detect_state,
    ensure_service_identity,
    load_service_identity,
)

logger = logging.getLogger("remo_cli.web.setup")

SUPPORTED_PAYLOAD_VERSIONS: list[int] = [1, 2]


def _get_settings(request: Request) -> WebSettings:
    """The app-wide `WebSettings` (set in `create_app()`), like health.py."""
    return getattr(request.app.state, "settings", None) or WebSettings()


def _dormant() -> HTTPException:
    """The dormant response — byte-identical to FastAPI's default unknown-route
    404 (FR-005/FR-006). A fresh instance per raise (never a shared singleton,
    which would accumulate traceback/context state)."""
    return HTTPException(status_code=404, detail="Not Found")


async def require_pairing_code(request: Request) -> None:
    """Pairing-code gate shared by every setup route (FR-005/FR-006).

    Dormant ``404`` unless a live pairing session exists AND the bearer matches
    the live code. A missing/wrong/expired code is indistinguishable from a
    dormant surface (same ``404``, never a ``401``). On success the session's
    sliding idle TTL is reset. The presented code is never logged (FR-016).
    """
    manager = request.app.state.pairing_manager
    if not manager.is_live():
        raise _dormant()

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() == "bearer" and manager.authenticate(presented.strip()) is not None:
        return

    # Log the failure with route/method context, never the presented credential.
    client = request.client.host if request.client else "unknown"
    logger.warning(
        "setup API request against no valid pairing code from %s: %s %s",
        client,
        request.method,
        request.url.path,
    )
    raise _dormant()


router = APIRouter(prefix="/setup", dependencies=[Depends(require_pairing_code)])


# ---------------------------------------------------------------------------
# Request/response models (contracts/setup-api.md, mirror-payload-v2.md shapes)
# ---------------------------------------------------------------------------


class SetupStatusResponse(BaseModel):
    state: str
    deployment_id: str | None
    public_key_available: bool
    registry_instances: int
    payload_versions: list[int] = Field(default_factory=lambda: list(SUPPORTED_PAYLOAD_VERSIONS))


class IdentityResponse(BaseModel):
    deployment_id: str
    public_key: str


class RegistryEntryV1In(BaseModel):
    """One v1 `AdoptionPayload.registry` entry -- the legacy colon-shaped fields."""

    type: str
    name: str
    host: str
    user: str
    instance_id: str = ""
    access_mode: str = ""
    region: str = ""


class AdoptionPayloadV1In(BaseModel):
    """v1 `PUT /registry` body (accepted for backward compatibility, FR-022)."""

    version: int
    registry: list[RegistryEntryV1In]
    host_keys: dict[str, list[str]] = Field(default_factory=dict)


class RegistryEntryV2In(BaseModel):
    """One v2 hostEntry -- exact schema from registry-file-v2.md.

    ``extra="allow"`` so the per-type nested object (``incus``/``proxmox``/
    ``aws``/``ssh``, keyed by ``type``) round-trips without a dedicated
    sub-model per type; shape is validated by :func:`registry.entry_to_known_host`.
    """

    model_config = ConfigDict(extra="allow")

    type: str
    name: str
    host: str
    user: str
    access: str


class AdoptionPayloadV2In(BaseModel):
    """v2 `PUT /registry` body (canonical; what an upgraded CLI sends)."""

    version: int
    registry: list[RegistryEntryV2In]
    host_keys: dict[str, list[str]] = Field(default_factory=dict)


class RegistryApplyResponse(BaseModel):
    applied: bool
    registry_instances: int
    host_key_instances: int


class VerifyCheckOut(BaseModel):
    name: str
    passed: bool
    detail: str
    remediation: str | None = None


class VerifyResponse(BaseModel):
    results: list[VerifyCheckOut]
    all_passed: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_lines_atomically(path: Path, lines: list[str]) -> None:
    """Write *lines* to *path* atomically via a same-directory temp file + rename.

    Used only for the service's own SSH ``known_hosts`` trust file (not the
    remo registry, which goes through :mod:`core.registry`'s own atomic writer).
    """
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=dir_, prefix=".known_hosts_tmp_")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w") as fh:
            for line in lines:
                fh.write(line + "\n")
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _mount_configured_response() -> JSONResponse:
    return JSONResponse(status_code=409, content={"reason": "mount_configured"})


def _invalid_payload(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"reason": "invalid_payload", "detail": detail}
    )


def _unsupported_payload_version(version: Any) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "unsupported_payload_version",
                "supported": list(SUPPORTED_PAYLOAD_VERSIONS),
                "received": version,
            }
        },
    )


#: Plausible OpenSSH key-type token, e.g. ssh-ed25519, ecdsa-sha2-nistp256,
#: sk-ssh-ed25519@openssh.com, ssh-rsa-cert-v01@openssh.com.
_HOST_KEY_TYPE_RE = re.compile(r"^(sk-)?(ssh|ecdsa)-[a-z0-9-]+(@[a-z0-9.-]+)?$")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_KNOWN_HOSTS_MARKERS = ("@cert-authority", "@revoked")


def _known_hosts_line_error(line: str) -> str | None:
    """Basic structural validation of one `known_hosts` line; None when OK."""
    stripped = line.strip()
    if not stripped:
        return "empty line"
    if stripped.startswith("#"):
        return "comment line"
    fields = stripped.split()
    if fields[0].startswith("@"):
        if fields[0] not in _KNOWN_HOSTS_MARKERS:
            return f"unknown marker {fields[0]!r}"
        fields = fields[1:]
    if len(fields) < 3:
        return "fewer than 3 fields (expected: hosts, key type, base64 key)"
    key_type, key_material = fields[1], fields[2]
    if not _HOST_KEY_TYPE_RE.match(key_type):
        return f"implausible key type {key_type!r}"
    if len(key_material) < 16 or not _BASE64_RE.match(key_material):
        return "key material is not plausible base64"
    return None


def _map_v1_entries(entries: list[RegistryEntryV1In]) -> tuple[list[KnownHost], str | None]:
    """Map v1 entries through the shared legacy->v2 mapper (data-model.md §4)."""
    hosts: list[KnownHost] = []
    for index, entry in enumerate(entries):
        for field_name in ("type", "name", "host", "user"):
            if not getattr(entry, field_name).strip():
                return [], f"registry[{index}]: field {field_name!r} must be non-empty"
        v2_entry = registry.legacy_fields_to_entry(
            entry.type,
            entry.name,
            entry.host,
            entry.user,
            entry.instance_id,
            entry.access_mode,
            entry.region,
        )
        known_host = registry.entry_to_known_host(v2_entry)
        if known_host is None:
            return [], f"registry[{index}]: unrecognized type {entry.type!r}"
        hosts.append(known_host)
    return hosts, None


def _map_v2_entries(entries: list[RegistryEntryV2In]) -> tuple[list[KnownHost], str | None]:
    hosts: list[KnownHost] = []
    for index, entry in enumerate(entries):
        raw = entry.model_dump()
        known_host = registry.entry_to_known_host(raw)
        if known_host is None:
            return [], f"registry[{index}]: does not match the expected hostEntry shape"
        hosts.append(known_host)
    return hosts, None


def _validate_host_keys(
    hosts: list[KnownHost], host_keys: dict[str, list[str]]
) -> str | None:
    names = {h.name for h in hosts}
    ssm_names = {h.name for h in hosts if h.access_mode == "ssm"}
    for name, lines in host_keys.items():
        if name not in names:
            return f"host_keys entry {name!r} does not reference any registry entry"
        if name in ssm_names:
            return f"host_keys entry {name!r} references an SSM-access instance (FR-012)"
        for line_index, line in enumerate(lines):
            error = _known_hosts_line_error(line)
            if error is not None:
                return f"host_keys[{name!r}][{line_index}]: {error}"
    return None


def _apply_payload(
    hosts: list[KnownHost], host_keys: dict[str, list[str]], settings: WebSettings
) -> None:
    """Ordered, crash-convergent apply (contracts/mirror-payload-v2.md §3):

    1. service trust file (``web-identity/known_hosts``) -- unchanged from today.
    2. ``registry.json`` (v2) via :func:`core.registry.replace_registry`.
    3. remove any legacy ``known_hosts`` mirror file left from a pre-upgrade
       push (service-owned replaceable state, not user data).

    A crash mid-sequence leaves a readable superset; re-push converges.
    """
    known_hosts_lines: list[str] = []
    for host in hosts:
        for line in host_keys.get(host.name, []):
            known_hosts_lines.append(line.strip())

    identity_dir = settings.web_identity_dir
    identity_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_lines_atomically(settings.service_known_hosts_path, known_hosts_lines)

    registry.replace_registry(hosts, allow_empty=True)

    get_known_hosts_path().unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", response_model=SetupStatusResponse)
def get_status(request: Request) -> SetupStatusResponse:
    """`GET /api/v1/setup/status` -- service mode + identity presence. Cheap."""
    settings = _get_settings(request)
    identity = load_service_identity(settings)  # no side effects
    registry_instances = len(registry.read_registry(readonly=True).hosts)
    return SetupStatusResponse(
        state=detect_state(settings).value,
        deployment_id=(identity.deployment_id or None) if identity else None,
        public_key_available=identity is not None,
        registry_instances=registry_instances,
        payload_versions=list(SUPPORTED_PAYLOAD_VERSIONS),
    )


@router.get("/identity", response_model=IdentityResponse)
def get_identity(request: Request) -> IdentityResponse | JSONResponse:
    """`GET /api/v1/setup/identity` -- deployment id + public key.

    A mount-configured service has no service identity to authorize -> 409
    (FR-017). Otherwise the identity is generated on first call when absent
    (idempotent: an existing keypair is loaded, NEVER regenerated, FR-002).
    """
    settings = _get_settings(request)
    if detect_state(settings) is ConfigurationState.MOUNT_CONFIGURED:
        return _mount_configured_response()
    try:
        identity = ensure_service_identity(settings)
    except ServiceIdentityError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return IdentityResponse(deployment_id=identity.deployment_id, public_key=identity.public_key)


@router.put("/registry", response_model=RegistryApplyResponse)
def put_registry(
    request: Request, body: dict[str, Any], allow_empty: bool = False
) -> RegistryApplyResponse | JSONResponse:
    """`PUT /api/v1/setup/registry` -- apply the adoption mirror atomically.

    Accepts payload v1 (legacy fields, mapped through the shared legacy->v2
    mapper) and v2 (registry-file-v2.md hostEntry shape); always stores v2
    (FR-020/FR-022). Validates the FULL payload before writing anything
    (FR-019); a mount-configured deployment is read-only via this API (409,
    FR-017); an empty registry requires the explicit ``allow_empty=true``
    opt-out (defense-in-depth for the CLI-side FR-016 guard); an unsupported
    ``version`` is rejected with the prior mirror left completely intact
    (400 ``unsupported_payload_version``, FR-021).
    """
    settings = _get_settings(request)
    if detect_state(settings) is ConfigurationState.MOUNT_CONFIGURED:
        return _mount_configured_response()

    version = body.get("version")
    if version not in SUPPORTED_PAYLOAD_VERSIONS:
        return _unsupported_payload_version(version)

    try:
        if version == 1:
            payload_v1 = AdoptionPayloadV1In.model_validate(body)
            hosts, error = _map_v1_entries(payload_v1.registry)
            host_keys = payload_v1.host_keys
        else:
            payload_v2 = AdoptionPayloadV2In.model_validate(body)
            hosts, error = _map_v2_entries(payload_v2.registry)
            host_keys = payload_v2.host_keys
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        return _invalid_payload(detail or "malformed payload")

    if error is not None:
        return _invalid_payload(error)

    if not hosts and not allow_empty:
        return JSONResponse(status_code=422, content={"reason": "empty_registry"})

    try:
        registry.validate_hosts(hosts)
    except registry.RegistryValidationError as exc:
        return _invalid_payload(str(exc))

    host_keys_error = _validate_host_keys(hosts, host_keys)
    if host_keys_error is not None:
        return _invalid_payload(host_keys_error)

    try:
        _apply_payload(hosts, host_keys, settings)
    except (OSError, registry.RegistryError) as exc:
        # OSError: a filesystem write failed. RegistryError: a lock timeout
        # (RegistryBusyError) or an unreadable/newer-version registry.json on
        # the service volume -- either way a clean 500, never an uncaught
        # traceback (the hosts themselves were already validated above).
        logger.error("registry apply failed: %s", exc)
        raise HTTPException(status_code=500, detail="failed to apply registry") from exc

    logger.info(
        "adoption mirror applied (payload v%d): %d registry entries, %d instances with host keys",
        version,
        len(hosts),
        len(host_keys),
    )
    return RegistryApplyResponse(
        applied=True,
        registry_instances=len(hosts),
        host_key_instances=len(host_keys),
    )


@router.post("/verify", response_model=VerifyResponse)
def post_verify(request: Request) -> VerifyResponse:
    """`POST /api/v1/setup/verify` -- the existing check pass, as JSON.

    Thin wrapper over `web.check.run_checks()` (research R4: verify reuses
    the check module, never duplicates it), instance round-trips included.
    Deliberately a sync route: FastAPI executes it in a threadpool, so the
    up-to-~5s-per-unreachable-instance runtime never blocks the event loop.
    """
    settings = _get_settings(request)
    results = web_check.run_checks(settings, include_instances=True)

    # The verification pass is the terminal authenticated step of both the
    # adopt and push flows (status -> identity -> registry -> verify). Ending
    # the session here returns the setup surface to dormant once the flow
    # completes (FR-007) without severing the in-flight verify call that ending
    # on the registry PUT would have broken. If a client skips verify, the idle
    # TTL / page-hide beacon remain the backstop.
    request.app.state.pairing_manager.end()

    return VerifyResponse(
        results=[
            VerifyCheckOut(
                name=result.name,
                passed=result.passed,
                detail=result.detail,
                remediation=result.remediation,
            )
            for result in results
        ],
        all_passed=web_check.all_passed(results),
    )
