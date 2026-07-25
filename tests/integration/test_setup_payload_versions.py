"""Payload version compatibility matrix (015-registry-v2, T034).

Asserts the full compatibility matrix from
specs/015-registry-v2/contracts/mirror-payload-v2.md §4 against a live-in-
process FastAPI app (`TestClient`, no real subprocess needed since this is a
wire-contract test, not an SSH/keyscan end-to-end test — see
tests/integration/test_web_adopt_e2e.py for the live-subprocess flow):

* v1 payload -> accepted, stored as registry.json v2, legacy mirror removed.
* v2 payload -> accepted; wire entries match the file schema exactly.
* v3 payload -> 400 unsupported_payload_version; prior mirror intact & served.
* missing ``payload_versions`` on status -> the workstation (`core.web_adopt`)
  aborts BEFORE any keyscan/authorize/PUT (FR-021).
* stale/missing push-cache ``cache_version`` -> treated as empty (one-time
  full re-verification push), idempotent on an immediate re-push.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from remo_cli.core import web_adopt
from remo_cli.models.host import KnownHost
from remo_cli.web import app as app_module
from remo_cli.web.config import WebSettings
from remo_cli.web.pairing import PairingSession

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

_ORIGIN = "http://testserver"
_TOKEN = "unit-test-setup-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}", "Origin": _ORIGIN}

_VALID_KEY_LINE = "10.0.0.5 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFixtureKeyMaterial0000"


class _NoopDiscovery:
    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        return None


def _inject_session(application, code: str = _TOKEN) -> None:
    application.state.pairing_manager._session = PairingSession(
        code=code, identity=None, origin="adopt", last_activity=time.monotonic(), ttl_s=1e9
    )


def _client(tmp_path) -> TestClient:
    settings = WebSettings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[_ORIGIN],
        operator_auth="none",
        ssh_control_dir=str(tmp_path / "ssh-ctrl"),
    )
    application = app_module.create_app(settings)
    application.state.discovery_service = _NoopDiscovery()
    _inject_session(application)
    return TestClient(application, base_url=_ORIGIN)


def _v1_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "registry": [
            {"type": "incus", "name": "dev", "host": "10.0.0.5", "user": "remo"},
        ],
        "host_keys": {"dev": [_VALID_KEY_LINE]},
    }
    payload.update(overrides)
    return payload


def _v2_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 2,
        "registry": [
            {
                "type": "incus",
                "name": "dev",
                "host": "10.0.0.5",
                "user": "remo",
                "access": "direct",
            },
        ],
        "host_keys": {"dev": [_VALID_KEY_LINE]},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Service-side: v1/v2/v3 compatibility matrix
# ---------------------------------------------------------------------------


def test_v1_payload_accepted_stored_as_v2_and_legacy_mirror_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("REMO_HOME", str(tmp_path / "remo"))
    remo_home = tmp_path / "remo"
    remo_home.mkdir()
    # A stray legacy mirror left by a pre-upgrade push (service-owned
    # replaceable state) must be removed once the service applies a payload.
    stale_legacy = remo_home / "known_hosts"
    stale_legacy.write_text("incus:stale:1.2.3.4:remo\n")

    with _client(tmp_path) as client:
        resp = client.put("/api/v1/setup/registry", json=_v1_payload(), headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json()["applied"] is True

    registry_json = remo_home / "registry.json"
    doc = json.loads(registry_json.read_text())
    assert doc["version"] == 2
    assert doc["hosts"] == [
        {"type": "incus", "name": "dev", "host": "10.0.0.5", "user": "remo", "access": "direct"}
    ]
    assert not stale_legacy.exists()


def test_v2_payload_accepted_wire_entries_match_file_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("REMO_HOME", str(tmp_path / "remo"))
    with _client(tmp_path) as client:
        resp = client.put("/api/v1/setup/registry", json=_v2_payload(), headers=_AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"applied": True, "registry_instances": 1, "host_key_instances": 1}

    doc = json.loads((tmp_path / "remo" / "registry.json").read_text())
    assert doc["hosts"] == _v2_payload()["registry"]


def test_v3_payload_rejected_mirror_intact_and_served(tmp_path, monkeypatch):
    monkeypatch.setenv("REMO_HOME", str(tmp_path / "remo"))
    with _client(tmp_path) as client:
        # Establish a baseline mirror first.
        first = client.put("/api/v1/setup/registry", json=_v1_payload(), headers=_AUTH)
        assert first.status_code == 200
        baseline = (tmp_path / "remo" / "registry.json").read_text()

        resp = client.put(
            "/api/v1/setup/registry", json=_v1_payload(version=3), headers=_AUTH
        )
        assert resp.status_code == 400
        assert resp.json() == {
            "error": {
                "code": "unsupported_payload_version",
                "supported": [1, 2],
                "received": 3,
            }
        }
        # Prior mirror unchanged and still served over GET /status.
        assert (tmp_path / "remo" / "registry.json").read_text() == baseline
        status = client.get("/api/v1/setup/status", headers=_AUTH).json()
    assert status["registry_instances"] == 1
    assert status["payload_versions"] == [1, 2]


# ---------------------------------------------------------------------------
# Workstation-side: fail-fast on version skew (FR-021)
# ---------------------------------------------------------------------------


def _ssm_free_host() -> KnownHost:
    return KnownHost(type="incus", name="dev", host="10.0.0.5", user="remo")


@pytest.fixture
def api_client(mocker):
    client = mocker.MagicMock()
    client.get_status.return_value = {"state": "adopted", "registry_instances": 1}
    mocker.patch("remo_cli.core.web_adopt.SetupApiClient", return_value=client)
    return client


def test_push_aborts_before_any_mutation_when_service_omits_payload_versions(
    tmp_config_dir, api_client, mocker
):
    """An old (pre-015) service's status has no `payload_versions` field at
    all -- implies [1] -- so the workstation must abort before any keyscan,
    authorize, or PUT (fail truly fast, no partial mutation of any kind)."""
    mocker.patch(
        "remo_cli.core.web_adopt.get_known_hosts", return_value=[_ssm_free_host()]
    )
    scan = mocker.patch("remo_cli.core.web_adopt.scan_and_verify_host_key")
    authorize = mocker.patch("remo_cli.core.web_adopt.authorize_service_key")

    with pytest.raises(web_adopt.UnsupportedPayloadVersionError, match="upgrade the remo-web"):
        web_adopt.run_push("http://web.example:8080", "code", interactive=False)

    scan.assert_not_called()
    authorize.assert_not_called()
    api_client.get_identity.assert_not_called()
    api_client.put_registry.assert_not_called()


def test_push_proceeds_when_service_advertises_v2(tmp_config_dir, api_client, mocker):
    api_client.get_status.return_value = {
        "state": "adopted",
        "registry_instances": 1,
        "payload_versions": [1, 2],
    }
    mocker.patch(
        "remo_cli.core.web_adopt.get_known_hosts", return_value=[_ssm_free_host()]
    )
    api_client.get_identity.return_value = {
        "deployment_id": "dep-1",
        "public_key": "ssh-ed25519 AAAAfixture remo-web@dep-1",
    }
    mocker.patch(
        "remo_cli.core.web_adopt.scan_and_verify_host_key",
        return_value=web_adopt.HostKeyScan("trusted", lines=[_VALID_KEY_LINE]),
    )
    mocker.patch(
        "remo_cli.core.web_adopt.authorize_service_key", return_value=(True, "")
    )
    api_client.put_registry.return_value = {"registry_instances": 1, "host_key_instances": 1}
    api_client.post_verify.return_value = {"all_passed": True, "results": []}

    result = web_adopt.run_push("http://web.example:8080", "code", interactive=False)

    api_client.put_registry.assert_called_once()
    payload = api_client.put_registry.call_args.args[0]
    assert payload["version"] == 2
    assert result.outcomes[0].outcome == web_adopt.OUTCOME_ADOPTED


# ---------------------------------------------------------------------------
# Push delta cache: stale/missing cache_version treated as empty (research R10)
# ---------------------------------------------------------------------------


def test_stale_cache_version_forces_full_reverify_then_idempotent(tmp_config_dir):
    cache_path = web_adopt.push_cache_path()
    # Pre-015 shape: no "cache_version" key at all.
    cache_path.write_text(
        json.dumps(
            {
                "push_cache": {
                    "dep-1": {
                        "dev": {"fingerprint": "f" * 64, "host_keys": [_VALID_KEY_LINE]}
                    }
                }
            }
        )
    )

    loaded = web_adopt.load_push_cache()
    assert loaded == {}  # discarded wholesale -> first push re-verifies everything

    # Saving now stamps cache_version: 2; a second load is idempotent.
    web_adopt.save_push_cache(
        {
            "dep-1": {
                "dev": web_adopt.CachedInstance(
                    fingerprint=web_adopt.instance_fingerprint(_ssm_free_host()),
                    host_keys=[_VALID_KEY_LINE],
                )
            }
        }
    )
    reloaded = json.loads(cache_path.read_text())
    assert reloaded["cache_version"] == 2
    assert web_adopt.load_push_cache()["dep-1"]["dev"].fingerprint == web_adopt.instance_fingerprint(
        _ssm_free_host()
    )
