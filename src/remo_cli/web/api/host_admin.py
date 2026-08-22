"""Config-gated host-maintenance API (host-detail feature, plan §2.3).

Project clone / delete / rebuild plus detached-job polling, driven through
the ``remo-host`` additive verbs (``projects clone|delete|rebuild``,
``jobs status``). The surface is **off by default** and dormant when off:
every route on this router answers the same ``404 {"detail": "Not Found"}``
an unknown route does (the ``/setup`` pairing-dormancy precedent,
`web/api/setup.py`'s ``_dormant``), so a scanner cannot even learn the
surface exists. When an operator-auth provider is configured
(`web/operator_auth.py`), an unauthenticated request gets the SAME 404.

Enforced for every route by the router-level :func:`require_host_admin`
dependency; the router itself is mounted unconditionally in ``create_app()``
— dormancy lives inside the dependency, exactly like setup's.

Error mapping (plan §2.3): unknown instance -> 404 envelope; operation
missing from ``capability.operations[]`` or remote exit 2/4 -> 409
``unsupported_host_tools`` (remediation names the per-type configure/upgrade
command); remote exit 3 -> 404 ``unknown_project``/``unknown_job``;
client-side :class:`~remo_cli.core.errors.PreconditionError` -> 400;
transport failures -> 502. The shared plumbing lives in `web/api/hosts.py`
so `/stats` and this router can never diverge.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from remo_cli.core.errors import PreconditionError
from remo_cli.core.remo_host_client import (
    delete_project,
    get_job_status,
    start_project_clone,
    start_project_rebuild,
)
from remo_cli.models.host_job import JobState
from remo_cli.web.api.hosts import (
    ErrorEnvelope,
    error_envelope,
    get_discovery_service,
    get_settings,
    map_host_call_error,
    operation_supported,
    resolve_instance,
    run_host_call,
    unknown_instance_error,
    unsupported_host_tools_error,
)
from remo_cli.web.discovery import build_service_ssh_prefix

logger = logging.getLogger("remo_cli.web.host_admin")


def _dormant() -> HTTPException:
    """The dormant response — byte-identical to FastAPI's default unknown-route
    404 (the `web/api/setup.py` precedent). A fresh instance per raise (never a
    shared singleton, which would accumulate traceback/context state)."""
    return HTTPException(status_code=404, detail="Not Found")


async def require_host_admin(request: Request) -> None:
    """Host-admin gate shared by every route on this router.

    Dormant ``404`` unless ``REMO_WEB_HOST_ADMIN=enabled``. When an
    operator-auth provider is configured, a request the provider refuses
    gets the SAME 404 — never a distinguishable 401/403 that would reveal
    the surface exists.
    """
    settings = get_settings(request)
    if not settings.host_admin_enabled:
        raise _dormant()

    provider = getattr(request.app.state, "operator_auth_provider", None)
    if provider is not None and provider.authenticate(request) is None:
        client = request.client.host if request.client else "unknown"
        logger.warning(
            "host-admin request without operator authentication from %s: %s %s",
            client,
            request.method,
            request.url.path,
        )
        raise _dormant()


router = APIRouter(dependencies=[Depends(require_host_admin)])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    repo: str
    name: str | None = None


class RebuildProjectRequest(BaseModel):
    no_cache: bool = False


class JobAcceptedResponse(BaseModel):
    """202: the host detached a job; poll `GET /hosts/{id}/jobs/{job_id}`."""

    job_id: str
    kind: str
    project: str


class ProjectDeletedResponse(BaseModel):
    project: str
    deleted: bool = True


class JobStatusResponse(BaseModel):
    state: JobState
    exit_code: int | None = None
    started_at: str = ""
    finished_at: str = ""
    log_tail: str = ""


_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    502: {"model": ErrorEnvelope},
}


def _invalid_request_error(exc: PreconditionError) -> JSONResponse:
    return error_envelope(
        400,
        "invalid_request",
        str(exc),
        remediation="Correct the request and retry.",
        retryable=False,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/hosts/{instance_id}/projects",
    status_code=202,
    response_model=JobAcceptedResponse,
    responses=_ERROR_RESPONSES,
)
async def create_project(request: Request, instance_id: str, body: CreateProjectRequest):
    """Clone a repository into the host's ``projects_root`` (detached job)."""
    service = get_discovery_service(request)
    settings = get_settings(request)
    resolved = resolve_instance(service, instance_id)
    if resolved is None:
        return unknown_instance_error()
    snapshot, host = resolved
    if not operation_supported(snapshot, "projects.clone"):
        return unsupported_host_tools_error(host)

    prefix = build_service_ssh_prefix(host, settings)
    try:
        ref = await run_host_call(
            lambda: start_project_clone(
                prefix, body.repo, name=body.name, timeout=settings.discovery_timeout_s
            ),
            timeout_s=settings.discovery_timeout_s,
        )
    except PreconditionError as exc:
        return _invalid_request_error(exc)
    except Exception as exc:  # noqa: BLE001 - mapped to the wire envelope.
        return map_host_call_error(exc, host)
    return JobAcceptedResponse(job_id=ref.job_id, kind=ref.kind, project=ref.project)


