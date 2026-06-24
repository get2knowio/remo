"""Channel catalog primitives: ``ChannelDescriptor`` + ``RequiredEnv``.

Import-light by contract: this module and any descriptor that uses it MUST NOT
import FastAPI, uvicorn, or a channel delivery SDK — the laptop CLI imports the
catalog freely (FR-019). The heavy transport is referenced by dotted path in
``transport_factory`` and imported lazily, only in the service container.
See contracts/channel-descriptor.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any


@dataclass(frozen=True)
class RequiredEnv:
    """One credential/config input a channel needs.

    ``name`` MUST follow ``REMO_NOTIFIER_<CHANNEL>_<NAME>`` (FR-012a). ``secret``
    vars are written to the on-host secret file (0400) and never rendered into
    the TOML or logs; non-secret vars render into the transport TOML fragment.
    """

    name: str
    secret: bool
    purpose: str


@dataclass(frozen=True)
class ChannelDescriptor:
    """The catalog entry one channel provides (see channel-descriptor.md)."""

    id: str
    label: str
    image_name: str
    required_env: list[RequiredEnv]
    transport_factory: str  # "pkg.module:callable" — lazy, in-container only
    render_transport_toml: Callable[[dict[str, str]], str] = field(repr=False)
    # In-container path the secret is mounted to (== the path the rendered TOML
    # references). The deploy mounts the host secret file here. None if the
    # channel has no secret.
    secret_mount: str | None = None

    def secret_env(self) -> RequiredEnv | None:
        """The single secret var (the token), or None if the channel has none."""
        secrets = [e for e in self.required_env if e.secret]
        return secrets[0] if secrets else None

    def secret_filename(self) -> str | None:
        """Host-side basename for the mounted secret (e.g. ``telegram_bot_token``)."""
        return self.secret_mount.rsplit("/", 1)[-1] if self.secret_mount else None

    def load_transport_factory(self) -> Callable[..., Any]:
        """Resolve ``transport_factory`` to its callable (lazy import).

        Called only inside the service container, where the channel's delivery
        SDK is installed.
        """
        module_path, _, attr = self.transport_factory.partition(":")
        module = import_module(module_path)
        return getattr(module, attr)
