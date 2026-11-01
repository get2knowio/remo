"""Hetzner provider descriptor — pure metadata, no hcloud import (FR-024).

The object of this distribution's ``remo.providers`` entry point
(docs/provider-plugins.md). Imported at discovery time on every remo start,
so it imports nothing beyond ``remo_cli.core.provider_registry``.
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

#: The provider API this plugin was built against (core/provider_registry.PROVIDER_API_VERSION).
REMO_PROVIDER_API_VERSION = 1

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
    implementation="remo_provider_hetzner.provider",
    # No optional SDK: hcloud is a hard dependency of this distribution, and
    # its only consumer is the hetzner.hcloud Ansible collection.
    sdk_extra=None,
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
