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

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from remo_cli.web import app as app_module
from remo_cli.web import check as web_check_module

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


#: Expected v2 registry.json hosts (sorted by (type, name)) for `_payload()`'s
#: default two entries, once mapped through the v1->v2 legacy mapper.
_EXPECTED_V2_HOSTS = [
    {
        "type": "aws",
        "name": "cloud",
        "host": "3.4.5.6",
        "user": "remo",
        "access": "ssm",
        "aws": {"instance_id": "i-0abc", "region": "us-east-1"},
    },
    {
        "type": "incus",
        "name": "dev",
        "host": "10.0.0.5",
        "user": "remo",
        "access": "direct",
    },
]


def _service_known_hosts(state_dir):
    return state_dir.web_identity_dir / "known_hosts"


def _assert_nothing_written(state_dir) -> None:
    """FR-019 all-or-nothing: no target file appears after a rejected PUT."""
    assert not state_dir.registry_path.exists()
    assert not state_dir.v2_registry_path.exists()
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
    # deployment_id is None here -> omitted by response_model_exclude_none (017);
    # mirror_generation/last_push are likewise absent (never pushed).
    assert resp.json() == {
        "state": "unconfigured",
        "public_key_available": False,
        "registry_instances": 0,
        "payload_versions": [1, 2],
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
        "payload_versions": [1, 2],
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
        "payload_versions": [1, 2],
    }


@pytest.mark.parametrize(
    "layout", ["mount_configured", "mount_configured_readonly"]
)
def test_status_mount_configured_has_null_identity(state_dir, layout):
    getattr(state_dir, layout)()
    with _client(state_dir) as client:
        resp = client.get("/api/v1/setup/status", headers=_AUTH)
    assert resp.status_code == 200
    # deployment_id None -> omitted (response_model_exclude_none, 017).
    assert resp.json() == {
        "state": "mount_configured",
        "public_key_available": False,
        "registry_instances": 1,
        "payload_versions": [1, 2],
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
    state_dir.mount_configured()
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
            "mirror_generation": 1,  # 017: first successful apply
        }

        # First-class file contents: service known_hosts + v2 registry.json.
        assert _service_known_hosts(state_dir).read_text() == _VALID_KEY_LINE + "\n"
        doc = json.loads(state_dir.v2_registry_path.read_text())
        assert doc["version"] == 2
        assert doc["hosts"] == _EXPECTED_V2_HOSTS
        assert not state_dir.registry_path.exists()  # legacy mirror never written

        # The PUT does not end the session (the CLI's POST /setup/end does, FR-007).
        status = client.get("/api/v1/setup/status", headers=_AUTH).json()
    assert status["state"] == "adopted"
    assert status["registry_instances"] == 2


# ---------------------------------------------------------------------------
# PUT /api/v1/setup/registry — rejections (nothing written, FR-019)
# ---------------------------------------------------------------------------


def test_put_registry_mount_configured_409_writes_nothing(state_dir):
    state_dir.mount_configured()
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
        "mirror_generation": 1,  # 017: marker written even for an empty mirror
    }
    doc = json.loads(state_dir.v2_registry_path.read_text())
    assert doc == {"version": 2, "hosts": []}
    assert _service_known_hosts(state_dir).read_text() == ""


@pytest.mark.parametrize(
    ("body", "detail_fragment"),
    [
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
                registry=[
                    {"type": "incus", "name": "a\nb", "host": "10.0.0.5", "user": "remo"}
                ],
                host_keys={},
            ),
            "control characters",
            id="control-character-in-registry-field",
        ),
        pytest.param(
            _payload(version=2, registry=[{"type": "incus", "name": "dev", "host": "10.0.0.5"}]),
            "user",
            id="v2-entry-missing-required-field",
        ),
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


@pytest.mark.parametrize(
    ("body", "received"),
    [
        pytest.param(_payload(version=3), 3, id="version-3-not-yet-supported"),
        pytest.param({"registry": "nope"}, None, id="missing-version-field"),
    ],
)
def test_put_registry_unsupported_version_writes_nothing(state_dir, body, received):
    """FR-021: an unknown/missing payload version is rejected with 400 and the
    prior mirror left completely intact — no partial application at all."""
    state_dir.unconfigured()
    with _client(state_dir) as client:
        resp = client.put("/api/v1/setup/registry", json=body, headers=_AUTH)
    assert resp.status_code == 400
    assert resp.json() == {
        "error": {
            "code": "unsupported_payload_version",
            "supported": [1, 2],
            "received": received,
        }
    }
    _assert_nothing_written(state_dir)


