"""Registers the four built-in provider descriptors.

Imported lazily by ``core/provider_registry.py`` on first lookup, so every
entry point (CLI, ``remo web serve``, tests) sees these four providers
without needing an explicit import.
"""

from __future__ import annotations

from remo_cli.core.provider_registry import register
from remo_cli.providers.aws_descriptor import DESCRIPTOR as AWS_DESCRIPTOR
from remo_cli.providers.hetzner_descriptor import DESCRIPTOR as HETZNER_DESCRIPTOR
from remo_cli.providers.incus_descriptor import DESCRIPTOR as INCUS_DESCRIPTOR
from remo_cli.providers.proxmox_descriptor import DESCRIPTOR as PROXMOX_DESCRIPTOR

register(INCUS_DESCRIPTOR)
register(PROXMOX_DESCRIPTOR)
register(AWS_DESCRIPTOR)
register(HETZNER_DESCRIPTOR)
