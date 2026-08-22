"""Host-admin API tests (host-detail feature, plan §2.3/§2.6): dormancy,
operator-auth gating, the four maintenance routes, the shared error mapping,
and the ungated `/stats` endpoint with its TTL coalescing cache.

The dormancy assertions compare the gated routes' 404 against a GET of a
truly unknown route **byte-for-byte** (status AND body): the gate must be
indistinguishable from the route not existing, exactly like `/setup`'s
pairing dormancy. remo-host round trips are monkeypatched at the typed
wrapper seam (`web.api.host_admin` / `web.api.hosts` module globals), so no
SSH is ever spawned.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from remo_cli.core.errors import PreconditionError
from remo_cli.core.remo_host_client import (
    RemoHostCommandError,
    SshTransportError,
)
from remo_cli.models.capability import RemoteCapability
from remo_cli.models.discovery import DiscoverySnapshot, InstanceStatus
from remo_cli.models.host import KnownHost
from remo_cli.models.host_job import JobRef, JobState, JobStatus
from remo_cli.models.host_stats import DiskUsage, HostStats, TempReading
from remo_cli.web import app as app_module
from remo_cli.web.api import host_admin as host_admin_module
from remo_cli.web.api import hosts as hosts_module
from remo_cli.web.config import WebSettings

_ORIGIN = "http://testserver"
_HEADERS = {"Origin": _ORIGIN}

_INSTANCE_ID = "inst-abc"
_ALL_OPS = [
    "sessions.list",
    "sessions.attach",
    "host.stats",
    "projects.clone",
    "projects.delete",
    "projects.rebuild",
    "jobs.status",
]

#: (method, path) for every gated maintenance route.
_GATED_ROUTES = [
    ("POST", f"/api/v1/hosts/{_INSTANCE_ID}/projects"),
    ("DELETE", f"/api/v1/hosts/{_INSTANCE_ID}/projects/demo"),
    ("POST", f"/api/v1/hosts/{_INSTANCE_ID}/projects/demo/rebuild"),
    ("GET", f"/api/v1/hosts/{_INSTANCE_ID}/jobs/job-1"),
]


def _settings(**overrides) -> WebSettings:
    return WebSettings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[_ORIGIN],
        ssh_control_dir="/tmp/remo-ssh-test",
        **overrides,
    )


def _capability(operations: list[str] | None = None) -> RemoteCapability:
    return RemoteCapability(
        protocol_version=1,
        host_tools_version="9.9.9",
        projects_root="/home/remo/projects",
        operations=_ALL_OPS if operations is None else operations,
        zellij=True,
        docker=True,
    )


class _StubDiscovery:
    """Fixed one-instance discovery cache + a refresh-call recorder."""

    def __init__(self, snapshot: DiscoverySnapshot | None, host: KnownHost | None) -> None:
        self._snapshot = snapshot
        self._host = host
        self.refresh_calls: list[str | None] = []

    def find_instance(self, instance_id: str):
        if self._snapshot is not None and instance_id == self._snapshot.instance_id:
            return self._snapshot
        return None

    def find_target(self, target_id: str):
        return None

    def find_host(self, instance_type: str, instance_name: str):
        if (
            self._host is not None
            and instance_type == self._host.type
            and instance_name == self._host.name
        ):
            return self._host
        return None

    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        self.refresh_calls.append(instance_id)


def _known_host() -> KnownHost:
    return KnownHost(type="incus", name="dev", host="127.0.0.1", user="remo")


def _snapshot(capability: RemoteCapability | None) -> DiscoverySnapshot:
    return DiscoverySnapshot(
        instance_id=_INSTANCE_ID,
        instance_type="incus",
        instance_name="dev",
        status=InstanceStatus.OK if capability else InstanceStatus.NO_REMO_HOST,
        capability=capability,
    )


def _make_client(
    monkeypatch,
    *,
    host_admin: str = "enabled",
    operations: list[str] | None = None,
    capability: bool = True,
    operator_auth: str = "",
    forward_auth_header: str = "",
) -> TestClient:
    settings = _settings(
        host_admin=host_admin,
        operator_auth=operator_auth,
        forward_auth_header=forward_auth_header,
    )
    application = app_module.create_app(settings)
    cap = _capability(operations) if capability else None
    application.state.discovery_service = _StubDiscovery(_snapshot(cap), _known_host())
    # Never build a real SSH prefix in unit tests (it consults REMO_HOME /
    # adoption state); the wrapper seam below is what each test drives.
    monkeypatch.setattr(host_admin_module, "build_service_ssh_prefix", lambda h, s: ["ssh"])
    monkeypatch.setattr(hosts_module, "build_service_ssh_prefix", lambda h, s: ["ssh"])
    return TestClient(application, base_url=_ORIGIN)


def _request(client: TestClient, method: str, path: str, **kwargs):
    kwargs.setdefault("headers", _HEADERS)
    if method in ("POST", "PUT") and "json" not in kwargs:
        kwargs["json"] = {}
    return client.request(method, path, **kwargs)


# ---------------------------------------------------------------------------
# Dormancy: gate off / operator-auth refused == unknown route, byte-for-byte
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), _GATED_ROUTES)
def test_disabled_gate_is_byte_identical_to_unknown_route(monkeypatch, method, path):
    with _make_client(monkeypatch, host_admin="") as client:
        unknown = client.get("/api/v1/definitely-not-a-route", headers=_HEADERS)
        assert unknown.status_code == 404

        gated = _request(client, method, path)
        assert gated.status_code == unknown.status_code
        assert gated.content == unknown.content


@pytest.mark.parametrize(("method", "path"), _GATED_ROUTES)
def test_forward_auth_missing_header_is_same_404(monkeypatch, method, path):
    with _make_client(
        monkeypatch,
        operator_auth="forward",
        forward_auth_header="X-Remote-User",
    ) as client:
        unknown = client.get("/api/v1/definitely-not-a-route", headers=_HEADERS)
        gated = _request(client, method, path)
        assert gated.status_code == 404
        assert gated.content == unknown.content


def test_forward_auth_present_header_reaches_route(monkeypatch):
    with _make_client(
        monkeypatch,
        operator_auth="forward",
        forward_auth_header="X-Remote-User",
    ) as client:
        monkeypatch.setattr(
            host_admin_module,
            "get_job_status",
            lambda prefix, job_id, **kw: JobStatus(state=JobState.RUNNING),
        )
        resp = client.get(
            f"/api/v1/hosts/{_INSTANCE_ID}/jobs/job-1",
            headers={**_HEADERS, "X-Remote-User": "paul"},
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "running"


# ---------------------------------------------------------------------------
# Happy paths (wrappers monkeypatched at the module seam)
# ---------------------------------------------------------------------------


def test_clone_happy_path_202(monkeypatch):
    calls = []

    def fake_clone(prefix, repo, *, name=None, **kw):
        calls.append((repo, name))
        return JobRef(job_id="job-42", kind="clone", project="widget")

    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(host_admin_module, "start_project_clone", fake_clone)
        resp = client.post(
            f"/api/v1/hosts/{_INSTANCE_ID}/projects",
            json={"repo": "octocat/widget"},
            headers=_HEADERS,
        )
    assert resp.status_code == 202
    assert resp.json() == {"job_id": "job-42", "kind": "clone", "project": "widget"}
    assert calls == [("octocat/widget", None)]


def test_delete_happy_path_200_and_schedules_refresh(monkeypatch):
    calls = []

    def fake_delete(prefix, project, **kw):
        calls.append(project)
        return None

    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(host_admin_module, "delete_project", fake_delete)
        resp = client.delete(
            f"/api/v1/hosts/{_INSTANCE_ID}/projects/demo", headers=_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json() == {"project": "demo", "deleted": True}
        assert calls == ["demo"]
        # BackgroundTasks ran after the response: the instance was refreshed.
        assert client.app.state.discovery_service.refresh_calls[-1] == _INSTANCE_ID


def test_rebuild_happy_path_202_passes_no_cache(monkeypatch):
    calls = []

    def fake_rebuild(prefix, project, *, no_cache=False, **kw):
        calls.append((project, no_cache))
        return JobRef(job_id="job-7", kind="rebuild", project=project)

    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(host_admin_module, "start_project_rebuild", fake_rebuild)
        resp = client.post(
            f"/api/v1/hosts/{_INSTANCE_ID}/projects/demo/rebuild",
            json={"no_cache": True},
            headers=_HEADERS,
        )
    assert resp.status_code == 202
    assert resp.json()["job_id"] == "job-7"
    assert calls == [("demo", True)]


def test_job_status_happy_path_200(monkeypatch):
    status = JobStatus(
        state=JobState.SUCCEEDED,
        exit_code=0,
        started_at="2026-08-20T00:00:00Z",
        finished_at="2026-08-20T00:01:00Z",
        log_tail="done\n",
    )
    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(
            host_admin_module, "get_job_status", lambda prefix, job_id, **kw: status
        )
        resp = client.get(f"/api/v1/hosts/{_INSTANCE_ID}/jobs/job-1", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {
        "state": "succeeded",
        "exit_code": 0,
        "started_at": "2026-08-20T00:00:00Z",
        "finished_at": "2026-08-20T00:01:00Z",
        "log_tail": "done\n",
    }


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def test_unknown_instance_is_404_envelope(monkeypatch):
    with _make_client(monkeypatch) as client:
        resp = client.get("/api/v1/hosts/not-an-instance/jobs/job-1", headers=_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_instance"


def test_operation_missing_from_capability_is_409(monkeypatch):
    with _make_client(monkeypatch, operations=["sessions.list"]) as client:
        resp = client.post(
            f"/api/v1/hosts/{_INSTANCE_ID}/projects",
            json={"repo": "octocat/widget"},
            headers=_HEADERS,
        )
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "unsupported_host_tools"
    # The remediation names the per-type command, not a vague "re-configure".
    assert "remo incus upgrade dev" in body["remediation"]


def test_missing_capability_is_409(monkeypatch):
    with _make_client(monkeypatch, capability=False) as client:
        resp = client.get(f"/api/v1/hosts/{_INSTANCE_ID}/jobs/job-1", headers=_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "unsupported_host_tools"


@pytest.mark.parametrize("exit_code", [2, 4])
def test_remote_exit_2_and_4_map_to_409(monkeypatch, exit_code):
    def fake_delete(prefix, project, **kw):
        raise RemoHostCommandError(exit_code, "unknown verb", verb="projects delete")

    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(host_admin_module, "delete_project", fake_delete)
        resp = client.delete(
            f"/api/v1/hosts/{_INSTANCE_ID}/projects/demo", headers=_HEADERS
        )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "unsupported_host_tools"


def test_remote_exit_3_on_delete_is_unknown_project(monkeypatch):
    def fake_delete(prefix, project, **kw):
        raise RemoHostCommandError(3, "no such project", verb="projects delete")

    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(host_admin_module, "delete_project", fake_delete)
        resp = client.delete(
            f"/api/v1/hosts/{_INSTANCE_ID}/projects/demo", headers=_HEADERS
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_project"


def test_remote_exit_3_on_jobs_is_unknown_job(monkeypatch):
    def fake_status(prefix, job_id, **kw):
        raise RemoHostCommandError(3, "no such job", verb="jobs status")

    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(host_admin_module, "get_job_status", fake_status)
        resp = client.get(f"/api/v1/hosts/{_INSTANCE_ID}/jobs/job-1", headers=_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_job"


def test_precondition_error_is_400(monkeypatch):
    def fake_clone(prefix, repo, **kw):
        raise PreconditionError("Invalid repository '../evil'")

    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(host_admin_module, "start_project_clone", fake_clone)
        resp = client.post(
            f"/api/v1/hosts/{_INSTANCE_ID}/projects",
            json={"repo": "../evil"},
            headers=_HEADERS,
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_transport_error_is_502(monkeypatch):
    def fake_rebuild(prefix, project, **kw):
        raise SshTransportError("ssh: connect to host 127.0.0.1: Connection refused")

    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(host_admin_module, "start_project_rebuild", fake_rebuild)
        resp = client.post(
            f"/api/v1/hosts/{_INSTANCE_ID}/projects/demo/rebuild",
            json={},
            headers=_HEADERS,
        )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "ssh_transport"


# ---------------------------------------------------------------------------
# GET /hosts/{id}/stats — ungated, TTL-coalesced
# ---------------------------------------------------------------------------


def _stats() -> HostStats:
    return HostStats(
        uptime_s=3600.0,
        load_1=0.5,
        load_5=0.4,
        load_15=0.3,
        cpu_count=8,
        cpu_used_pct=12.5,
        mem_total=16_000_000_000,
        mem_used=4_000_000_000,
        mem_available=11_000_000_000,
        swap_total=0,
        swap_used=0,
        disks=[DiskUsage(mount="/", size_bytes=100, used_bytes=40, avail_bytes=60)],
        temps=[TempReading(name="coretemp", label="Package id 0", celsius=41.0)],
    )


def test_stats_reachable_with_gate_off(monkeypatch):
    with _make_client(monkeypatch, host_admin="") as client:
        monkeypatch.setattr(hosts_module, "get_host_stats", lambda prefix, **kw: _stats())
        resp = client.get(f"/api/v1/hosts/{_INSTANCE_ID}/stats", headers=_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["cpu_count"] == 8
    assert body["disks"] == [
        {"mount": "/", "size_bytes": 100, "used_bytes": 40, "avail_bytes": 60}
    ]
    assert body["temps"] == [{"name": "coretemp", "label": "Package id 0", "celsius": 41.0}]


def test_stats_ttl_coalesces_to_one_client_call(monkeypatch):
    calls = []

    def fake_stats(prefix, **kw):
        calls.append(prefix)
        return _stats()

    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(hosts_module, "get_host_stats", fake_stats)
        first = client.get(f"/api/v1/hosts/{_INSTANCE_ID}/stats", headers=_HEADERS)
        second = client.get(f"/api/v1/hosts/{_INSTANCE_ID}/stats", headers=_HEADERS)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1  # the second call was served from the TTL cache


def test_stats_unknown_instance_is_404(monkeypatch):
    with _make_client(monkeypatch) as client:
        resp = client.get("/api/v1/hosts/not-an-instance/stats", headers=_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_instance"


def test_stats_without_host_stats_operation_is_409(monkeypatch):
    with _make_client(monkeypatch, operations=["sessions.list"]) as client:
        resp = client.get(f"/api/v1/hosts/{_INSTANCE_ID}/stats", headers=_HEADERS)
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "unsupported_host_tools"
    assert "remo incus upgrade dev" in body["remediation"]


def test_stats_remote_exit_2_maps_to_409(monkeypatch):
    """An old host answering the verb with exit 2 (unknown top-level verb) is
    'host tools outdated', reachable mid-upgrade when operations[] already
    advertises host.stats but the deployed script predates it."""

    def fake_stats(prefix, **kw):
        raise RemoHostCommandError(2, "unknown verb", verb="host stats")

    with _make_client(monkeypatch) as client:
        monkeypatch.setattr(hosts_module, "get_host_stats", fake_stats)
        resp = client.get(f"/api/v1/hosts/{_INSTANCE_ID}/stats", headers=_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "unsupported_host_tools"


# ---------------------------------------------------------------------------
# Config fail-fast (plan §2.3: REMO_WEB_HOST_ADMIN / REMO_WEB_HOST_STATS_TTL_S)
# ---------------------------------------------------------------------------


def test_invalid_host_admin_value_fails_fast(monkeypatch):
    from remo_cli.web.config import WebConfigError

    monkeypatch.setenv("REMO_WEB_HOST_ADMIN", "yes-please")
    with pytest.raises(WebConfigError):
        WebSettings()


def test_nonpositive_stats_ttl_fails_fast(monkeypatch):
    from remo_cli.web.config import WebConfigError

    monkeypatch.setenv("REMO_WEB_HOST_STATS_TTL_S", "0")
    with pytest.raises(WebConfigError):
        WebSettings()