def test_put_registry_v2_payload_happy_path(state_dir):
    """A v2-shaped payload (registry-file-v2.md hostEntry, no overloaded fields)
    is accepted and stored exactly as-is (FR-020)."""
    state_dir.write_keypair()
    state_dir.write_state_json()
    v2_body = {
        "version": 2,
        "registry": [
            {
                "type": "incus",
                "name": "dev",
                "host": "10.0.0.5",
                "user": "remo",
                "access": "direct",
            },
            {
                "type": "aws",
                "name": "cloud",
                "host": "3.4.5.6",
                "user": "remo",
                "access": "ssm",
                "aws": {"instance_id": "i-0abc", "region": "us-east-1"},
            },
        ],
        "host_keys": {"dev": [_VALID_KEY_LINE]},
    }
    with _client(state_dir) as client:
        resp = client.put("/api/v1/setup/registry", json=v2_body, headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == {
        "applied": True,
        "registry_instances": 2,
        "host_key_instances": 1,
        "mirror_generation": 1,  # 017: first successful apply
    }
    doc = json.loads(state_dir.v2_registry_path.read_text())
    assert doc["hosts"] == _EXPECTED_V2_HOSTS


# ---------------------------------------------------------------------------
# PUT /api/v1/setup/registry — atomicity on mid-apply failure (research R5)
# ---------------------------------------------------------------------------


def test_put_registry_mid_apply_failure_is_safe_and_converges(state_dir, monkeypatch):
    state_dir.write_keypair()
    state_dir.write_state_json()

    from remo_cli.core import registry as registry_module

    real_write = registry_module._atomic_write_text
    fail_registry_write = {"active": True}

    def flaky_write(path, text):
        # Service known_hosts is written first (setup.py's own helper, not
        # patched here); fail only the registry.json (v2) write.
        if fail_registry_write["active"] and path == state_dir.v2_registry_path:
            raise OSError("disk full")
        real_write(path, text)

    monkeypatch.setattr(registry_module, "_atomic_write_text", flaky_write)

    with _client(state_dir) as client:
        resp = client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
        assert resp.status_code == 500
        assert resp.json() == {"detail": "failed to apply registry"}

        # Crash between writes: host keys may exist (documented-safe superset,
        # apply order R5), but the registry must be untouched/absent.
        assert not state_dir.v2_registry_path.exists()
        assert _service_known_hosts(state_dir).read_text() == _VALID_KEY_LINE + "\n"

        # A subsequent successful push converges to the full mirror. (The
        # first PUT failed inside _apply_payload, before end(), so the session
        # is still live here.)
        fail_registry_write["active"] = False
        resp = client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["applied"] is True
        doc = json.loads(state_dir.v2_registry_path.read_text())
        assert doc["hosts"] == _EXPECTED_V2_HOSTS

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


# ---------------------------------------------------------------------------
# Mirror-identity marker (017 US5, service side) —
# contracts/setup-status-marker.md, data-model.md §3.
# ---------------------------------------------------------------------------


def _mirror_meta_path(state_dir):
    return state_dir.web_identity_dir / "mirror-meta.json"


class TestMirrorMarker:
    """The mirror-identity marker: written by PUT /registry, surfaced by
    GET /status. Advisory-only; carries no secret and no instance content."""

    def test_status_omits_marker_when_never_pushed(self, state_dir):
        # A fresh service (identity present, no mirror ever applied) must not
        # surface mirror_generation/last_push at all (omitted, not null).
        state_dir.write_keypair()
        state_dir.write_state_json()
        assert not _mirror_meta_path(state_dir).exists()
        with _client(state_dir) as client:
            body = client.get("/api/v1/setup/status", headers=_AUTH).json()
        assert "mirror_generation" not in body
        assert "last_push" not in body

    def test_put_writes_marker_and_status_surfaces_it(self, state_dir):
        state_dir.write_keypair()
        state_dir.write_state_json()
        with _client(state_dir) as client:
            put = client.put(
                "/api/v1/setup/registry",
                json=_payload(workstation="hostA/paul"),
                headers=_AUTH,
            )
            assert put.status_code == 200
            assert put.json()["mirror_generation"] == 1

            # The marker file exists on the writable state volume.
            meta = json.loads(_mirror_meta_path(state_dir).read_text())
            assert meta["generation"] == 1
            assert meta["last_push"]["workstation"] == "hostA/paul"
            assert isinstance(meta["last_push"]["at"], str) and meta["last_push"]["at"]

            body = client.get("/api/v1/setup/status", headers=_AUTH).json()
        assert body["mirror_generation"] == 1
        assert body["last_push"]["workstation"] == "hostA/paul"
        assert body["last_push"]["at"] == meta["last_push"]["at"]

    def test_put_increments_generation_monotonically(self, state_dir):
        state_dir.write_keypair()
        state_dir.write_state_json()
        with _client(state_dir) as client:
            first = client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
            second = client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
            status = client.get("/api/v1/setup/status", headers=_AUTH).json()
        assert first.json()["mirror_generation"] == 1
        assert second.json()["mirror_generation"] == 2
        assert status["mirror_generation"] == 2

    def test_absent_workstation_label_defaults_to_unknown(self, state_dir):
        # _payload() carries no top-level "workstation" key.
        state_dir.write_keypair()
        state_dir.write_state_json()
        with _client(state_dir) as client:
            client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
            body = client.get("/api/v1/setup/status", headers=_AUTH).json()
        assert body["last_push"]["workstation"] == "unknown"

    def test_non_string_workstation_label_defaults_to_unknown(self, state_dir):
        state_dir.write_keypair()
        state_dir.write_state_json()
        with _client(state_dir) as client:
            client.put(
                "/api/v1/setup/registry",
                json=_payload(workstation={"not": "a string"}),
                headers=_AUTH,
            )
            body = client.get("/api/v1/setup/status", headers=_AUTH).json()
        assert body["last_push"]["workstation"] == "unknown"

    def test_marker_write_failure_does_not_fail_the_put(self, state_dir, monkeypatch):
        # A marker write failure after a successful registry apply is advisory:
        # logged, swallowed, PUT still succeeds; mirror_generation omitted.
        from remo_cli.web import mirror_meta as mirror_meta_module

        def boom(path, doc):
            raise OSError("read-only marker volume")

        monkeypatch.setattr(mirror_meta_module, "_write_doc", boom)

        state_dir.write_keypair()
        state_dir.write_state_json()
        with _client(state_dir) as client:
            resp = client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
            assert resp.status_code == 200
            body = resp.json()
            assert body["applied"] is True
            # response_model_exclude_none: a failed marker write omits the field.
            assert "mirror_generation" not in body
            # The registry apply itself still landed.
            assert json.loads(state_dir.v2_registry_path.read_text())["hosts"] == _EXPECTED_V2_HOSTS
            # No marker file was written.
            assert not _mirror_meta_path(state_dir).exists()
            # A later successful push converges the generation to 1.
            monkeypatch.undo()
            again = client.put("/api/v1/setup/registry", json=_payload(), headers=_AUTH)
        assert again.json()["mirror_generation"] == 1

    def test_marker_exposes_no_secret_or_instance_contents(self, state_dir):
        # FR-027: /status carries only the whitelisted keys; last_push only
        # {at, workstation} — no key material, no registry entries.
        state_dir.write_keypair()
        state_dir.write_state_json()
        with _client(state_dir) as client:
            client.put(
                "/api/v1/setup/registry",
                json=_payload(workstation="hostA/paul"),
                headers=_AUTH,
            )
            body = client.get("/api/v1/setup/status", headers=_AUTH).json()
        assert set(body) == {
            "state",
            "deployment_id",
            "public_key_available",
            "registry_instances",
            "payload_versions",
            "mirror_generation",
            "last_push",
        }
        assert set(body["last_push"]) == {"at", "workstation"}


def test_put_registry_stamps_last_change_with_push_origin(state_dir):
    """023: every setup-API PUT records last_change (origin=push) alongside the
    legacy generation + last_push fields, which stay shaped exactly as before."""
    state_dir.write_keypair()
    state_dir.write_state_json()
    with _client(state_dir) as client:
        resp = client.put(
            "/api/v1/setup/registry",
            json=_payload(workstation="wk1"),
            headers=_AUTH,
        )
        assert resp.status_code == 200
    doc = json.loads(_mirror_meta_path(state_dir).read_text())
    assert doc["generation"] == 1
    assert set(doc["last_push"]) == {"at", "workstation"}
    assert doc["last_push"]["workstation"] == "wk1"
    assert doc["last_change"]["origin"] == "push"
    assert doc["last_change"]["workstation"] == "wk1"
