"""Discovery-backed REST endpoints (T027).

Implements `GET /hosts`, `GET /sessions`, and `POST /discovery/refresh` from
`contracts/rest-api.md`, mirroring `web/health.py`'s style: a plain
`APIRouter`, pydantic response models for FastAPI's automatic response
validation/OpenAPI docs, and a shared `DiscoveryService` singleton read from
`request.app.state` (set once in `create_app()`) -- the same
`app.state`-based sharing pattern `health.py` already established for
`WebSettings`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from enum import Enum
from typing import TypeVar

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from remo_cli.core.remo_host_client import (
    IncompatibleProtocolError,
    MalformedResponseError,
    PayloadTooLargeError,
    RemoHostCommandError,
    SshTransportError,
    get_host_stats,
)
from remo_cli.models.discovery import DiscoverySnapshot, InstanceStatus
from remo_cli.models.host import KnownHost
from remo_cli.models.host_stats import HostStats
from remo_cli.models.session_target import DevcontainerRunning, SessionTarget, ZellijState
from remo_cli.web.config import WebSettings
from remo_cli.web.discovery import (
    DiscoveryService,
    build_service_ssh_prefix,
    configure_remediation,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response/request models
# ---------------------------------------------------------------------------


class KnownProviderType(str, Enum):
    """The built-in provider types (data-model.md §1).

    Fixed by the built-in set, not derived from `core.provider_registry.
    all_descriptors()` at import time (FR-004a/SC-011): a third-party
    provider install must never perturb the exported OpenAPI artifact.
    `tests/unit/test_schema_drift.py` (T-8) asserts this enum's members
    equal the built-in descriptor names, so a first-party provider addition
    fails loudly instead of silently drifting from reality.

    Every field this annotates stays typed `KnownProviderType | str`
    (FR-014, SC-009): the wire value is unconstrained (any provider type
    name is valid data); this enum only makes the *known* vocabulary a
    referenceable OpenAPI component for the console to enumerate.
    """

    INCUS = "incus"
    HETZNER = "hetzner"
    AWS = "aws"
    PROXMOX = "proxmox"


class CapabilityOut(BaseModel):
    protocol_version: int
    host_tools_version: str
    projects_root: str
    #: Mirrors `models.capability.RemoteCapability` (same defaults): the
    #: advertised additive verbs plus the two tool-presence booleans. The
    #: console gates its maintenance affordances on `operations`.
    operations: list[str] = []
    zellij: bool = False
    docker: bool = False


class ErrorOut(BaseModel):
    code: str
    message: str
    retryable: bool
    remediation: str


class ErrorEnvelope(BaseModel):
    """The `{"error": {...}}` wire envelope every failure response that
    actually returns it uses (`terminals.py`, `setup.py`, the `app.py`
    middleware). Declared only on routes that return this exact shape —
    `pairing.py`'s 403 returns `{"detail": ...}` instead (data-model.md §2)."""

    error: ErrorOut


class InstanceOut(BaseModel):
    instance_id: str
    instance_type: KnownProviderType | str
    instance_name: str
    status: InstanceStatus
    region: str = ""
    capability: CapabilityOut | None = None
    error: ErrorOut | None = None
    refreshed_at: str | None = None


class HostsResponse(BaseModel):
    instances: list[InstanceOut]


class SessionTargetOut(BaseModel):
    id: str
    instance_type: KnownProviderType | str
    instance_name: str
    project: str
    has_devcontainer: bool
    zellij_state: ZellijState
    devcontainer_running: DevcontainerRunning
    discovered_at: str
    git_tracked: bool = False
    git_dirty: bool = False
    git_ahead: int = 0
    git_behind: int = 0


class SessionsResponse(BaseModel):
    targets: list[SessionTargetOut]


class RefreshRequest(BaseModel):
    instance_id: str | None = None
    #: ``False`` asks for a TTL-gated refresh: run discovery only if the cache
    #: is older than ``discovery_cache_ttl_s``, otherwise no-op. That is what
    #: the console's background poll sends, so N open browsers cost at most one
    #: discovery run per TTL instead of one per tick per browser. Defaults to
    #: ``True`` (always run) so an explicit "Refresh" — and any older client
    #: that omits the field — behaves exactly as before.
    force: bool = True


class RefreshResponse(BaseModel):
    refreshing: bool


