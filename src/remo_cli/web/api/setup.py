"""Token-gated setup API router (`/api/v1/setup/*`, 011-web-adopt T008).

Authentication contract (contracts/setup-api.md, FR-020/FR-021/FR-024),
enforced for every route on this router by the `require_setup_token`
dependency:

- token NOT configured (``REMO_WEB_API_TOKEN`` unset/empty) -> ``404`` on
  every setup route. Fail closed: the surface is disabled and the response
  is indistinguishable from an unknown route (same body FastAPI returns for
  a path that does not exist).
- token configured + correct ``Authorization: Bearer <token>`` -> the route
  handles the request. Comparison is constant-time (`hmac.compare_digest`).
- token configured + missing/wrong header -> ``401 {"detail":
  "unauthorized"}`` with no further detail; the attempt is logged WITHOUT
  the presented credential.

Business endpoints (contracts/setup-api.md is the normative wire contract;
T011/T012/T013), all inheriting the router-level token dependency:

- ``GET /status`` -- configuration state + identity presence; cheap, pollable.
- ``GET /identity`` -- deployment id + public key; generates the service
  identity on first call when unconfigured (idempotent, FR-002); ``409
  {"reason": "mount_configured"}`` when the deployment is mount-configured.
- ``PUT /registry`` -- the `AdoptionPayload` mirror. Validates EVERYTHING
  before writing anything (FR-019), then applies atomically: service
  known_hosts file first, registry file last (research R5), each via
  temp-file + ``os.replace``. Live terminal sessions are never touched --
  they hold their own SSH processes; this is file replacement only.
- ``POST /verify`` -- JSON wrapper around `web.check.run_checks()` with
  instance checks included (sync route: FastAPI runs it in a threadpool, so
  the ~5s-per-unreachable-instance round-trips never block the event loop).
"""

from __future__ import annotations

import hmac
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from remo_cli.core.config import get_known_hosts_path, get_known_hosts_path_readonly
from remo_cli.core.known_hosts import _write_lines_atomically
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


def _get_settings(request: Request) -> WebSettings:
    """The app-wide `WebSettings` (set in `create_app()`), like health.py."""
    return getattr(request.app.state, "settings", None) or WebSettings()


async def require_setup_token(request: Request) -> None:
    """Bearer-token gate shared by every setup route (research R4)."""
    configured = _get_settings(request).api_token.strip()
    if not configured:
        # No token configured: the setup surface does not exist. Mirror
        # FastAPI's default unknown-route response exactly (FR-021).
        raise HTTPException(status_code=404, detail="Not Found")

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() == "bearer" and hmac.compare_digest(
        presented.strip().encode(), configured.encode()
    ):
        return

    # Log the failure, never the presented credential (FR-024).
    client = request.client.host if request.client else "unknown"
    logger.warning("setup API authentication failure from %s on %s", client, request.url.path)
    raise HTTPException(status_code=401, detail="unauthorized")


router = APIRouter(prefix="/setup", dependencies=[Depends(require_setup_token)])


# ---------------------------------------------------------------------------
# Request/response models (contracts/setup-api.md shapes)
# ---------------------------------------------------------------------------


class SetupStatusResponse(BaseModel):
    state: str
    deployment_id: str | None
    public_key_available: bool
    registry_instances: int


class IdentityResponse(BaseModel):
    deployment_id: str
    public_key: str


class RegistryEntryIn(BaseModel):
    """One `AdoptionPayload.registry` entry -- mirrors `models/host.py:KnownHost`."""

    type: str
    name: str
    host: str
    user: str
    instance_id: str = ""
    access_mode: str = ""
    region: str = ""


class AdoptionPayloadIn(BaseModel):
    """`PUT /registry` body (data-model.md AdoptionPayload) -- a full mirror."""

    version: int
    registry: list[RegistryEntryIn]
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


def _read_registry_readonly() -> list[KnownHost]:
    """Parse the registry with no mkdir side effects; unreadable/absent -> []."""
    try:
        text = get_known_hosts_path_readonly().read_text()
    except OSError:
        return []
    hosts: list[KnownHost] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            hosts.append(KnownHost.from_line(line))
        except ValueError:
            continue
    return hosts


def _mount_configured_response() -> JSONResponse:
    return JSONResponse(status_code=409, content={"reason": "mount_configured"})


def _invalid_payload(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"reason": "invalid_payload", "detail": detail}
    )


