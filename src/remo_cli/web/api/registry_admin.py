"""Config-gated registry-admin API (023): manage hosts from the console.

Add / remove / configure SSH hosts by shelling out to the service's own
embedded `remo` CLI (the Docker image ships the full CLI — wheel +
ansible-core + playbooks + openssh-client), never by reimplementing it.
Short calls (`remo add`, `remo remove`, key scans, an SSH verify) run inline
in the route's threadpool; a configure play runs as a detached
:class:`~remo_cli.web.jobs.CliJobRunner` job the console polls.

Gated by ``REMO_WEB_REGISTRY_ADMIN=enabled`` — a NEW flag, not
``REMO_WEB_HOST_ADMIN`` reuse: registry mutation changes *which machines the
service will SSH into* and appends to its trust store, a bigger blast radius
than project maintenance. Same dormant-404 posture as `/setup` and
host_admin: off (or operator-auth-refused) requests answer the byte-identical
404 an unknown route does. All mutating routes additionally refuse
mount-configured deployments with 409 ``read_only_deployment`` (that mode's
REMO_HOME is read-only; the CLI would fail anyway — this fails it cleanly).

Trust bootstrap for a new host (the deploy-key pattern):

1. ``POST /registry/hosts`` registers the entry and returns the paste-one-liner
   that authorizes the service key on the target.
2. ``POST …/scan-key`` + ``…/trust-key``: the operator confirms the scanned
   fingerprints in the browser; the client echoes exactly the lines it showed
   (no blind re-scan window) and the server re-validates each against this
   instance's lookup key — the route can never trust an arbitrary host.
3. ``POST …/verify`` proves the service can actually authenticate.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from remo_cli.core import registry
from remo_cli.core.ssh import build_ssh_base_cmd
from remo_cli.core.web_adopt import (
    build_authorize_command,
    classify_scanned_keys,
    known_hosts_lookup_key,
    render_fingerprint_list,
    scan_host_keys,
)
from remo_cli.models.host import KnownHost
from remo_cli.models.host_job import JobState
from remo_cli.web.api.host_admin import JobAcceptedResponse, JobStatusResponse
from remo_cli.web.api.hosts import (
    ErrorEnvelope,
    error_envelope,
    get_discovery_service,
    get_settings,
)
from remo_cli.web.config import WebSettings
from remo_cli.web.discovery import derive_instance_id
from remo_cli.web.jobs import CliJobRunner, DuplicateJobError
from remo_cli.web.mirror_meta import record_change
from remo_cli.web.state import (
    ConfigurationState,
    ServiceIdentityError,
    detect_state,
    ensure_service_identity,
)
from remo_cli.web.trust_store import (
    known_hosts_line_error,
    remove_instance_host_keys,
    set_instance_host_keys,
)

logger = logging.getLogger("remo_cli.web.registry_admin")

#: `--only` / `--skip` values ride into CLI argv; keep them to the tool-name
#: alphabet before they get anywhere near a subprocess.
_TOOL_RE = re.compile(r"^[a-z0-9_-]+$")

_CLI_TIMEOUT_S = 15.0
_SCAN_TIMEOUT_S = 15.0
_VERIFY_TIMEOUT_S = 20.0


def _dormant() -> HTTPException:
    """The dormant response — byte-identical to FastAPI's default unknown-route
    404 (the `web/api/setup.py` precedent). A fresh instance per raise (never a
    shared singleton, which would accumulate traceback/context state)."""
    return HTTPException(status_code=404, detail="Not Found")


async def require_registry_admin(request: Request) -> None:
    """Registry-admin gate shared by every route on this router.

    Dormant ``404`` unless ``REMO_WEB_REGISTRY_ADMIN=enabled``. When an
    operator-auth provider is configured, a request the provider refuses gets
    the SAME 404 — never a distinguishable 401/403 that would reveal the
    surface exists.
    """
    settings = get_settings(request)
    if not settings.registry_admin_enabled:
        raise _dormant()

    provider = getattr(request.app.state, "operator_auth_provider", None)
    if provider is not None and provider.authenticate(request) is None:
        client = request.client.host if request.client else "unknown"
        logger.warning(
            "registry-admin request without operator authentication from %s: %s %s",
            client,
            request.method,
            request.url.path,
        )
        raise _dormant()


router = APIRouter(prefix="/registry", dependencies=[Depends(require_registry_admin)])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AddHostRequest(BaseModel):
    """Deliberately NO identity field: the service always authenticates with
    its own key (a container path stored in the entry would sync to
    workstations and, under IdentitiesOnly, guarantee auth failure there)."""

    name: str
    target: str
    user: str | None = None
    port: int | None = None


class AddHostResponse(BaseModel):
    instance_id: str
    name: str
    host: str
    user: str
    port: int
    public_key: str
    authorize_command: str


class HostRemovedResponse(BaseModel):
    name: str
    removed: bool = True


class ScanKeyResponse(BaseModel):
    status: str  # trusted | mismatch | no_trust | unreachable
    detail: str
    fingerprints: list[str] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)


class TrustKeyRequest(BaseModel):
    #: The client echoes exactly the lines the operator saw and confirmed —
    #: never "whatever the host answers now" (no blind re-scan window).
    lines: list[str]


class TrustKeyResponse(BaseModel):
    trusted: bool = True


class VerifyHostResponse(BaseModel):
    status: str  # ok | auth_failed | host_key_untrusted | unreachable
    detail: str


class AuthorizeCommandResponse(BaseModel):
    public_key: str
    authorize_command: str


class ConfigureRequest(BaseModel):
    only: list[str] = Field(default_factory=list)
    skip: list[str] = Field(default_factory=list)


# Job responses REUSE host_admin's models (JobAcceptedResponse /
# JobStatusResponse, imported above) rather than redefining wire-identical
# copies: two same-named pydantic models would make FastAPI mangle BOTH
# schema names in the OpenAPI artifact (module-path prefixes), breaking the
# console's generated-type imports. `project` is always "" for registry jobs.


class JobSummary(BaseModel):
    job_id: str
    kind: str
    state: str
    started_at: str = ""
    finished_at: str = ""


class JobListResponse(BaseModel):
    jobs: list[JobSummary]


_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    502: {"model": ErrorEnvelope},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str], timeout: float) -> tuple[int, str]:
    """Run one embedded-CLI command; ``(returncode, stderr tail)``.

    `print_error` writes to stderr, so the tail is the CLI's own actionable
    message. Module-level so tests monkeypatch it (the host_admin seam
    pattern); 124 stands in for a timeout (no partial-output semantics
    needed — every mapped caller treats unexpected codes as cli_failure).
    """
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return 127, "the `remo` CLI is not on the service PATH"
    except subprocess.TimeoutExpired:
        return 124, f"`{argv[0]} {argv[1] if len(argv) > 1 else ''}` timed out after {timeout:.0f}s"
    stderr_tail = (result.stderr or "").strip()[-2000:]
    return result.returncode, stderr_tail


def _run_ssh(cmd: list[str], timeout: float) -> tuple[int, str]:
    """Run one ssh argv; ``(returncode, stderr)``. Module-level test seam.

    ``(255, <exception text>)`` for spawn/timeout failures — ssh's own
    transport-failure code, so the caller's mapping stays uniform.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 255, str(exc)
    return result.returncode, (result.stderr or "").strip()