class DiskUsageOut(BaseModel):
    """Wire mirror of `models.host_stats.DiskUsage` (the OpenAPI artifact is
    the contract, so the shape is declared here rather than inferred)."""

    mount: str
    size_bytes: int = 0
    used_bytes: int = 0
    avail_bytes: int = 0


class TempReadingOut(BaseModel):
    """Wire mirror of `models.host_stats.TempReading`."""

    name: str
    label: str
    celsius: float


class HostStatsResponse(BaseModel):
    """Wire mirror of `models.host_stats.HostStats`: a live snapshot, no
    time series. ``temps`` is empty on hosts without sensors (VMs/containers);
    the console hides the card entirely then."""

    uptime_s: float = 0.0
    load_1: float = 0.0
    load_5: float = 0.0
    load_15: float = 0.0
    cpu_count: int = 0
    cpu_used_pct: float = 0.0
    mem_total: int = 0
    mem_used: int = 0
    mem_available: int = 0
    swap_total: int = 0
    swap_used: int = 0
    disks: list[DiskUsageOut] = []
    temps: list[TempReadingOut] = []


# ---------------------------------------------------------------------------
# Model mapping helpers
# ---------------------------------------------------------------------------


def _instance_out(snapshot: DiscoverySnapshot) -> InstanceOut:
    capability_out = None
    if snapshot.capability is not None:
        capability_out = CapabilityOut(
            protocol_version=snapshot.capability.protocol_version,
            host_tools_version=snapshot.capability.host_tools_version,
            projects_root=snapshot.capability.projects_root,
            operations=list(snapshot.capability.operations),
            zellij=snapshot.capability.zellij,
            docker=snapshot.capability.docker,
        )

    error_out = None
    if snapshot.error is not None:
        error_out = ErrorOut(
            code=snapshot.error.code,
            message=snapshot.error.message,
            retryable=snapshot.error.retryable,
            remediation=snapshot.error.remediation,
        )

    return InstanceOut(
        instance_id=snapshot.instance_id,
        instance_type=snapshot.instance_type,
        instance_name=snapshot.instance_name,
        status=snapshot.status,
        region=snapshot.region,
        capability=capability_out,
        error=error_out,
        refreshed_at=snapshot.refreshed_at or None,
    )


def _target_out(target: SessionTarget) -> SessionTargetOut:
    return SessionTargetOut(
        id=target.id,
        instance_type=target.instance_type,
        instance_name=target.instance_name,
        project=target.project,
        has_devcontainer=target.has_devcontainer,
        zellij_state=target.zellij_state,
        devcontainer_running=target.devcontainer_running,
        discovered_at=target.discovered_at,
        git_tracked=target.git_tracked,
        git_dirty=target.git_dirty,
        git_ahead=target.git_ahead,
        git_behind=target.git_behind,
    )


def get_discovery_service(request: Request) -> DiscoveryService:
    """Return the app-wide `DiscoveryService`, creating one if `create_app()`
    hasn't (e.g. a router mounted standalone in isolation)."""
    service = getattr(request.app.state, "discovery_service", None)
    if service is None:
        service = DiscoveryService()
        request.app.state.discovery_service = service
    return service


def get_settings(request: Request) -> WebSettings:
    """The app-wide `WebSettings` (set in `create_app()`), like health.py."""
    return getattr(request.app.state, "settings", None) or WebSettings()


# ---------------------------------------------------------------------------
# Shared remo-host call plumbing (used here for /stats and by
# web/api/host_admin.py for the gated maintenance routes)
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


def error_envelope(
    status_code: int, code: str, message: str, *, remediation: str, retryable: bool
) -> JSONResponse:
    """The `{"error": {...}}` wire envelope as a JSONResponse (ErrorEnvelope)."""
    return JSONResponse(
        status_code=status_code,
        content=ErrorEnvelope(
            error=ErrorOut(
                code=code, message=message, retryable=retryable, remediation=remediation
            )
        ).model_dump(),
    )


def unknown_instance_error() -> JSONResponse:
    return error_envelope(
        404,
        "unknown_instance",
        "The requested instance is not currently discovered.",
        remediation="Refresh discovery and pick a currently registered instance.",
        retryable=True,
    )


def unsupported_host_tools_error(host: KnownHost) -> JSONResponse:
    """409: the instance's remo-host predates the requested operation."""
    return error_envelope(
        409,
        "unsupported_host_tools",
        "The instance's Remo host tools do not support this operation.",
        remediation=configure_remediation(host),
        retryable=False,
    )


