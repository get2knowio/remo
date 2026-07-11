"""Approach B: socket-watching controller (issue #46).

Public surface: the ``Controller`` core (pure logic over the ``DockerClient`` /
``Registrar`` protocols) plus the protocol/config/event types. Concrete HTTP
implementations are imported from ``docker_http`` by the eventual entry point.
"""

from __future__ import annotations

from remo_cli.notifier.controller.core import Controller
from remo_cli.notifier.controller.types import (
    ContainerInfo,
    ControllerConfig,
    DockerClient,
    DockerEvent,
    Registrar,
)

__all__ = [
    "Controller",
    "ControllerConfig",
    "ContainerInfo",
    "DockerClient",
    "DockerEvent",
    "Registrar",
]
