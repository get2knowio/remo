"""Socket-watching controller core (Approach B — issue #46).

Watches Docker events and drives source enrollment without any in-container agent:
on a labeled container `start` it reads the host-mounted approver key, derives the
agentsh URL from the container's resolvable name, attaches the notifier to the
project network(s), and registers the source via the existing spec-009 surface. On
`die`/`stop` it deregisters and detaches the notifier (only when no other source
remains on the network). Fail-closed: anything missing/malformed → no registration.

The core is pure logic over the `DockerClient`/`Registrar` protocols, so it is
exercised with fakes (no daemon, no proxy) — `serve()` adds the production
reconnect loop on top.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from remo_cli.notifier.controller.types import (
    ContainerInfo,
    ControllerConfig,
    DockerClient,
    DockerEvent,
    Registrar,
)
from remo_cli.notifier.logging_setup import get_logger
from remo_cli.notifier.models import SourceRegistration

_log = get_logger("remo_notifier.controller")

_TRUTHY = {"1", "true", "yes", "on"}
_STOP_ACTIONS = {"die", "stop", "destroy", "kill"}


@dataclass
class _Tracked:
    source_id: str
    networks: list[str]  # cached at start; the container is gone by stop-time


class Controller:
    def __init__(
        self,
        docker: DockerClient,
        registrar: Registrar,
        config: ControllerConfig | None = None,
    ) -> None:
        self._docker = docker
        self._registrar = registrar
        self._cfg = config or ControllerConfig()
        self._tracked: dict[str, _Tracked] = {}  # container id → tracked source

    # -- lifecycle ----------------------------------------------------------
    async def serve(self, *, backoff_base: float = 1.0, backoff_cap: float = 30.0) -> None:
        """Production entry: reconcile + consume events, reconnecting on stream end."""
        delay = backoff_base
        while True:
            try:
                await self.run()
                delay = backoff_base
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never let the watch loop die
                _log.warning("controller_stream_error", error=str(exc), retry_in=round(delay, 2))
            await asyncio.sleep(delay)
            delay = min(backoff_cap, delay * 2)

    async def run(self) -> None:
        """Single pass: rebuild state from existing containers, then stream events."""
        await self.reconcile()
        async for event in self._docker.events():
            await self.handle(event)

    async def reconcile(self) -> None:
        """Rebuild state on startup so a controller restart re-adopts live sources."""
        for cid in await self._docker.list_containers():
            await self._on_start(cid)

    async def handle(self, event: DockerEvent) -> None:
        if event.type != "container":
            return
        if event.action == "start":
            await self._on_start(event.actor_id)
        elif event.action in _STOP_ACTIONS:
            await self._on_stop(event.actor_id)

    # -- start / stop -------------------------------------------------------
    async def _on_start(self, cid: str) -> None:
        if cid in self._tracked:
            return  # idempotent: duplicate start / reconcile overlap
        try:
            info = await self._docker.inspect_container(cid)
            if info is None or not self._is_source(info):
                return
            source_id = self._source_id(info)
            key = self._read_key(source_id)
            if not key:
                _log.warning("controller_no_key", source_id=source_id)
                return  # fail-closed: no key → no source
            nets = [n for n in info.networks if n != self._cfg.bridge_network]
            for net in nets:
                await self._attach(net)
            reg = SourceRegistration(
                source_id=source_id,
                api_url=self._api_url(source_id, info),
                api_key=key,
                labels=self._labels(info),
            )
            if await self._registrar.register(reg):
                self._tracked[cid] = _Tracked(source_id=source_id, networks=nets)
                _log.info("controller_registered", source_id=source_id, api_url=reg.api_url)
        except Exception as exc:  # noqa: BLE001 - one bad container must not wedge the loop
            _log.warning("controller_start_failed", container=cid, error=str(exc))

    async def _on_stop(self, cid: str) -> None:
        tracked = self._tracked.pop(cid, None)
        if tracked is None:
            return
        try:
            await self._registrar.deregister(tracked.source_id)
            for net in tracked.networks:
                await self._detach(net, exclude=tracked.source_id)
            _log.info("controller_deregistered", source_id=tracked.source_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning("controller_stop_failed", source_id=tracked.source_id, error=str(exc))

    # -- network wiring (netwire semantics, PR #45) -------------------------
    async def _attach(self, net: str) -> None:
        members = await self._docker.network_members(net)
        if self._cfg.notifier_container in members:
            return  # idempotent
        await self._docker.network_connect(net, self._cfg.notifier_container)

    async def _detach(self, net: str, *, exclude: str) -> None:
        members = await self._docker.network_members(net)
        others = [m for m in members if m not in (self._cfg.notifier_container, exclude)]
        if others:
            return  # other sources remain → keep the notifier on the network
        if self._cfg.notifier_container in members:
            await self._docker.network_disconnect(net, self._cfg.notifier_container)

    # -- conventions --------------------------------------------------------
    def _is_source(self, info: ContainerInfo) -> bool:
        return info.labels.get(self._cfg.label_enabled, "").strip().lower() in _TRUTHY

    def _source_id(self, info: ContainerInfo) -> str:
        return info.labels.get(self._cfg.label_source_id, "").strip() or info.name

    def _api_url(self, source_id: str, info: ContainerInfo) -> str:
        port = info.labels.get(self._cfg.label_port, "").strip() or str(self._cfg.default_port)
        return f"{self._cfg.scheme}://{source_id}:{port}"

    def _read_key(self, source_id: str) -> str | None:
        try:
            key = (self._cfg.key_dir / source_id).read_text().strip()
        except OSError:
            return None
        return key or None

    def _labels(self, info: ContainerInfo) -> dict[str, str]:
        pref = self._cfg.label_prefix
        out = dict(self._cfg.extra_labels)
        out.update({k[len(pref):]: v for k, v in info.labels.items() if k.startswith(pref)})
        if len(out) > 16:  # SourceRegistration bounds labels to 16
            out = dict(list(out.items())[:16])
        return out
