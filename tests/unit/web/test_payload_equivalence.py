"""Byte-identical payload proof for the Phase 2 (020) contract-completeness pass.

FR-005 is the invariant the whole feature rests on: annotating response models
with closed enums, adding `KnownProviderType`, and declaring previously-untyped
response bodies must not move a single serialized byte. This is the only
dedicated check for that invariant — everything else in this feature is about
what the *document* says, not what the *wire* carries.

The fixture JSON files under ``fixtures/`` were captured from the service
**before** any Phase 2 model-declaration change landed (T005-T014), by running
this exact request harness against the pre-change code. They must never be
regenerated to make a test pass — a mismatch here means a declaration changed
a real byte, which is a bug in the declaration, not in the fixture.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remo_cli.models.discovery import DiscoverySnapshot, InstanceStatus, TypedError
from remo_cli.models.session_target import DevcontainerRunning, SessionTarget, ZellijState
from remo_cli.web import app as app_module

_ORIGIN = "http://testserver"
FIXTURES = Path(__file__).parent / "fixtures"


class _FakeDiscovery:
    """Fixed snapshot/targets: one known provider type, one third-party type."""

    def get_snapshot(self) -> list[DiscoverySnapshot]:
        return [
            DiscoverySnapshot(
                instance_id="i-100",
                instance_type="aws",
                instance_name="use1",
                status=InstanceStatus.OK,
                region="us-east-1",
                refreshed_at="2026-07-27T00:00:00Z",
            ),
            DiscoverySnapshot(
                instance_id="i-200",
                instance_type="vultr",  # third-party -- not in KnownProviderType (FR-014)
                instance_name="hq1",
                status=InstanceStatus.UNREACHABLE,
                error=TypedError(
                    code="unreachable",
                    message="Could not connect over SSH.",
                    retryable=True,
                    remediation="Check the instance is powered on and reachable.",
                ),
                refreshed_at="2026-07-27T00:00:01Z",
            ),
        ]

    def get_targets(self) -> list[SessionTarget]:
        return [
            SessionTarget(
                id="deadbeef",
                instance_type="aws",
                instance_name="use1",
                project="api",
                has_devcontainer=True,
                zellij_state=ZellijState.ACTIVE,
                devcontainer_running=DevcontainerRunning.RUNNING,
                discovered_at="2026-07-27T00:00:00Z",
                git_tracked=True,
                git_dirty=False,
                git_ahead=1,
                git_behind=0,
            ),
        ]


def _client(tmp_config_dir: Path) -> TestClient:
    from remo_cli.web.config import WebSettings

    settings = WebSettings(
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[_ORIGIN],
    )
    application = app_module.create_app(settings)
    application.state.discovery_service = _FakeDiscovery()
    return TestClient(application, base_url=_ORIGIN)


def test_hosts_response_byte_identical(tmp_config_dir: Path) -> None:
    client = _client(tmp_config_dir)
    response = client.get("/api/v1/hosts")
    assert response.status_code == 200
    expected = (FIXTURES / "hosts_response.json").read_bytes()
    assert response.content == expected


def test_sessions_response_byte_identical(tmp_config_dir: Path) -> None:
    client = _client(tmp_config_dir)
    response = client.get("/api/v1/sessions")
    assert response.status_code == 200
    expected = (FIXTURES / "sessions_response.json").read_bytes()
    assert response.content == expected


def test_pairing_end_is_204_with_empty_body(tmp_config_dir: Path) -> None:
    """T017b: `POST /pairing/end` stays a 204 with no body -- never idealized
    into a JSON object."""
    client = _client(tmp_config_dir)
    response = client.post("/api/v1/pairing/end", headers={"origin": _ORIGIN})
    assert response.status_code == 204
    assert response.content == b""


def test_pairing_mint_403_returns_detail_not_envelope(tmp_config_dir: Path) -> None:
    """T017b: the pairing 403 body is `{"detail": ...}`, never the `{"error":
    {...}}` envelope -- ErrorEnvelope must not be declared on this route."""
    client = _client(tmp_config_dir)
    response = client.post("/api/v1/pairing/mint", headers={"origin": _ORIGIN})
    assert response.status_code == 403
    body = response.json()
    assert set(body.keys()) == {"detail"}
    assert isinstance(body["detail"], str)
