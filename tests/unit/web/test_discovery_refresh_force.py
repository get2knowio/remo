"""`POST /api/v1/discovery/refresh` honours the request's `force` flag.

`GET /hosts` and `GET /sessions` only READ the discovery cache — this endpoint
is the only thing that repopulates it. The browser console therefore has to hit
it on a background cadence or its whole view freezes at whatever page load
found (the ⚡ on a session started after load never appears, git counts go
stale, new projects never show up).

Hitting it unconditionally on every tick would mean one full SSH sweep of every
instance per tick per open tab, so the console sends `force: false` and lets the
service's own `discovery_cache_ttl_s` decide when a run is actually due. These
tests pin that passthrough — including the default, since an older console omits
the field entirely and must keep its always-run behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from remo_cli.web.api.hosts import router


class _RecordingDiscoveryService:
    """Stands in for DiscoveryService, recording how `refresh` was called."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, bool]] = []

    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        self.calls.append((instance_id, force))

    def get_snapshot(self) -> list[Any]:
        return []

    def get_targets(self) -> list[Any]:
        return []


@pytest.fixture
def client() -> tuple[TestClient, _RecordingDiscoveryService]:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    service = _RecordingDiscoveryService()
    app.state.discovery_service = service
    return TestClient(app), service


def test_no_body_forces_a_run(client) -> None:
    """An older console posts no body at all; it must still force a run."""
    http, service = client

    assert http.post("/api/v1/discovery/refresh").status_code == 202

    assert service.calls == [(None, True)]


def test_explicit_refresh_forces_a_run(client) -> None:
    http, service = client

    http.post("/api/v1/discovery/refresh", json={})

    assert service.calls == [(None, True)]


def test_background_tick_asks_for_a_ttl_gated_run(client) -> None:
    """The whole point: cheap enough to run on an interval, from every tab."""
    http, service = client

    http.post("/api/v1/discovery/refresh", json={"force": False})

    assert service.calls == [(None, False)]


def test_targeted_refresh_still_forces_by_default(client) -> None:
    """`onTerminalEnded` refreshes one instance and wants it to actually run."""
    http, service = client

    http.post("/api/v1/discovery/refresh", json={"instance_id": "abc123"})

    assert service.calls == [("abc123", True)]


def test_force_and_instance_id_compose(client) -> None:
    http, service = client

    http.post("/api/v1/discovery/refresh", json={"instance_id": "abc123", "force": False})

    assert service.calls == [("abc123", False)]