def resolve_instance(
    service: DiscoveryService, instance_id: str
) -> tuple[DiscoverySnapshot, KnownHost] | None:
    """Resolve an opaque instance id to its snapshot + full `KnownHost`.

    ``None`` when the id is not in the current discovery cache OR the
    instance is no longer registered — callers answer with
    :func:`unknown_instance_error` (404).
    """
    snapshot = service.find_instance(instance_id)
    if snapshot is None:
        return None
    host = service.find_host(snapshot.instance_type, snapshot.instance_name)
    if host is None:
        return None
    return snapshot, host


def operation_supported(snapshot: DiscoverySnapshot, operation: str) -> bool:
    """True when the instance's advertised ``operations[]`` includes *operation*.

    A missing capability (host not ``ok`` at last discovery) is "unsupported":
    the caller can neither prove nor perform the operation.
    """
    capability = snapshot.capability
    return capability is not None and operation in capability.operations


async def run_host_call(func: Callable[[], _T], *, timeout_s: float) -> _T:
    """Run a blocking remo-host client call off the event loop, time-bounded.

    Mirrors discovery's executor + ``asyncio.wait_for`` pattern (FR-005): the
    client is `subprocess.run`-based, so it runs in the default thread-pool
    executor rather than being rewritten as async.
    """
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(loop.run_in_executor(None, func), timeout=timeout_s)


def map_host_call_error(
    exc: Exception,
    host: KnownHost,
    *,
    exit3_code: str = "unknown_project",
    exit3_message: str = "The named project does not exist on the instance.",
) -> JSONResponse:
    """Map a remo-host client failure to the documented ErrorEnvelope response.

    * exit 2/4 (old host answering a verb it does not know — top-level unknown
      verbs exit 2, sub-verbs 4) and protocol incompatibility -> 409
      ``unsupported_host_tools`` naming the per-type configure/upgrade command;
    * exit 3 -> a 404 whose code the route chooses (``unknown_project`` /
      ``unknown_job``);
    * transport / malformed-response / timeout failures -> 502.
    """
    if isinstance(exc, RemoHostCommandError):
        if exc.returncode in (2, 4):
            return unsupported_host_tools_error(host)
        if exc.returncode == 3:
            return error_envelope(
                404, exit3_code, exit3_message,
                remediation="Refresh discovery and pick a current name.",
                retryable=False,
            )
        return error_envelope(
            502,
            "remote_command_failed",
            f"remo-host failed on the instance: {exc}",
            remediation="Check the instance's remo-host logs and retry.",
            retryable=True,
        )
    if isinstance(exc, IncompatibleProtocolError):
        return unsupported_host_tools_error(host)
    if isinstance(exc, (MalformedResponseError, PayloadTooLargeError)):
        return error_envelope(
            502,
            "malformed_response",
            f"remo-host returned an unexpected response: {exc}",
            remediation=configure_remediation(host),
            retryable=False,
        )
    if isinstance(exc, SshTransportError):
        return error_envelope(
            502,
            "ssh_transport",
            "Could not reach the instance over SSH.",
            remediation="Check instance is running / reachable.",
            retryable=True,
        )
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return error_envelope(
            502,
            "timeout",
            "The instance did not answer in time.",
            remediation="Check instance is reachable and not overloaded; retry.",
            retryable=True,
        )
    raise exc


class HostStatsCache:
    """Per-instance TTL mini-cache + lock coalescing stats polling.

    Multiple tabs polling ``GET /hosts/{id}/stats`` cost at most one SSH
    round trip per ``host_stats_ttl_s`` per host: the per-instance
    ``asyncio.Lock`` serializes concurrent fetches, and whoever waited on
    the lock finds a fresh entry and returns it without a second call.
    Only successes are cached — a failing host is re-probed on the next poll.
    """

    def __init__(self, ttl_s: float) -> None:
        self._ttl_s = ttl_s
        self._locks: dict[str, asyncio.Lock] = {}
        self._entries: dict[str, tuple[float, HostStats]] = {}

    def lock_for(self, instance_id: str) -> asyncio.Lock:
        return self._locks.setdefault(instance_id, asyncio.Lock())

    def get_fresh(self, instance_id: str) -> HostStats | None:
        entry = self._entries.get(instance_id)
        if entry is None:
            return None
        fetched_at, stats = entry
        if time.monotonic() - fetched_at >= self._ttl_s:
            return None
        return stats

    def store(self, instance_id: str, stats: HostStats) -> None:
        self._entries[instance_id] = (time.monotonic(), stats)