def _find_registry_host(instance_id: str) -> KnownHost | None:
    """Resolve *instance_id* against a FRESH registry read.

    Deliberately not `resolve_instance` (discovery-cache-backed): a
    just-added host is not discovered yet, and these routes exist precisely
    for that window.
    """
    try:
        view = registry.read_registry(readonly=True)
    except registry.RegistryError:
        return None
    for host in view.hosts:
        if derive_instance_id(host) == instance_id:
            return host
    return None


def _unknown_registry_host() -> JSONResponse:
    return error_envelope(
        404,
        "unknown_instance",
        "The requested instance is not in the service registry.",
        remediation="Refresh and pick a currently registered instance.",
        retryable=True,
    )


def _read_only_deployment() -> JSONResponse:
    return error_envelope(
        409,
        "read_only_deployment",
        "This deployment is mount-configured: its registry is a read-only "
        "mirror of a workstation's.",
        remediation="Manage hosts from the workstation CLI (`remo add` / "
        "`remo remove`), then restart the service with the updated mount.",
        retryable=False,
    )


def _provider_managed(host: KnownHost, action: str) -> JSONResponse:
    return error_envelope(
        409,
        "provider_managed",
        f"'{host.name}' is a {host.type}-managed instance; the console can "
        f"only {action} hosts added over plain SSH.",
        remediation=f"Use the workstation CLI: remo {host.type} "
        + ("destroy" if action == "remove" else "upgrade")
        + f" {host.name}",
        retryable=False,
    )


