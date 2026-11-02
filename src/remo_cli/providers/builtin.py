"""The two built-in provider descriptors and their registration.

Called lazily by ``core/provider_registry._ensure_discovered()`` on first
lookup, so every entry point (CLI, ``remo web serve``, tests) sees these two
providers without needing an explicit import — and, since 027, before any
``remo.providers`` entry-point plugin is loaded. Registration is a function
rather than an import side effect so a test can reset the registry and
re-run discovery without re-importing this module.

Hetzner and EC2 are not here: they ship as the ``remo-provider-hetzner`` and
``remo-provider-ec2`` plugins (plugins/hetzner and plugins/ec2, installed by
``remo-cli[hetzner]`` / ``remo-cli[ec2]``). EC2 was the built-in ``aws`` type
until #258.
"""

from __future__ import annotations

from remo_cli.core.provider_registry import BUILTIN_SOURCE, register
from remo_cli.providers.incus_descriptor import DESCRIPTOR as INCUS_DESCRIPTOR
from remo_cli.providers.proxmox_descriptor import DESCRIPTOR as PROXMOX_DESCRIPTOR

#: Registration order is part of the CLI surface (`remo --help` group order).
BUILTIN_DESCRIPTORS = (INCUS_DESCRIPTOR, PROXMOX_DESCRIPTOR)


def register_builtins() -> None:
    """Register the built-ins, in the fixed order above."""
    for descriptor in BUILTIN_DESCRIPTORS:
        register(descriptor, source=BUILTIN_SOURCE)
