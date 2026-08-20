"""Registry-admin API tests (023): dormancy, gating, the nine routes.

Template: tests/unit/web/test_host_admin_api.py — dormancy compared
byte-for-byte against a truly unknown route; the embedded-CLI calls are
monkeypatched at the `_run_cli` seam (no subprocess ever runs `remo`), the
verify SSH at `_run_ssh`, and the scan machinery at the module's imported
names. The registry itself is REAL (the `state_dir` temp REMO_HOME): these
routes deliberately re-read it instead of the discovery cache.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from remo_cli.web import app as app_module
from remo_cli.web.api import registry_admin as ra_module

_ORIGIN = "http://testserver"
_HEADERS = {"Origin": _ORIGIN}

_VALID_KEY_LINE = "10.0.0.9 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFixtureKeyMaterial0000"


def _iid(type_: str, name: str) -> str:
    return hashlib.sha256(f"{type_}\x1f{name}".encode()).hexdigest()[:32]


_SSH_ID = _iid("ssh", "mbp")
_INCUS_ID = _iid("incus", "dev")

#: (method, path) for every gated registry-admin route.
_GATED_ROUTES = [
    ("POST", "/api/v1/registry/hosts"),
    ("DELETE", f"/api/v1/registry/hosts/{_SSH_ID}"),
    ("POST", f"/api/v1/registry/hosts/{_SSH_ID}/scan-key"),
    ("POST", f"/api/v1/registry/hosts/{_SSH_ID}/trust-key"),
    ("POST", f"/api/v1/registry/hosts/{_SSH_ID}/verify"),
    ("GET", f"/api/v1/registry/hosts/{_SSH_ID}/authorize-command"),
    ("POST", f"/api/v1/registry/hosts/{_SSH_ID}/configure"),
    ("GET", "/api/v1/registry/jobs/configure-abc123"),
    ("GET", f"/api/v1/registry/hosts/{_SSH_ID}/jobs"),
]


class _NoopDiscovery:
    def __init__(self) -> None:
        self.refresh_calls: list[str | None] = []

    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        self.refresh_calls.append(instance_id)


def _make_client(
    state_dir,
    *,
    registry_admin: str = "enabled",
    operator_auth: str = "",
    forward_auth_header: str = "",
) -> TestClient:
    settings = state_dir.settings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[_ORIGIN],
        registry_admin=registry_admin,
        operator_auth=operator_auth,
        forward_auth_header=forward_auth_header,
    )
    application = app_module.create_app(settings)
    application.state.discovery_service = _NoopDiscovery()
    return TestClient(application, base_url=_ORIGIN)


def _request(client: TestClient, method: str, path: str, **kwargs):
    kwargs.setdefault("headers", _HEADERS)
    if method in ("POST", "PUT") and "json" not in kwargs:
        kwargs["json"] = {}
    return client.request(method, path, **kwargs)


def _seed_ssh_host(state_dir, *, port: str = "22", user: str = "paul") -> None:
    state_dir.write_registry([f"ssh:mbp:10.0.0.9:{user}:{port}:direct"])


@pytest.fixture
def adopted(state_dir):
    state_dir.write_keypair()
    state_dir.write_state_json()
    _seed_ssh_host(state_dir)
    return state_dir


# ---------------------------------------------------------------------------
# Dormancy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), _GATED_ROUTES)
def test_disabled_gate_is_byte_identical_to_unknown_route(state_dir, method, path):
    with _make_client(state_dir, registry_admin="") as client:
        unknown = client.get("/api/v1/definitely-not-a-route", headers=_HEADERS)
        assert unknown.status_code == 404
        gated = _request(client, method, path)
        assert gated.status_code == unknown.status_code
        assert gated.content == unknown.content


@pytest.mark.parametrize(("method", "path"), _GATED_ROUTES)
def test_forward_auth_missing_header_is_same_404(state_dir, method, path):
    with _make_client(
        state_dir, operator_auth="forward", forward_auth_header="X-Remote-User"
    ) as client:
        unknown = client.get("/api/v1/definitely-not-a-route", headers=_HEADERS)
        gated = _request(client, method, path)
        assert gated.status_code == 404
        assert gated.content == unknown.content


# ---------------------------------------------------------------------------
# POST /registry/hosts (add)
# ---------------------------------------------------------------------------


class TestAddHost:
    def _fake_add(self, state_dir, rc: int, stderr: str = ""):
        """A _run_cli fake that (on rc 0) also lands the entry, as remo add would."""

        calls: list[list[str]] = []

        def fake(argv, timeout):
            calls.append(argv)
            if rc == 0:
                _seed_ssh_host(state_dir)
            return rc, stderr

        return fake, calls

    def test_add_happy_path(self, state_dir, monkeypatch):
        state_dir.write_keypair()
        state_dir.write_state_json()
        fake, calls = self._fake_add(state_dir, 0)
        monkeypatch.setattr(ra_module, "_run_cli", fake)

        with _make_client(state_dir) as client:
            resp = _request(
                client,
                "POST",
                "/api/v1/registry/hosts",
                json={"name": "mbp", "target": "paul@10.0.0.9"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["instance_id"] == _SSH_ID
        assert body["name"] == "mbp"
        assert body["host"] == "10.0.0.9"
        assert body["user"] == "paul"
        assert body["port"] == 22
        assert body["public_key"].startswith("ssh-ed25519")
        assert "authorized_keys" in body["authorize_command"]
        # argv: no --verify, always --yes.
        assert calls == [["remo", "add", "--yes", "--", "mbp", "paul@10.0.0.9"]]
        # A web-origin generation bump was recorded.
        meta = json.loads((state_dir.web_identity_dir / "mirror-meta.json").read_text())
        assert meta["generation"] == 1
        assert meta["last_change"]["origin"] == "web"

    def test_user_and_port_ride_into_argv(self, state_dir, monkeypatch):
        state_dir.write_keypair()
        state_dir.write_state_json()
        fake, calls = self._fake_add(state_dir, 0)
        monkeypatch.setattr(ra_module, "_run_cli", fake)
        with _make_client(state_dir) as client:
            _request(
                client,
                "POST",
                "/api/v1/registry/hosts",
                json={"name": "mbp", "target": "10.0.0.9", "user": "paul", "port": 2222},
            )
        assert calls[0] == [
            "remo", "add", "--yes", "--user", "paul", "--port", "2222",
            "--", "mbp", "10.0.0.9",
        ]

    def test_duplicate_ssh_name_is_409_before_the_cli_runs(self, adopted, monkeypatch):
        """`remo add --yes` UPDATES an existing same-name ssh entry in place
        (rc 0), so without this pre-check a duplicate add would return 201
        and silently repoint the existing host."""
        monkeypatch.setattr(
            ra_module, "_run_cli", lambda a, t: pytest.fail("CLI must not run")
        )
        with _make_client(adopted) as client:
            resp = _request(
                client,
                "POST",
                "/api/v1/registry/hosts",
                json={"name": "mbp", "target": "other@10.9.9.9"},
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "name_conflict"

    def test_rc1_is_409_name_conflict(self, state_dir, monkeypatch):
        state_dir.write_keypair()
        state_dir.write_state_json()
        monkeypatch.setattr(ra_module, "_run_cli", lambda a, t: (1, "conflicts with hetzner"))
        with _make_client(state_dir) as client:
            resp = _request(
                client, "POST", "/api/v1/registry/hosts",
                json={"name": "web1", "target": "1.2.3.4"},
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "name_conflict"
        assert "conflicts with hetzner" in resp.json()["error"]["message"]

    def test_rc2_is_400_invalid_target(self, state_dir, monkeypatch):
        state_dir.write_keypair()
        state_dir.write_state_json()
        monkeypatch.setattr(ra_module, "_run_cli", lambda a, t: (2, "bad name"))
        with _make_client(state_dir) as client:
            resp = _request(
                client, "POST", "/api/v1/registry/hosts",
                json={"name": "x!", "target": "y"},
            )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_target"

    def test_timeout_is_502_cli_failure(self, state_dir, monkeypatch):
        state_dir.write_keypair()
        state_dir.write_state_json()
        monkeypatch.setattr(ra_module, "_run_cli", lambda a, t: (124, "timed out"))
        with _make_client(state_dir) as client:
            resp = _request(
                client, "POST", "/api/v1/registry/hosts",
                json={"name": "mbp", "target": "10.0.0.9"},
            )
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "cli_failure"

    def test_mount_configured_is_409_read_only(self, state_dir, monkeypatch):
        state_dir.mount_configured()
        monkeypatch.setattr(
            ra_module, "_run_cli",
            lambda a, t: pytest.fail("CLI must not run in mount-configured mode"),
        )
        with _make_client(state_dir) as client:
            resp = _request(
                client, "POST", "/api/v1/registry/hosts",
                json={"name": "mbp", "target": "10.0.0.9"},
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "read_only_deployment"


# ---------------------------------------------------------------------------
# DELETE /registry/hosts/{id}
# ---------------------------------------------------------------------------


class TestRemoveHost:
    def test_remove_happy_path(self, adopted, monkeypatch):
        removed: list[list[str]] = []
        cleanup: list[tuple] = []
        monkeypatch.setattr(ra_module, "_run_cli", lambda a, t: (removed.append(a), (0, ""))[1])
        monkeypatch.setattr(
            ra_module, "remove_instance_host_keys", lambda p, k: cleanup.append((p, k))
        )
        with _make_client(adopted) as client:
            resp = _request(client, "DELETE", f"/api/v1/registry/hosts/{_SSH_ID}")
        assert resp.status_code == 200
        assert resp.json() == {"name": "mbp", "removed": True}
        assert removed == [["remo", "remove", "--yes", "--", "mbp"]]
        # Trust cleanup used the bare-host lookup key (port 22).
        assert cleanup[0][1] == "10.0.0.9"
        meta = json.loads((adopted.web_identity_dir / "mirror-meta.json").read_text())
        assert meta["last_change"]["origin"] == "web"

    def test_lost_race_rc1_is_idempotent_success(self, adopted, monkeypatch):
        def fake_run(argv, timeout):
            # Simulate the race the rc-1 branch is FOR: another writer removed
            # the entry between the route's read and the CLI run.
            adopted.write_registry([])
            return 1, "not found"

        monkeypatch.setattr(ra_module, "_run_cli", fake_run)
        monkeypatch.setattr(ra_module, "remove_instance_host_keys", lambda p, k: None)
        with _make_client(adopted) as client:
            resp = _request(client, "DELETE", f"/api/v1/registry/hosts/{_SSH_ID}")
        assert resp.status_code == 200
        assert resp.json()["removed"] is True

    def test_rc1_with_entry_still_registered_is_502(self, adopted, monkeypatch):
        """rc 1 is the CLI's generic failure code (busy/corrupt registry share
        it with not-found): reporting removed:true while the entry survives
        would also strip the host's trust lines. Regression for the rc-1
        conflation."""
        monkeypatch.setattr(ra_module, "_run_cli", lambda a, t: (1, "registry is busy"))
        monkeypatch.setattr(
            ra_module,
            "remove_instance_host_keys",
            lambda p, k: pytest.fail("trust lines must not be removed on failure"),
        )
        with _make_client(adopted) as client:
            resp = _request(client, "DELETE", f"/api/v1/registry/hosts/{_SSH_ID}")
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "cli_failure"

    def test_provider_host_is_409_provider_managed(self, state_dir, monkeypatch):
        state_dir.write_keypair()
        state_dir.write_state_json()
        state_dir.write_registry(["incus:dev:127.0.0.1:remo"])
        monkeypatch.setattr(
            ra_module, "_run_cli", lambda a, t: pytest.fail("CLI must not run")
        )
        with _make_client(state_dir) as client:
            resp = _request(client, "DELETE", f"/api/v1/registry/hosts/{_INCUS_ID}")
        assert resp.status_code == 409
        body = resp.json()["error"]
        assert body["code"] == "provider_managed"
        assert "remo incus destroy dev" in body["remediation"]

    def test_unknown_instance_is_404(self, adopted):
        with _make_client(adopted) as client:
            resp = _request(client, "DELETE", "/api/v1/registry/hosts/" + "0" * 32)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "unknown_instance"


# ---------------------------------------------------------------------------
# scan-key / trust-key / verify / authorize-command
# ---------------------------------------------------------------------------


class TestScanKey:
    def test_scan_returns_lines_and_fingerprints(self, adopted, monkeypatch):
        monkeypatch.setattr(
            ra_module, "scan_host_keys", lambda h, p, timeout: ([_VALID_KEY_LINE], None)
        )
        monkeypatch.setattr(
            ra_module,
            "classify_scanned_keys",
            lambda lines, key, path: ("no_trust", f"no trusted host key for {key}"),
        )
        monkeypatch.setattr(
            ra_module, "render_fingerprint_list", lambda lines: ["256 SHA256:abc (ED25519)"]
        )
        with _make_client(adopted) as client:
            resp = _request(client, "POST", f"/api/v1/registry/hosts/{_SSH_ID}/scan-key")
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "no_trust",
            "detail": "no trusted host key for 10.0.0.9",
            "fingerprints": ["256 SHA256:abc (ED25519)"],
            "lines": [_VALID_KEY_LINE],
        }

    def test_scan_failure_is_unreachable(self, adopted, monkeypatch):
        monkeypatch.setattr(
            ra_module, "scan_host_keys", lambda h, p, timeout: ([], "scan timed out")
        )
        with _make_client(adopted) as client:
            resp = _request(client, "POST", f"/api/v1/registry/hosts/{_SSH_ID}/scan-key")
        assert resp.json()["status"] == "unreachable"
        assert resp.json()["fingerprints"] == []


class TestTrustKey:
    def test_confirmed_lines_are_recorded(self, adopted):
        with _make_client(adopted) as client:
            resp = _request(
                client,
                "POST",
                f"/api/v1/registry/hosts/{_SSH_ID}/trust-key",
                json={"lines": [_VALID_KEY_LINE]},
            )
        assert resp.status_code == 200
        assert resp.json() == {"trusted": True}
        content = (adopted.web_identity_dir / "known_hosts").read_text()
        assert _VALID_KEY_LINE in content

    def test_line_for_a_different_host_is_rejected(self, adopted):
        wrong = _VALID_KEY_LINE.replace("10.0.0.9", "10.9.9.9")
        with _make_client(adopted) as client:
            resp = _request(
                client,
                "POST",
                f"/api/v1/registry/hosts/{_SSH_ID}/trust-key",
                json={"lines": [wrong]},
            )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_key_lines"
        assert not (adopted.web_identity_dir / "known_hosts").exists()

    def test_structurally_invalid_line_is_rejected(self, adopted):
        with _make_client(adopted) as client:
            resp = _request(
                client,
                "POST",
                f"/api/v1/registry/hosts/{_SSH_ID}/trust-key",
                json={"lines": ["10.0.0.9 nonsense AAAA"]},
            )
        assert resp.status_code == 400

    def test_empty_lines_are_rejected(self, adopted):
        with _make_client(adopted) as client:
            resp = _request(
                client,
                "POST",
                f"/api/v1/registry/hosts/{_SSH_ID}/trust-key",
                json={"lines": []},
            )
        assert resp.status_code == 400


class TestVerifyHost:
    @pytest.mark.parametrize(
        ("rc", "stderr", "status"),
        [
            (0, "", "ok"),
            (255, "x\nHost key verification failed.", "host_key_untrusted"),
            (255, "paul@10.0.0.9: Permission denied (publickey).", "auth_failed"),
            (255, "ssh: connect to host 10.0.0.9 port 22: timed out", "unreachable"),
        ],
    )
    def test_status_mapping(self, adopted, monkeypatch, rc, stderr, status):
        monkeypatch.setattr(ra_module, "_run_ssh", lambda cmd, timeout: (rc, stderr))
        with _make_client(adopted) as client:
            resp = _request(client, "POST", f"/api/v1/registry/hosts/{_SSH_ID}/verify")
        assert resp.status_code == 200
        assert resp.json()["status"] == status

    def test_ok_triggers_targeted_refresh(self, adopted, monkeypatch):
        monkeypatch.setattr(ra_module, "_run_ssh", lambda cmd, timeout: (0, ""))
        with _make_client(adopted) as client:
            _request(client, "POST", f"/api/v1/registry/hosts/{_SSH_ID}/verify")
            discovery = client.app.state.discovery_service
        # The lifespan's initial refresh(None) also lands in the recorder.
        assert _SSH_ID in discovery.refresh_calls


class TestAuthorizeCommand:
    def test_returns_key_and_one_liner(self, adopted):
        with _make_client(adopted) as client:
            resp = _request(
                client, "GET", f"/api/v1/registry/hosts/{_SSH_ID}/authorize-command"
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["public_key"].startswith("ssh-ed25519")
        assert "authorized_keys" in body["authorize_command"]


# ---------------------------------------------------------------------------
# configure + jobs
# ---------------------------------------------------------------------------


class _StubRunner:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.records: dict[str, dict] = {}
        self.raise_duplicate: str | None = None

    def start(self, **kwargs):
        if self.raise_duplicate:
            raise ra_module.DuplicateJobError(self.raise_duplicate)
        self.started.append(kwargs)
        record = {"job_id": "configure-abc123def456", "kind": kwargs["kind"]}
        return record

    def status(self, job_id):
        return self.records.get(job_id)

    def list_jobs(self, instance_id):
        return [r for r in self.records.values() if r.get("instance_id") == instance_id]


@pytest.fixture
def stub_runner():
    return _StubRunner()


def _client_with_runner(state_dir, runner) -> TestClient:
    client = _make_client(state_dir)
    client.app.state.cli_job_runner = runner
    return client


class TestConfigure:
    def test_configure_202_with_job_id(self, adopted, stub_runner):
        with _client_with_runner(adopted, stub_runner) as client:
            resp = _request(
                client,
                "POST",
                f"/api/v1/registry/hosts/{_SSH_ID}/configure",
                json={"only": ["docker"], "skip": ["zellij"]},
            )
        assert resp.status_code == 202
        assert resp.json() == {
            "job_id": "configure-abc123def456",
            "kind": "configure",
            "project": "",
        }
        assert stub_runner.started[0]["argv"] == [
            "remo", "configure", "--yes", "-v",
            "--only", "docker", "--skip", "zellij", "--", "mbp",
        ]

    def test_duplicate_job_is_409_with_existing_id(self, adopted, stub_runner):
        stub_runner.raise_duplicate = "configure-existing00"
        with _client_with_runner(adopted, stub_runner) as client:
            resp = _request(client, "POST", f"/api/v1/registry/hosts/{_SSH_ID}/configure")
        assert resp.status_code == 409
        body = resp.json()["error"]
        assert body["code"] == "job_already_running"
        assert "configure-existing00" in body["remediation"]

    def test_root_user_is_400(self, state_dir, stub_runner):
        state_dir.write_keypair()
        state_dir.write_state_json()
        _seed_ssh_host(state_dir, user="root")
        with _client_with_runner(state_dir, stub_runner) as client:
            resp = _request(client, "POST", f"/api/v1/registry/hosts/{_SSH_ID}/configure")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "root_user"
        assert stub_runner.started == []

    def test_workstation_identity_is_409(self, state_dir, stub_runner):
        state_dir.write_keypair()
        state_dir.write_state_json()
        state_dir.write_registry(
            ["ssh:mbp:10.0.0.9:paul:22:direct:/home/paul/.ssh/nonexistent_key"]
        )
        with _client_with_runner(state_dir, stub_runner) as client:
            resp = _request(client, "POST", f"/api/v1/registry/hosts/{_SSH_ID}/configure")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "workstation_identity"

    def test_provider_host_is_409(self, state_dir, stub_runner):
        state_dir.write_keypair()
        state_dir.write_state_json()
        state_dir.write_registry(["incus:dev:127.0.0.1:remo"])
        with _client_with_runner(state_dir, stub_runner) as client:
            resp = _request(client, "POST", f"/api/v1/registry/hosts/{_INCUS_ID}/configure")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "provider_managed"

    def test_bad_tool_name_is_400(self, adopted, stub_runner):
        with _client_with_runner(adopted, stub_runner) as client:
            resp = _request(
                client,
                "POST",
                f"/api/v1/registry/hosts/{_SSH_ID}/configure",
                json={"only": ["docker; rm -rf /"]},
            )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_tool"
        assert stub_runner.started == []


class TestJobRoutes:
    def test_job_status_shape(self, adopted, stub_runner):
        stub_runner.records["configure-abc123def456"] = {
            "job_id": "configure-abc123def456",
            "kind": "configure",
            "instance_id": _SSH_ID,
            "state": "succeeded",
            "exit_code": 0,
            "started_at": "2026-08-20T00:00:00+00:00",
            "finished_at": "2026-08-20T00:05:00+00:00",
            "log_tail": "PLAY RECAP ...",
        }
        with _client_with_runner(adopted, stub_runner) as client:
            resp = _request(client, "GET", "/api/v1/registry/jobs/configure-abc123def456")
        assert resp.status_code == 200
        assert resp.json() == {
            "state": "succeeded",
            "exit_code": 0,
            "started_at": "2026-08-20T00:00:00+00:00",
            "finished_at": "2026-08-20T00:05:00+00:00",
            "log_tail": "PLAY RECAP ...",
        }

    def test_unknown_job_is_404(self, adopted, stub_runner):
        with _client_with_runner(adopted, stub_runner) as client:
            resp = _request(client, "GET", "/api/v1/registry/jobs/configure-nope")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "unknown_job"

    def test_job_list_is_per_instance(self, adopted, stub_runner):
        stub_runner.records["a"] = {
            "job_id": "a", "kind": "configure", "instance_id": _SSH_ID,
            "state": "running", "started_at": "t1", "finished_at": "",
        }
        stub_runner.records["b"] = {
            "job_id": "b", "kind": "configure", "instance_id": "other",
            "state": "running", "started_at": "t2", "finished_at": "",
        }
        with _client_with_runner(adopted, stub_runner) as client:
            resp = _request(client, "GET", f"/api/v1/registry/hosts/{_SSH_ID}/jobs")
        assert resp.status_code == 200
        assert resp.json() == {
            "jobs": [
                {
                    "job_id": "a",
                    "kind": "configure",
                    "state": "running",
                    "started_at": "t1",
                    "finished_at": "",
                }
            ]
        }