def _mount_guard(settings: WebSettings) -> JSONResponse | None:
    if detect_state(settings) is ConfigurationState.MOUNT_CONFIGURED:
        return _read_only_deployment()
    return None


def _job_runner(request: Request) -> CliJobRunner:
    runner = getattr(request.app.state, "cli_job_runner", None)
    if runner is None:
        runner = CliJobRunner(get_settings(request))
        request.app.state.cli_job_runner = runner
    return runner


def _write_lock(request: Request) -> threading.Lock:
    """The app-wide lock making the v3 generation check atomic with the apply.

    The same lock `PUT /setup/registry`'s v3 precondition holds. Console
    mutators hold it across the WHOLE mutation — the CLI subprocess /
    trust-file write AND the marker bump — not just the bump: a sync PUT
    interleaving between a console registry write and its generation bump
    would pass the stale-generation precondition and wholesale-overwrite the
    console's change (created in `create_app`; lazily here for bare test
    apps).
    """
    lock = getattr(request.app.state, "registry_write_lock", None)
    if lock is None:
        lock = threading.Lock()
        request.app.state.registry_write_lock = lock
    return lock


def _record_web_change_locked(request: Request) -> None:
    """Bump the mirror marker. Caller MUST hold :func:`_write_lock`."""
    record_change(get_settings(request), origin="web")


# ---------------------------------------------------------------------------
# Routes — registry mutation
# ---------------------------------------------------------------------------


@router.post(
    "/hosts",
    status_code=201,
    response_model=AddHostResponse,
    responses=_ERROR_RESPONSES,
)
def add_host(request: Request, body: AddHostRequest, background_tasks: BackgroundTasks):
    """Register an SSH host via the embedded `remo add` (sync, sub-second)."""
    settings = get_settings(request)
    guard = _mount_guard(settings)
    if guard is not None:
        return guard

    # First add from `unconfigured` flips the deployment to adopted — the
    # service needs an identity to authorize before anything can verify.
    try:
        identity = ensure_service_identity(settings)
    except ServiceIdentityError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    argv = ["remo", "add", "--yes"]
    if body.user:
        argv += ["--user", body.user]
    if body.port:
        argv += ["--port", str(body.port)]
    # `--` terminates option parsing: a dash-leading name/target must reach
    # the CLI's own validators (rc 2 -> 400) instead of being read as an
    # option — `remo add --help ...` would "succeed" with rc 0.
    argv += ["--", body.name, body.target]
    # No --verify: reachability is the explicit verify step of the wizard.
    with _write_lock(request):
        # `remo add --yes` UPDATES an existing same-name ssh entry in place
        # (rc 0) — the CLI's rc 1 only means a cross-type collision. The
        # console's contract is add-only, so refuse the duplicate here.
        for host in registry.read_registry(readonly=True).hosts:
            if host.type == "ssh" and host.name == body.name:
                return error_envelope(
                    409,
                    "name_conflict",
                    f"'{body.name}' is already registered.",
                    remediation="Pick a different name, or remove the "
                    "existing entry first.",
                    retryable=False,
                )
        rc, stderr_tail = _run_cli(argv, _CLI_TIMEOUT_S)
        if rc == 2:
            return error_envelope(
                400,
                "invalid_target",
                stderr_tail or "The name or target was rejected.",
                remediation="Correct the name/target and retry.",
                retryable=False,
            )
        if rc == 1:
            return error_envelope(
                409,
                "name_conflict",
                stderr_tail or f"'{body.name}' conflicts with an existing entry.",
                remediation="Pick a different name, or remove the existing entry first.",
                retryable=False,
            )
        if rc != 0:
            return error_envelope(
                502,
                "cli_failure",
                stderr_tail or f"`remo add` failed with exit code {rc}.",
                remediation="Check the service logs and retry.",
                retryable=True,
            )

        entry = None
        for host in registry.read_registry(readonly=True).hosts:
            if host.type == "ssh" and host.name == body.name:
                entry = host
                break
        if entry is None:  # pragma: no cover - add succeeded moments ago
            return error_envelope(
                502,
                "cli_failure",
                "The added entry could not be read back from the registry.",
                remediation="Retry the add.",
                retryable=True,
            )

        _record_web_change_locked(request)
    instance_id = derive_instance_id(entry)
    background_tasks.add_task(get_discovery_service(request).refresh, instance_id)
    return AddHostResponse(
        instance_id=instance_id,
        name=entry.name,
        host=entry.host,
        user=entry.user,
        port=entry.ssh_port,
        public_key=identity.public_key,
        authorize_command=build_authorize_command(identity.public_key),
    )