@router.delete(
    "/hosts/{instance_id}/projects/{project}",
    response_model=ProjectDeletedResponse,
    responses=_ERROR_RESPONSES,
)
async def delete_project_route(
    request: Request,
    background_tasks: BackgroundTasks,
    instance_id: str,
    project: str,
):
    """Delete a project (synchronous on the host: session + containers + dir)."""
    service = get_discovery_service(request)
    settings = get_settings(request)
    resolved = resolve_instance(service, instance_id)
    if resolved is None:
        return unknown_instance_error()
    snapshot, host = resolved
    if not operation_supported(snapshot, "projects.delete"):
        return unsupported_host_tools_error(host)

    prefix = build_service_ssh_prefix(host, settings)
    # delete_project's own timeout (PROJECT_DELETE_TIMEOUT, 30s) bounds the
    # subprocess; give the awaiting side the same headroom rather than the
    # shorter discovery timeout, so a slow-but-succeeding delete isn't
    # abandoned mid-flight.
    try:
        await run_host_call(
            lambda: delete_project(prefix, project),
            timeout_s=35.0,
        )
    except PreconditionError as exc:
        return _invalid_request_error(exc)
    except Exception as exc:  # noqa: BLE001 - mapped to the wire envelope.
        return map_host_call_error(exc, host)
    # The project list just changed: refresh this instance's snapshot so the
    # console's next poll reflects the deletion without a manual refresh.
    background_tasks.add_task(service.refresh, instance_id)
    return ProjectDeletedResponse(project=project)


@router.post(
    "/hosts/{instance_id}/projects/{project}/rebuild",
    status_code=202,
    response_model=JobAcceptedResponse,
    responses=_ERROR_RESPONSES,
)
async def rebuild_project(
    request: Request,
    instance_id: str,
    project: str,
    body: RebuildProjectRequest | None = None,
):
    """Rebuild a project's devcontainer (detached job)."""
    service = get_discovery_service(request)
    settings = get_settings(request)
    resolved = resolve_instance(service, instance_id)
    if resolved is None:
        return unknown_instance_error()
    snapshot, host = resolved
    if not operation_supported(snapshot, "projects.rebuild"):
        return unsupported_host_tools_error(host)

    no_cache = body.no_cache if body is not None else False
    prefix = build_service_ssh_prefix(host, settings)
    try:
        ref = await run_host_call(
            lambda: start_project_rebuild(
                prefix, project, no_cache=no_cache, timeout=settings.discovery_timeout_s
            ),
            timeout_s=settings.discovery_timeout_s,
        )
    except PreconditionError as exc:
        return _invalid_request_error(exc)
    except Exception as exc:  # noqa: BLE001 - mapped to the wire envelope.
        return map_host_call_error(exc, host)
    return JobAcceptedResponse(job_id=ref.job_id, kind=ref.kind, project=ref.project)


@router.get(
    "/hosts/{instance_id}/jobs/{job_id}",
    response_model=JobStatusResponse,
    responses=_ERROR_RESPONSES,
)
async def get_job_status_route(request: Request, instance_id: str, job_id: str):
    """Poll a detached job's status + log tail (console polls at 2s)."""
    service = get_discovery_service(request)
    settings = get_settings(request)
    resolved = resolve_instance(service, instance_id)
    if resolved is None:
        return unknown_instance_error()
    snapshot, host = resolved
    if not operation_supported(snapshot, "jobs.status"):
        return unsupported_host_tools_error(host)

    prefix = build_service_ssh_prefix(host, settings)
    try:
        status = await run_host_call(
            lambda: get_job_status(prefix, job_id, timeout=settings.discovery_timeout_s),
            timeout_s=settings.discovery_timeout_s,
        )
    except PreconditionError as exc:
        return _invalid_request_error(exc)
    except Exception as exc:  # noqa: BLE001 - mapped to the wire envelope.
        return map_host_call_error(
            exc,
            host,
            exit3_code="unknown_job",
            exit3_message="The named job does not exist on the instance.",
        )
    return JobStatusResponse(
        state=status.state,
        exit_code=status.exit_code,
        started_at=status.started_at,
        finished_at=status.finished_at,
        log_tail=status.log_tail,
    )