def get_stats_cache(request: Request) -> HostStatsCache:
    cache = getattr(request.app.state, "host_stats_cache", None)
    if cache is None:
        cache = HostStatsCache(get_settings(request).host_stats_ttl_s)
        request.app.state.host_stats_cache = cache
    return cache


def _stats_response(stats: HostStats) -> HostStatsResponse:
    return HostStatsResponse(
        uptime_s=stats.uptime_s,
        load_1=stats.load_1,
        load_5=stats.load_5,
        load_15=stats.load_15,
        cpu_count=stats.cpu_count,
        cpu_used_pct=stats.cpu_used_pct,
        mem_total=stats.mem_total,
        mem_used=stats.mem_used,
        mem_available=stats.mem_available,
        swap_total=stats.swap_total,
        swap_used=stats.swap_used,
        disks=[
            DiskUsageOut(
                mount=d.mount,
                size_bytes=d.size_bytes,
                used_bytes=d.used_bytes,
                avail_bytes=d.avail_bytes,
            )
            for d in stats.disks
        ],
        temps=[
            TempReadingOut(name=t.name, label=t.label, celsius=t.celsius)
            for t in stats.temps
        ],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/hosts", response_model=HostsResponse)
async def get_hosts(request: Request) -> HostsResponse:
    """`GET /api/v1/hosts` -- current `DiscoverySnapshot` per instance, from cache."""
    service = get_discovery_service(request)
    return HostsResponse(instances=[_instance_out(s) for s in service.get_snapshot()])


@router.get("/sessions", response_model=SessionsResponse)
async def get_sessions(request: Request) -> SessionsResponse:
    """`GET /api/v1/sessions` -- flattened `SessionTarget[]` across `ok` instances."""
    service = get_discovery_service(request)
    return SessionsResponse(targets=[_target_out(t) for t in service.get_targets()])


@router.post("/discovery/refresh", response_model=RefreshResponse, status_code=202)
async def post_discovery_refresh(
    request: Request,
    background_tasks: BackgroundTasks,
    body: RefreshRequest | None = None,
) -> RefreshResponse:
    """`POST /api/v1/discovery/refresh` -- kick off a fresh discovery run.

    Never blocks on the discovery run itself (FR-035): the refresh is
    scheduled as a `BackgroundTasks` job and results land in the cache
    incrementally, visible on subsequent `GET /hosts`/`GET /sessions` calls.

    ``force: false`` in the body makes the run TTL-gated — the console's
    background poll uses it so a long-lived page keeps its view fresh without
    every tick costing an SSH round trip to every instance.
    """
    service = get_discovery_service(request)
    instance_id = body.instance_id if body is not None else None
    force = body.force if body is not None else True
    background_tasks.add_task(service.refresh, instance_id, force=force)
    return RefreshResponse(refreshing=True)


@router.get(
    "/hosts/{instance_id}/stats",
    response_model=HostStatsResponse,
    responses={
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        502: {"model": ErrorEnvelope},
    },
)
async def get_host_stats_route(request: Request, instance_id: str):
    """`GET /api/v1/hosts/{instance_id}/stats` -- live host statistics snapshot.

    Ungated by design: the same trust level as `GET /hosts` (it discloses
    load/mem/temps to anyone who can reach the service — a documented
    accepted risk for this home-lab tool). Polling is coalesced by
    :class:`HostStatsCache` to at most one SSH call per TTL per host.
    """
    service = get_discovery_service(request)
    settings = get_settings(request)
    resolved = resolve_instance(service, instance_id)
    if resolved is None:
        return unknown_instance_error()
    snapshot, host = resolved

    if not operation_supported(snapshot, "host.stats"):
        return unsupported_host_tools_error(host)

    cache = get_stats_cache(request)
    async with cache.lock_for(instance_id):
        cached = cache.get_fresh(instance_id)
        if cached is not None:
            return _stats_response(cached)
        prefix = build_service_ssh_prefix(host, settings)
        try:
            stats = await run_host_call(
                lambda: get_host_stats(prefix, timeout=settings.discovery_timeout_s),
                timeout_s=settings.discovery_timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 - mapped to the wire envelope below.
            return map_host_call_error(exc, host)
        cache.store(instance_id, stats)
    return _stats_response(stats)