@router.delete(
    "/hosts/{instance_id}",
    response_model=HostRemovedResponse,
    responses=_ERROR_RESPONSES,
)
def remove_host(request: Request, instance_id: str, background_tasks: BackgroundTasks):
    """Deregister a host (local registry only — the machine is never touched)."""
    settings = get_settings(request)
    guard = _mount_guard(settings)
    if guard is not None:
        return guard

    host = _find_registry_host(instance_id)
    if host is None:
        return _unknown_registry_host()
    if host.type != "ssh":
        return _provider_managed(host, "remove")

    with _write_lock(request):
        rc, stderr_tail = _run_cli(
            ["remo", "remove", "--yes", "--", host.name], _CLI_TIMEOUT_S
        )
        if rc not in (0, 1):
            return error_envelope(
                502,
                "cli_failure",
                stderr_tail or f"`remo remove` failed with exit code {rc}.",
                remediation="Check the service logs and retry.",
                retryable=True,
            )
        if rc == 1:
            # rc 1 is the CLI's GENERIC failure code: not-found shares it
            # with a busy/corrupt registry. Idempotent success only if the
            # entry is actually gone — otherwise a real failure would report
            # removed:true while stripping the host's trust lines.
            still_registered = any(
                h.type == "ssh" and h.name == host.name
                for h in registry.read_registry(readonly=True).hosts
            )
            if still_registered:
                return error_envelope(
                    502,
                    "cli_failure",
                    stderr_tail or "`remo remove` failed and the entry is "
                    "still registered.",
                    remediation="Check the service logs and retry.",
                    retryable=True,
                )

        # The trust slice is keyed by host:port, not by entry — two entries
        # (different users, same machine) share it, and stripping the lines
        # while a sibling entry survives would strand that entry unable to
        # verify the host key.
        lookup_key = known_hosts_lookup_key(host.host, host.ssh_port)
        still_shared = any(
            h.type == "ssh"
            and h.name != host.name
            and known_hosts_lookup_key(h.host, h.ssh_port) == lookup_key
            for h in registry.read_registry(readonly=True).hosts
        )
        if not still_shared:
            remove_instance_host_keys(settings.service_known_hosts_path, lookup_key)
        _record_web_change_locked(request)
    # Full refresh: discovery prunes removed instances only on a full pass.
    background_tasks.add_task(get_discovery_service(request).refresh)
    return HostRemovedResponse(name=host.name)


# ---------------------------------------------------------------------------
# Routes — trust bootstrap + verify
# ---------------------------------------------------------------------------


@router.post(
    "/hosts/{instance_id}/scan-key",
    response_model=ScanKeyResponse,
    responses=_ERROR_RESPONSES,
)
def scan_key(request: Request, instance_id: str):
    """Scan the host's SSH keys and classify them against the service trust
    file. Host keys are public — returning them (with fingerprints) is the
    point: the browser shows them for the operator's confirmation."""
    settings = get_settings(request)
    host = _find_registry_host(instance_id)
    if host is None:
        return _unknown_registry_host()

    lines, scan_error = scan_host_keys(host.host, host.ssh_port, timeout=_SCAN_TIMEOUT_S)
    if scan_error is not None:
        return ScanKeyResponse(status="unreachable", detail=scan_error)

    lookup_key = known_hosts_lookup_key(host.host, host.ssh_port)
    status, detail = classify_scanned_keys(
        lines, lookup_key, settings.service_known_hosts_path
    )
    return ScanKeyResponse(
        status=status,
        detail=detail,
        fingerprints=render_fingerprint_list(lines),
        lines=lines,
    )


