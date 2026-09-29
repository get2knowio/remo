"""Fixture provider descriptor — metadata only, no SDK imports.

This is what an out-of-tree provider's descriptor module looks like
(docs/provider-plugins.md). Registered through the ``remo.providers`` entry
point declared in this distribution's pyproject.toml.
"""

from __future__ import annotations

from remo_cli.core.provider_registry import (
    REGION,
    VOLUME_SIZE,
    ConnectionSpec,
    NameFormat,
    ProviderDescriptor,
)

#: The provider API this plugin was built against (core/provider_registry.PROVIDER_API_VERSION).
REMO_PROVIDER_API_VERSION = 1

DESCRIPTOR = ProviderDescriptor(
    type_name="fixture",
    display_name="Fixture",
    default_instance_name="fixture1",
    name_format=NameFormat.FLAT,
    # KnownHost attribute -> registry v2 nested JSON key; the registry parses
    # these back by the same declaration, so entries round-trip.
    registry_fields=(("instance_id", "box_id"), ("region", "zone")),
    connection=ConnectionSpec(),
    implementation="remo_fixture_provider.provider",
    # `--name`, `--volume-size`, `--only`, `--skip` and `--verbose` are added
    # by the CLI factory for every provider; declare only what is ours.
    create_options=(REGION,),
    resize_dimensions=(VOLUME_SIZE,),
    sync_options=(),
)
