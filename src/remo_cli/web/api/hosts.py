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

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel

from remo_cli.models.discovery import DiscoverySnapshot
from remo_cli.models.session_target import SessionTarget
from remo_cli.web.discovery import DiscoveryService

router = APIRouter()


# ---------------------------------------------------------------------------
# Response/request models
# ---------------------------------------------------------------------------


class CapabilityOut(BaseModel):
    protocol_version: int
    host_tools_version: str
    projects_root: str


class ErrorOut(BaseModel):
    code: str
    message: str
    retryable: bool
    remediation: str


class InstanceOut(BaseModel):
    instance_id: str
    instance_type: str
    instance_name: str
    status: str
    region: str = ""
    capability: CapabilityOut | None = None
    error: ErrorOut | None = None
    refreshed_at: str | None = None


class HostsResponse(BaseModel):
    instances: list[InstanceOut]


class SessionTargetOut(BaseModel):
    id: str
    instance_type: str
    instance_name: str
    project: str
    has_devcontainer: bool
    zellij_state: str
    devcontainer_running: str
    discovered_at: str
    git_tracked: bool = False
    git_dirty: bool = False
    git_ahead: int = 0
    git_behind: int = 0


class SessionsResponse(BaseModel):
    targets: list[SessionTargetOut]


class RefreshRequest(BaseModel):
    instance_id: str | None = None


class RefreshResponse(BaseModel):
    refreshing: bool


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
        status=snapshot.status.value,
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
        zellij_state=target.zellij_state.value,
        devcontainer_running=target.devcontainer_running.value,
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
    """
    service = get_discovery_service(request)
    instance_id = body.instance_id if body is not None else None
    background_tasks.add_task(service.refresh, instance_id)
    return RefreshResponse(refreshing=True)