@router.post(
    "/hosts/{instance_id}/trust-key",
    response_model=TrustKeyResponse,
    responses=_ERROR_RESPONSES,
)
def trust_key(request: Request, instance_id: str, body: TrustKeyRequest):
    """Record the operator-confirmed key lines in the service trust file."""
    settings = get_settings(request)
    guard = _mount_guard(settings)
    if guard is not None:
        return guard
    host = _find_registry_host(instance_id)
    if host is None:
        return _unknown_registry_host()

    lookup_key = known_hosts_lookup_key(host.host, host.ssh_port)
    if not body.lines:
        return error_envelope(
            400,
            "invalid_key_lines",
            "No key lines were provided.",
            remediation="Re-run the scan and confirm its lines.",
            retryable=False,
        )
    for index, line in enumerate(body.lines):
        error = known_hosts_line_error(line)
        if error is None and line.split()[0] != lookup_key:
            # The route can never trust an arbitrary host: every confirmed
            # line must name exactly this instance's lookup key.
            error = f"hosts field {line.split()[0]!r} is not {lookup_key!r}"
        if error is not None:
            return error_envelope(
                400,
                "invalid_key_lines",
                f"lines[{index}]: {error}",
                remediation="Re-run the scan and confirm its lines.",
                retryable=False,
            )

    with _write_lock(request):
        # Under the shared lock: the trust file is part of the state a sync
        # PUT rewrites wholesale, so an unlocked read-modify-write here could
        # silently drop either side's lines. The generation bump makes an
        # in-flight sync 409-and-re-merge (re-fetching these lines) instead
        # of overwriting the operator's confirmation.
        set_instance_host_keys(settings.service_known_hosts_path, lookup_key, body.lines)
        _record_web_change_locked(request)
    return TrustKeyResponse()


@router.post(
    "/hosts/{instance_id}/verify",
    response_model=VerifyHostResponse,
    responses=_ERROR_RESPONSES,
)
def verify_host(request: Request, instance_id: str, background_tasks: BackgroundTasks):
    """Prove the service can SSH-authenticate to the host (before remo-host
    exists there — this is a bare `true`, not a capability probe)."""
    settings = get_settings(request)
    host = _find_registry_host(instance_id)
    if host is None:
        return _unknown_registry_host()

    cmd = build_ssh_base_cmd(
        host,
        control_dir=settings.ssh_control_dir,
        identity_file=settings.ssh_identity_for(host),
        use_registry_identity=False,
        known_hosts_file=settings.ssh_known_hosts_file,
        extra_opts=["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"],
    ) + ["true"]
    returncode, stderr = _run_ssh(cmd, _VERIFY_TIMEOUT_S)

    if returncode == 0:
        # The rail can flip this instance out of `unreachable` right away.
        background_tasks.add_task(get_discovery_service(request).refresh, instance_id)
        return VerifyHostResponse(status="ok", detail="service key accepted")

    tail = stderr.splitlines()[-1] if stderr else f"ssh exited {returncode}"
    if "Host key verification failed" in stderr:
        return VerifyHostResponse(status="host_key_untrusted", detail=tail)
    if "Permission denied" in stderr:
        return VerifyHostResponse(
            status="auth_failed",
            detail="the service key was not accepted — run the authorize "
            "command on the host, then retry",
        )
    return VerifyHostResponse(status="unreachable", detail=tail)


@router.get(
    "/hosts/{instance_id}/authorize-command",
    response_model=AuthorizeCommandResponse,
    responses=_ERROR_RESPONSES,
)
def authorize_command(request: Request, instance_id: str):
    """The paste-one-liner again, for come-back-later screens."""
    settings = get_settings(request)
    host = _find_registry_host(instance_id)
    if host is None:
        return _unknown_registry_host()
    guard = _mount_guard(settings)
    if guard is not None:
        return guard
    try:
        identity = ensure_service_identity(settings)
    except ServiceIdentityError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return AuthorizeCommandResponse(
        public_key=identity.public_key,
        authorize_command=build_authorize_command(identity.public_key),
    )


