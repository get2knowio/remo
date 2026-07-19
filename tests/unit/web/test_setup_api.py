"""Setup API tests (011-web-adopt T025) — `/api/v1/setup/*` via TestClient.

Asserts the normative wire contract in specs/011-web-adopt/contracts/setup-api.md
against `remo_cli.web.api.setup`, using the `state_dir` factory from
tests/unit/web/conftest.py for each configuration-state layout.

Conventions:
- State-changing requests (PUT/POST) must carry an allowed ``Origin`` header
  to pass the app-wide origin middleware; GETs are exempt.
- Malformed bodies return the contract's ``{"reason": "invalid_payload",
  "detail": ...}`` shape (a string detail), never FastAPI's default 422 body.
- The exhaustive auth matrix lives in a later task; here we only prove the
  four routes inherit the router-level token dependency (404 unset / 401 wrong).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from remo_cli.web import app as app_module
from remo_cli.web import check as web_check_module
from remo_cli.web.api import setup as setup_api

_ORIGIN = "http://testserver"
_TOKEN = "unit-test-setup-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}", "Origin": _ORIGIN}

#: Structurally valid known_hosts line per setup.py's line validator.
_VALID_KEY_LINE = "10.0.0.5 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFixtureKeyMaterial0000"

_SETUP_ROUTES = [
    ("GET", "/api/v1/setup/status"),
    ("GET", "/api/v1/setup/identity"),
    ("PUT", "/api/v1/setup/registry"),
    ("POST", "/api/v1/setup/verify"),
]


class _NoopDiscovery:
    """Stops the app lifespan's initial discovery from opening real SSH."""

    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        return None


def _inject_session(application, code: str = _TOKEN) -> None:
    """Directly install a live pairing session with a KNOWN code (012).

    Reaches into the in-memory manager so the many ``_AUTH`` (Bearer _TOKEN)
    call sites below keep working without minting a random code per test. The
    huge ttl means it never idle-expires mid-test.
    """
    import time

    from remo_cli.web.pairing import PairingSession

    application.state.pairing_manager._session = PairingSession(
        code=code, identity=None, origin="adopt", last_activity=time.monotonic(), ttl_s=1e9
    )


def _client(state_dir, *, live: bool = True) -> TestClient:
    settings = state_dir.settings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[_ORIGIN],
        operator_auth="none",
    )
    application = app_module.create_app(settings)
    application.state.discovery_service = _NoopDiscovery()
    if live:
        _inject_session(application)
    return TestClient(application, base_url=_ORIGIN)


def _payload(**overrides: Any) -> dict[str, Any]:
    """A valid AdoptionPayload: 2 registry entries, host keys for 1 of them."""
    payload: dict[str, Any] = {
        "version": 1,
        "registry": [
            {"type": "incus", "name": "dev", "host": "10.0.0.5", "user": "remo"},
            {
                "type": "aws",
                "name": "cloud",
                "host": "3.4.5.6",
                "user": "remo",
                "instance_id": "i-0abc",
                "access_mode": "ssm",
                "region": "us-east-1",
            },
        ],
        "host_keys": {"dev": [_VALID_KEY_LINE]},
    }
    payload.update(overrides)
    return payload


_EXPECTED_REGISTRY_TEXT = "incus:dev:10.0.0.5:remo\naws:cloud:3.4.5.6:remo:i-0abc:ssm:us-east-1\n"


def _service_known_hosts(state_dir):
    return state_dir.web_identity_dir / "known_hosts"


def _assert_nothing_written(state_dir) -> None:
    """FR-019 all-or-nothing: neither target file appears after a rejected PUT."""
    assert not state_dir.registry_path.exists()
    assert not _service_known_hosts(state_dir).exists()


# ---------------------------------------------------------------------------
# GET /api/v1/setup/status
# ---------------------------------------------------------------------------


