"""Hetzner provider descriptor — pure metadata, no hcloud import (FR-024).

Registered by ``providers/builtin.py``. Must stay importable with the
``hetzner`` extra (hcloud) absent; this module imports nothing beyond
``core/provider_registry.py``.
"""

from __future__ import annotations

from remo_cli.core.provider_registry import (
    LOCATION,
    VOLUME_SIZE,
    ConnectionSpec,
    NameFormat,
    OptionSpec,
    ProviderDescriptor,
)

# Provider-local extras (not in the shared catalog — only Hetzner uses these).
_SERVER_TYPE = OptionSpec(
    name="--type", param="server_type", default="", help="Server type (default: cx22)."
)
_REMOVE_VOLUME = OptionSpec(
    name="--remove-volume",
    param="remove_volume",
    is_flag=True,
    help="Also remove persistent volume.",
)

DESCRIPTOR = ProviderDescriptor(
    type_name="hetzner",
    display_name="Hetzner",
    default_instance_name="remo",
    name_format=NameFormat.FLAT,
    registry_fields=(),  # hetzner has no nested registry fields today
    connection=ConnectionSpec(),
    implementation="remo_cli.providers.hetzner",
    sdk_extra="hetzner",
    create_options=(
        _SERVER_TYPE,
        LOCATION,
    ),
    upgrade_options=(),
    resize_dimensions=(VOLUME_SIZE,),
    tag_options=(),
    destroy_options=(
        _REMOVE_VOLUME,
    ),
    sync_options=(),
    info_options=(),
    extra_commands=(),
    supports_managed_marker=True,
    snapshot_region_scoped=False,
    snapshot_async=True,
)
