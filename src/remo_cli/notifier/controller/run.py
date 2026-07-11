"""Controller entry point + settings (Approach B — issue #46).

Wires the concrete HTTP Docker client (via the socket proxy) and the
presence-connection registrar (to the notifier) into the controller and runs the
supervised watch loop. Settings come from ``REMO_CONTROLLER_*`` env vars; the
parsing is isolated from the I/O so it can be unit-tested.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from remo_cli.notifier.controller.core import Controller
from remo_cli.notifier.controller.docker_http import HttpDockerClient
from remo_cli.notifier.controller.presence import PresenceRegistrar, make_http_hold
from remo_cli.notifier.controller.types import ControllerConfig
from remo_cli.notifier.logging_setup import get_logger

_log = get_logger("remo_notifier.controller.run")


class ConfigError(Exception):
    """A required REMO_CONTROLLER_* setting is missing or invalid."""


@dataclass(frozen=True)
class ControllerSettings:
    docker_url: str  # the socket proxy base URL, e.g. http://docker-proxy:2375
    notifier_url: str  # the notifier control plane, e.g. http://remo-notifier:18181
    notifier_container: str = "remo-notifier"
    key_dir: Path = Path("/run/remo/keys")
    scheme: str = "http"
    agentsh_port: int = 8080

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ControllerSettings:
        env = os.environ if env is None else env
        docker_url = env.get("REMO_CONTROLLER_DOCKER_URL", "").strip()
        notifier_url = env.get("REMO_CONTROLLER_NOTIFIER_URL", "").strip()
        missing = [
            name
            for name, val in (
                ("REMO_CONTROLLER_DOCKER_URL", docker_url),
                ("REMO_CONTROLLER_NOTIFIER_URL", notifier_url),
            )
            if not val
        ]
        if missing:
            raise ConfigError(f"missing required setting(s): {', '.join(missing)}")
        port_raw = env.get("REMO_CONTROLLER_AGENTSH_PORT", "8080").strip()
        try:
            agentsh_port = int(port_raw)
        except ValueError as exc:
            raise ConfigError(f"REMO_CONTROLLER_AGENTSH_PORT not an int: {port_raw!r}") from exc
        return cls(
            docker_url=docker_url,
            notifier_url=notifier_url,
            notifier_container=env.get("REMO_CONTROLLER_NOTIFIER_CONTAINER", "remo-notifier"),
            key_dir=Path(env.get("REMO_CONTROLLER_KEY_DIR", "/run/remo/keys")),
            scheme=env.get("REMO_CONTROLLER_SCHEME", "http"),
            agentsh_port=agentsh_port,
        )

    def controller_config(self) -> ControllerConfig:
        return ControllerConfig(
            notifier_container=self.notifier_container,
            key_dir=self.key_dir,
            scheme=self.scheme,
            default_port=self.agentsh_port,
        )


async def serve(settings: ControllerSettings) -> None:
    # No request timeout: both the Docker event stream and the presence connections
    # are intentionally long-lived.
    async with (
        httpx.AsyncClient(base_url=settings.docker_url, timeout=None) as docker_client,
        httpx.AsyncClient(base_url=settings.notifier_url, timeout=None) as notifier_client,
    ):
        controller = Controller(
            HttpDockerClient(docker_client),
            PresenceRegistrar(make_http_hold(notifier_client)),
            settings.controller_config(),
        )
        _log.info(
            "controller_starting",
            docker_url=settings.docker_url,
            notifier_url=settings.notifier_url,
        )
        await controller.serve()


def main() -> None:
    asyncio.run(serve(ControllerSettings.from_env()))