def _is_ssm_entry(entry: RegistryEntryIn) -> bool:
    """Mirrors `KnownHost.to_line`'s default: instance_id set + no explicit mode -> ssm."""
    return entry.access_mode == "ssm" or bool(entry.instance_id and not entry.access_mode)


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


def _validate_payload(payload: AdoptionPayloadIn) -> str | None:
    """All semantic `AdoptionPayload` rules (data-model.md); error detail or None.

    All-or-nothing: callers write NOTHING unless this returns None (FR-019).
    The empty-registry guard is separate (its own 422 reason).
    """
    if payload.version != 1:
        return f"unsupported payload version {payload.version} (expected 1)"

    names: set[str] = set()
    for index, entry in enumerate(payload.registry):
        for field_name in ("type", "name", "host", "user"):
            if not getattr(entry, field_name).strip():
                return f"registry[{index}]: field {field_name!r} must be non-empty"
        for field_name in ("type", "name", "host", "user", "instance_id", "access_mode", "region"):
            value = getattr(entry, field_name)
            if ":" in value or "\n" in value:
                return (
                    f"registry[{index}].{field_name}: value {value!r} cannot contain "
                    "':' or newline (colon-delimited registry format)"
                )
        names.add(entry.name)

    ssm_names = {entry.name for entry in payload.registry if _is_ssm_entry(entry)}
    for name, lines in payload.host_keys.items():
        if name not in names:
            return f"host_keys entry {name!r} does not reference any registry entry"
        if name in ssm_names:
            return f"host_keys entry {name!r} references an SSM-access instance (FR-012)"
        for line_index, line in enumerate(lines):
            error = _known_hosts_line_error(line)
            if error is not None:
                return f"host_keys[{name!r}][{line_index}]: {error}"
    return None


def _apply_payload(payload: AdoptionPayloadIn, settings: WebSettings) -> None:
    """Atomic two-file apply: service known_hosts FIRST, registry LAST (R5).

    Each file is replaced via temp-file + ``os.replace`` (the
    `core/known_hosts.py` atomic pattern). A crash between the two writes
    leaves a superset of needed host keys and the old registry -- safe, and
    convergent on re-push (FR-015). Never touches live terminal sessions:
    established SSH processes hold their own file descriptors.
    """
    known_hosts_lines: list[str] = []
    for entry in payload.registry:
        for line in payload.host_keys.get(entry.name, []):
            known_hosts_lines.append(line.strip())

    identity_dir = settings.web_identity_dir
    identity_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    _write_lines_atomically(settings.service_known_hosts_path, known_hosts_lines)

    registry_lines = [
        KnownHost(
            type=entry.type,
            name=entry.name,
            host=entry.host,
            user=entry.user,
            instance_id=entry.instance_id,
            access_mode=entry.access_mode,
            region=entry.region,
        ).to_line()
        for entry in payload.registry
    ]
    _write_lines_atomically(get_known_hosts_path(), registry_lines)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", response_model=SetupStatusResponse)
def get_status(request: Request) -> SetupStatusResponse:
    """`GET /api/v1/setup/status` -- service mode + identity presence. Cheap."""
    settings = _get_settings(request)
    identity = load_service_identity(settings)  # no side effects
    return SetupStatusResponse(
        state=detect_state(settings).value,
        deployment_id=(identity.deployment_id or None) if identity else None,
        public_key_available=identity is not None,
        registry_instances=len(_read_registry_readonly()),
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

    Validates the FULL payload before writing anything (FR-019); a
    mount-configured deployment is read-only via this API (409, FR-017); an
    empty registry requires the explicit ``allow_empty=true`` opt-out
    (defense-in-depth for the CLI-side FR-016 guard).
    """
    settings = _get_settings(request)
    if detect_state(settings) is ConfigurationState.MOUNT_CONFIGURED:
        return _mount_configured_response()

    try:
        payload = AdoptionPayloadIn.model_validate(body)
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        return _invalid_payload(detail or "malformed payload")

    error = _validate_payload(payload)
    if error is not None:
        return _invalid_payload(error)

    if not payload.registry and not allow_empty:
        return JSONResponse(status_code=422, content={"reason": "empty_registry"})

    try:
        _apply_payload(payload, settings)
    except OSError as exc:
        logger.error("registry apply failed: %s", exc)
        raise HTTPException(status_code=500, detail="failed to apply registry") from exc

    logger.info(
        "adoption mirror applied: %d registry entries, %d instances with host keys",
        len(payload.registry),
        len(payload.host_keys),
    )
    return RegistryApplyResponse(
        applied=True,
        registry_instances=len(payload.registry),
        host_key_instances=len(payload.host_keys),
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