def test_status_unconfigured_without_identity(state_dir):
    state_dir.unconfigured()
    # No `with` (lifespan skipped): since T030 the app lifespan generates the
    # service identity when unconfigured, which is exactly the pre-identity
    # window this test asserts. tests/unit/web/test_health_states.py covers
    # the lifespan-generation behavior itself.
    client = _client(state_dir)
    resp = client.get("/api/v1/setup/status", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == {
        "state": "unconfigured",
        "deployment_id": None,
        "public_key_available": False,
        "registry_instances": 0,
    }


def test_status_unconfigured_with_identity(state_dir):
    state_dir.write_keypair()
    state_dir.write_state_json()
    with _client(state_dir) as client:
        resp = client.get("/api/v1/setup/status", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == {
        "state": "unconfigured",
        "deployment_id": "dep12345",
        "public_key_available": True,
        "registry_instances": 0,
    }


def test_status_adopted(state_dir):
    state_dir.adopted()
    with _client(state_dir) as client:
        resp = client.get("/api/v1/setup/status", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == {
        "state": "adopted",
        "deployment_id": "dep12345",
        "public_key_available": True,
        "registry_instances": 1,
    }


@pytest.mark.parametrize(
    "layout", ["mount_configured_user_identity", "mount_configured_readonly"]
)
def test_status_mount_configured_has_null_identity(state_dir, layout):
    getattr(state_dir, layout)()
    with _client(state_dir) as client:
        resp = client.get("/api/v1/setup/status", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == {
        "state": "mount_configured",
        "deployment_id": None,
        "public_key_available": False,
        "registry_instances": 1,
    }


def test_status_registry_instances_counts_only_parseable_lines(state_dir):
    state_dir.adopted()
    state_dir.write_registry(
        [
            "incus:dev:127.0.0.1:remo",
            "",
            "not-enough-fields",
            "aws:cloud:3.4.5.6:remo:i-1:ssm:us-east-1",
        ]
    )
    with _client(state_dir) as client:
        resp = client.get("/api/v1/setup/status", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["registry_instances"] == 2


# ---------------------------------------------------------------------------
# GET /api/v1/setup/identity
# ---------------------------------------------------------------------------


def test_identity_generated_on_first_call_when_unconfigured(state_dir):
    state_dir.unconfigured()
    with _client(state_dir) as client:
        resp = client.get("/api/v1/setup/identity", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["deployment_id"]) == 8
    assert body["public_key"].startswith("ssh-ed25519 ")
    assert body["public_key"].endswith(f"remo-web@{body['deployment_id']}")
    # Keypair + state.json materialized on disk by the first call.
    assert state_dir.private_key_path.is_file()
    assert state_dir.public_key_path.is_file()
    assert state_dir.state_json_path.is_file()


def test_identity_stable_across_calls(state_dir):
    state_dir.unconfigured()
    with _client(state_dir) as client:
        first = client.get("/api/v1/setup/identity", headers=_AUTH)
        private_key_bytes = state_dir.private_key_path.read_bytes()
        second = client.get("/api/v1/setup/identity", headers=_AUTH)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    # FR-002: the keypair is never regenerated while the files exist.
    assert state_dir.private_key_path.read_bytes() == private_key_bytes


def test_identity_loads_preseeded_keypair_without_regenerating(state_dir):
    state_dir.write_keypair()
    state_dir.write_state_json()
    fixture_private = state_dir.private_key_path.read_bytes()
    with _client(state_dir) as client:
        resp = client.get("/api/v1/setup/identity", headers=_AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["deployment_id"] == "dep12345"
    assert body["public_key"] == state_dir.public_key_path.read_text().strip()
    assert state_dir.private_key_path.read_bytes() == fixture_private


def test_identity_mount_configured_is_409(state_dir):
    state_dir.mount_configured_user_identity()
    with _client(state_dir) as client:
        resp = client.get("/api/v1/setup/identity", headers=_AUTH)
    assert resp.status_code == 409
    assert resp.json() == {"reason": "mount_configured"}
    # No service identity gets generated for a mount-configured deployment.
    assert not state_dir.private_key_path.exists()


# ---------------------------------------------------------------------------
# PUT /api/v1/setup/registry — happy path
# ---------------------------------------------------------------------------


def test_put_registry_happy_path_applies_mirror_and_flips_to_adopted(state_dir):
    # Real adoption order: identity exists first, then the mirror is pushed.
    state_dir.write_keypair()
    state_dir.write_state_json()
    with _client(state_dir) as client:
        resp = client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json() == {
            "applied": True,
            "registry_instances": 2,
            "host_key_instances": 1,
        }

        # First-class file contents: service known_hosts + colon-delimited registry.
        assert _service_known_hosts(state_dir).read_text() == _VALID_KEY_LINE + "\n"
        assert state_dir.registry_path.read_text() == _EXPECTED_REGISTRY_TEXT

        # The PUT does not end the session (verify is the terminal step, FR-007).
        status = client.get("/api/v1/setup/status", headers=_AUTH).json()
    assert status["state"] == "adopted"
    assert status["registry_instances"] == 2


# ---------------------------------------------------------------------------
# PUT /api/v1/setup/registry — rejections (nothing written, FR-019)
# ---------------------------------------------------------------------------


def test_put_registry_mount_configured_409_writes_nothing(state_dir):
    state_dir.mount_configured_user_identity()
    original_registry = state_dir.registry_path.read_text()
    with _client(state_dir) as client:
        resp = client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
    assert resp.status_code == 409
    assert resp.json() == {"reason": "mount_configured"}
    assert state_dir.registry_path.read_text() == original_registry
    assert not _service_known_hosts(state_dir).exists()


def test_put_registry_empty_without_allow_empty_is_422(state_dir):
    state_dir.unconfigured()
    with _client(state_dir) as client:
        resp = client.put(
            "/api/v1/setup/registry",
            json={"version": 1, "registry": [], "host_keys": {}},
            headers=_AUTH,
        )
    assert resp.status_code == 422
    assert resp.json() == {"reason": "empty_registry"}
    _assert_nothing_written(state_dir)


def test_put_registry_empty_with_allow_empty_succeeds(state_dir):
    state_dir.unconfigured()
    with _client(state_dir) as client:
        resp = client.put(
            "/api/v1/setup/registry?allow_empty=true",
            json={"version": 1, "registry": [], "host_keys": {}},
            headers=_AUTH,
        )
    assert resp.status_code == 200
    assert resp.json() == {
        "applied": True,
        "registry_instances": 0,
        "host_key_instances": 0,
    }
    assert state_dir.registry_path.read_text() == ""
    assert _service_known_hosts(state_dir).read_text() == ""


@pytest.mark.parametrize(
    ("body", "detail_fragment"),
    [
        pytest.param(_payload(version=2), "unsupported payload version 2", id="wrong-version"),
        pytest.param(
            _payload(host_keys={"ghost": [_VALID_KEY_LINE]}),
            "does not reference any registry entry",
            id="host-keys-unknown-name",
        ),
        pytest.param(
            _payload(host_keys={"dev": ["garbage-not-a-known-hosts-line"]}),
            "fewer than 3 fields",
            id="unparseable-known-hosts-line",
        ),
        pytest.param(
            _payload(host_keys={"cloud": [_VALID_KEY_LINE]}),
            "SSM-access",
            id="ssm-entry-with-host-keys",
        ),
        pytest.param(
            _payload(
                registry=[{"type": "incus", "name": "a:b", "host": "10.0.0.5", "user": "remo"}],
                host_keys={},
            ),
            "cannot contain",
            id="colon-in-registry-field",
        ),
        pytest.param({"registry": "nope"}, "", id="structurally-malformed-body"),
    ],
)
def test_put_registry_invalid_payload_writes_nothing(state_dir, body, detail_fragment):
    state_dir.unconfigured()
    with _client(state_dir) as client:
        resp = client.put("/api/v1/setup/registry", json=body, headers=_AUTH)
    assert resp.status_code == 422
    payload = resp.json()
    # Contract shape — never FastAPI's default {"detail": [...]} 422 body.
    assert payload["reason"] == "invalid_payload"
    assert isinstance(payload["detail"], str)
    assert detail_fragment in payload["detail"]
    _assert_nothing_written(state_dir)


# ---------------------------------------------------------------------------
# PUT /api/v1/setup/registry — atomicity on mid-apply failure (research R5)
# ---------------------------------------------------------------------------


def test_put_registry_mid_apply_failure_is_safe_and_converges(state_dir, monkeypatch):
    state_dir.write_keypair()
    state_dir.write_state_json()

    real_write = setup_api._write_lines_atomically
    fail_registry_write = {"active": True}

    def flaky_write(path, lines):
        # Host-keys file is written first; fail only the registry (second) write.
        if fail_registry_write["active"] and path == state_dir.registry_path:
            raise OSError("disk full")
        real_write(path, lines)

    monkeypatch.setattr(setup_api, "_write_lines_atomically", flaky_write)

    with _client(state_dir) as client:
        resp = client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
        assert resp.status_code == 500
        assert resp.json() == {"detail": "failed to apply registry"}

        # Crash between writes: host keys may exist (documented-safe superset,
        # apply order R5), but the registry must be untouched/absent.
        assert not state_dir.registry_path.exists()
        assert _service_known_hosts(state_dir).read_text() == _VALID_KEY_LINE + "\n"

        # A subsequent successful push converges to the full mirror. (The
        # first PUT failed inside _apply_payload, before end(), so the session
        # is still live here.)
        fail_registry_write["active"] = False
        resp = client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["applied"] is True
        assert state_dir.registry_path.read_text() == _EXPECTED_REGISTRY_TEXT

        status = client.get("/api/v1/setup/status", headers=_AUTH).json()
    assert status["state"] == "adopted"


# ---------------------------------------------------------------------------
# POST /api/v1/setup/verify
# ---------------------------------------------------------------------------


def test_verify_wraps_check_results(state_dir, monkeypatch):
    state_dir.adopted()
    canned = [
        web_check_module.CheckResult(
            name="registry", passed=True, detail="readable (1 instances)"
        ),
        web_check_module.CheckResult(
            name="instance incus/dev",
            passed=False,
            detail="unreachable",
            remediation="Check instance is running / reachable.",
        ),
    ]
    seen: dict[str, Any] = {}

    def fake_run_checks(settings, *, include_instances):
        seen["include_instances"] = include_instances
        return canned

    monkeypatch.setattr(web_check_module, "run_checks", fake_run_checks)

    with _client(state_dir) as client:
        resp = client.post("/api/v1/setup/verify", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == {
        "results": [
            {
                "name": "registry",
                "passed": True,
                "detail": "readable (1 instances)",
                "remediation": None,
            },
            {
                "name": "instance incus/dev",
                "passed": False,
                "detail": "unreachable",
                "remediation": "Check instance is running / reachable.",
            },
        ],
        "all_passed": False,
    }
    # Verify includes the per-instance round-trips (contract: check pass).
    assert seen["include_instances"] is True


def test_verify_all_passed_true_when_every_check_passes(state_dir, monkeypatch):
    state_dir.adopted()
    monkeypatch.setattr(
        web_check_module,
        "run_checks",
        lambda settings, *, include_instances: [
            web_check_module.CheckResult(name="registry", passed=True, detail="ok")
        ],
    )
    with _client(state_dir) as client:
        resp = client.post("/api/v1/setup/verify", headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["all_passed"] is True


# ---------------------------------------------------------------------------
# Pairing gate inheritance (dormancy matrix lives in test_setup_dormancy.py)
# ---------------------------------------------------------------------------


def _request(client: TestClient, method: str, path: str, headers: dict[str, str]):
    kwargs: dict[str, Any] = {"headers": headers}
    if method in {"PUT", "POST"}:
        kwargs["json"] = {}
    return client.request(method, path, **kwargs)


@pytest.mark.parametrize(("method", "path"), _SETUP_ROUTES)
def test_setup_routes_are_404_when_no_live_session(state_dir, method, path):
    state_dir.unconfigured()
    with _client(state_dir, live=False) as client:
        resp = _request(client, method, path, {"Origin": _ORIGIN})
    assert resp.status_code == 404
    # Fail closed: indistinguishable from an unknown route (FR-005).
    assert resp.json() == {"detail": "Not Found"}


@pytest.mark.parametrize(("method", "path"), _SETUP_ROUTES)
def test_setup_routes_are_dormant_404_on_wrong_code(state_dir, method, path):
    state_dir.unconfigured()
    with _client(state_dir) as client:  # a live session exists, but the code is wrong
        resp = _request(
            client, method, path, {"Authorization": "Bearer wrong-code", "Origin": _ORIGIN}
        )
    # FR-006: a wrong-but-present code is the SAME dormant 404, never a 401.
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
