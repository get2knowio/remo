"""Concrete DockerClient/Registrar over HTTP (Approach B — issue #46).

Talks to the Docker Engine API **through the socket proxy** (never the raw socket):
the proxy must allow only GET /events,/containers,/networks and POST
/networks/*/connect|disconnect. Endpoint shapes here are pending confirmation by
the proxy-allowlist spike (#46) — the controller core is tested against fakes, so
this layer stays thin and reviewable.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from remo_cli.notifier.controller.types import ContainerInfo, DockerEvent
from remo_cli.notifier.logging_setup import get_logger
from remo_cli.notifier.models import SourceRegistration

_log = get_logger("remo_notifier.controller.http")

# Only container/network events drive the controller; filter at the source.
_EVENT_FILTERS = json.dumps({"type": ["container", "network"]})


def _strip_slash(name: str) -> str:
    return name[1:] if name.startswith("/") else name


class HttpDockerClient:
    """DockerClient backed by the proxied Engine API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._c = client

    async def events(self) -> AsyncIterator[DockerEvent]:
        async with self._c.stream("GET", "/events", params={"filters": _EVENT_FILTERS}) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                actor = raw.get("Actor", {}) or {}
                yield DockerEvent(
                    type=raw.get("Type", ""),
                    action=raw.get("Action", ""),
                    actor_id=actor.get("ID", ""),
                    actor_name=(actor.get("Attributes", {}) or {}).get("name", ""),
                )

    async def list_containers(self) -> list[str]:
        resp = await self._c.get("/containers/json")
        resp.raise_for_status()
        return [c["Id"] for c in resp.json()]

    async def inspect_container(self, cid: str) -> ContainerInfo | None:
        resp = await self._c.get(f"/containers/{cid}/json")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        nets = list((data.get("NetworkSettings", {}).get("Networks", {}) or {}).keys())
        return ContainerInfo(
            id=data.get("Id", cid),
            name=_strip_slash(data.get("Name", "")),
            labels=(data.get("Config", {}) or {}).get("Labels") or {},
            networks=nets,
        )

    async def network_members(self, net: str) -> list[str]:
        resp = await self._c.get(f"/networks/{net}")
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        containers = resp.json().get("Containers", {}) or {}
        return [v.get("Name", "") for v in containers.values() if v.get("Name")]

    async def network_connect(self, net: str, container: str) -> None:
        resp = await self._c.post(f"/networks/{net}/connect", json={"Container": container})
        resp.raise_for_status()

    async def network_disconnect(self, net: str, container: str) -> None:
        resp = await self._c.post(
            f"/networks/{net}/disconnect", json={"Container": container, "Force": False}
        )
        resp.raise_for_status()


class HttpRegistrar:
    """Registrar that posts to the notifier's spec-009 ``/v1/sources`` surface.

    Held-presence vs explicit-endpoint semantics are deferred to #46; for now this
    issues a best-effort register/deregister against the internal notifier URL.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._c = client

    async def register(self, reg: SourceRegistration) -> bool:
        try:
            resp = await self._c.post("/v1/sources", json=reg.model_dump())
            return resp.status_code < 400
        except httpx.HTTPError as exc:
            _log.warning("registrar_register_failed", source_id=reg.source_id, error=str(exc))
            return False

    async def deregister(self, source_id: str) -> None:
        try:
            await self._c.request("DELETE", f"/v1/sources/{source_id}")
        except httpx.HTTPError as exc:
            _log.warning("registrar_deregister_failed", source_id=source_id, error=str(exc))