# ---------------------------------------------------------------------------
# Routes — configure job
# ---------------------------------------------------------------------------


@router.post(
    "/hosts/{instance_id}/configure",
    status_code=202,
    response_model=JobAcceptedResponse,
    responses=_ERROR_RESPONSES,
)
def start_configure(request: Request, instance_id: str, body: ConfigureRequest | None = None):
    """Run the embedded `remo configure NAME` as a detached job (minutes)."""
    settings = get_settings(request)
    guard = _mount_guard(settings)
    if guard is not None:
        return guard
    host = _find_registry_host(instance_id)
    if host is None:
        return _unknown_registry_host()
    if host.type != "ssh":
        return _provider_managed(host, "configure")
    if host.user == "root":
        return error_envelope(
            400,
            "root_user",
            f"'{host.name}' is registered as root@{host.host}. remo configures "
            "the registered account as the workspace user (UID 1000), which "
            "would break root.",
            remediation="Re-add the host with a normal user.",
            retryable=False,
        )
    if host.ssh_identity:
        from pathlib import Path

        if not Path(host.ssh_identity).expanduser().is_file():
            return error_envelope(
                409,
                "workstation_identity",
                f"'{host.name}' stores an SSH identity path that does not "
                "resolve on the service filesystem.",
                remediation="Configure from the workstation CLI, or re-add "
                "the host without --identity so the service key is used.",
                retryable=False,
            )

    only = list((body.only if body else []) or [])
    skip = list((body.skip if body else []) or [])
    for value in (*only, *skip):
        if not _TOOL_RE.match(value):
            return error_envelope(
                400,
                "invalid_tool",
                f"tool name {value!r} is not valid.",
                remediation="Tool names use [a-z0-9_-] only.",
                retryable=False,
            )

    # -v deliberately: the filtered ansible renderer emits \r progress
    # control characters that garbage a log tail; verbose output is plain.
    argv = ["remo", "configure", "--yes", "-v"]
    for value in only:
        argv += ["--only", value]
    for value in skip:
        argv += ["--skip", value]
    # `--` terminates option parsing: registry names can arrive dash-leading
    # via PUT /setup/registry (which only bars control characters), and
    # `remo configure --help ...` would rc-0 as a phantom success.
    argv += ["--", host.name]

    try:
        record = _job_runner(request).start(
            kind="configure",
            instance_id=instance_id,
            instance_name=host.name,
            argv=argv,
        )
    except DuplicateJobError as exc:
        return error_envelope(
            409,
            "job_already_running",
            f"A configure job is already running for '{host.name}' "
            f"(job {exc.job_id}).",
            remediation=f"Poll GET /api/v1/registry/jobs/{exc.job_id} instead.",
            retryable=False,
        )
    return JobAcceptedResponse(job_id=record["job_id"], kind=record["kind"], project="")


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    responses=_ERROR_RESPONSES,
)
def get_registry_job(request: Request, job_id: str):
    """Poll a registry-admin job (wire-identical to host_admin's job status)."""
    record = _job_runner(request).status(job_id)
    if record is None:
        return error_envelope(
            404,
            "unknown_job",
            "The named job does not exist on this service.",
            remediation="Re-list the instance's jobs and use a current id.",
            retryable=False,
        )
    try:
        state = JobState(str(record.get("state", "")))
    except ValueError:
        state = JobState.FAILED
    return JobStatusResponse(
        state=state,
        exit_code=record.get("exit_code"),
        started_at=str(record.get("started_at", "")),
        finished_at=str(record.get("finished_at", "")),
        log_tail=str(record.get("log_tail", "")),
    )


@router.get(
    "/hosts/{instance_id}/jobs",
    response_model=JobListResponse,
    responses=_ERROR_RESPONSES,
)
def list_registry_jobs(request: Request, instance_id: str):
    """This instance's jobs, newest-first (re-attach after reload/restart)."""
    jobs = _job_runner(request).list_jobs(instance_id)
    return JobListResponse(
        jobs=[
            JobSummary(
                job_id=str(record.get("job_id", "")),
                kind=str(record.get("kind", "")),
                state=str(record.get("state", "")),
                started_at=str(record.get("started_at", "")),
                finished_at=str(record.get("finished_at", "")),
            )
            for record in jobs
        ]
    )
