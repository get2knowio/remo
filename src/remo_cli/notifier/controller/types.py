"""Controller abstractions (Approach B — issue #46).

The controller is intentionally decoupled from Docker's wire format and from the
socket proxy: it speaks to a ``DockerClient`` protocol (normalized events +
inspect + network ops) and a ``Registrar`` protocol (the spec-009 ``/v1/sources``
side). Concrete implementations live in ``docker_http.py``; tests inject fakes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from remo_cli.notifier.models import SourceRegistration


@dataclass(frozen=True)
class DockerEvent:
    """A normalized Docker event (raw JSON is translated at the edge)."""

    type: str  # "container" | "network" | ...
    action: str  # "start" | "die" | "stop" | "destroy" | ...
    actor_id: str
    actor_name: str = ""


@dataclass(frozen=True)
class ContainerInfo:
    id: str
    name: str
    labels: dict[str, str]
    networks: list[str]  # all attached networks (controller filters the bridge)


@dataclass
class ControllerConfig:
    """Discovery + wiring conventions (host-managed key mount; issue #42/#46)."""

    notifier_container: str = "remo-notifier"
    key_dir: Path = Path("/run/remo/keys")  # per-project approver keys: <key_dir>/<sourceId>
    scheme: str = "http"
    default_port: int = 8080
    label_enabled: str = "remo.agentsh.enabled"  # truthy → this container is a source
    label_port: str = "remo.agentsh.port"
    label_source_id: str = "remo.agentsh.source-id"  # optional; defaults to container name
    label_prefix: str = "remo.label."  # remo.label.foo=bar → source label foo=bar
    bridge_network: str = "bridge"  # excluded from attach (shared; no isolation)
    extra_labels: dict[str, str] = field(default_factory=dict)


class DockerClient(Protocol):
    """The (proxied) Docker surface the controller needs — and nothing more."""

    def events(self) -> AsyncIterator[DockerEvent]: ...

    async def list_containers(self) -> list[str]: ...

    async def inspect_container(self, cid: str) -> ContainerInfo | None: ...

    async def network_members(self, net: str) -> list[str]: ...

    async def network_connect(self, net: str, container: str) -> None: ...

    async def network_disconnect(self, net: str, container: str) -> None: ...


class Registrar(Protocol):
    """The spec-009 ``/v1/sources`` side, behind a protocol so it can be a held
    presence connection or an explicit endpoint without the controller caring."""

    async def register(self, reg: SourceRegistration) -> bool: ...

    async def deregister(self, source_id: str) -> None: ...
