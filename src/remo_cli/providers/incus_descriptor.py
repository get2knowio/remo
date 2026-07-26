"""Incus ``ProviderDescriptor`` (pure metadata, no SDK imports — FR-024).

Registered from ``providers/builtin.py``. See ``specs/018-provider-abstraction/
data-model.md`` for the field table and ``contracts/cli-surface.md`` for the
CLI-surface contract this descriptor implements.
"""

from __future__ import annotations

from dataclasses import replace

from remo_cli.core.provider_registry import (
    CORES,
    CREATE_YES_DEPRECATION,
    DOMAIN,
    HOST,
    IMAGE,
    MEMORY,
    USE_IP,
    USER,
    VERBOSE,
    CommandSpec,
    ConnectionSpec,
    NameFormat,
    OptionSpec,
    ProviderDescriptor,
)

# Incus-specific options not shared with any other provider.
REMOVE_STORAGE = OptionSpec(
    name="--remove-storage",
    param="remove_storage",
    is_flag=True,
    help="Also remove host mount directories (e.g. /home, /workspace) bound into the container.",
)
NETWORK_TYPE = OptionSpec(
    name="--network-type",
    param="network_type",
    default="",
    help="Network type for Incus host.",
)

DESCRIPTOR = ProviderDescriptor(
    type_name="incus",
    display_name="Incus",
    default_instance_name="dev1",
    name_format=NameFormat.HOST_SCOPED,
    registry_fields=(("instance_id", "host_user"),),
    connection=ConnectionSpec(),
    implementation="remo_cli.providers.incus",
    sdk_extra=None,
    create_options=(
        replace(HOST, default="localhost"),
        USER,
        DOMAIN,
        IMAGE,
        CORES,
        MEMORY,
        USE_IP,
    ),
    update_options=(
        replace(HOST, default=""),
        USER,
        CORES,
        MEMORY,
    ),
    destroy_options=(
        replace(HOST, default=""),
        USER,
        REMOVE_STORAGE,
    ),
    sync_options=(
        replace(HOST, default="localhost"),
        USER,
        USE_IP,
    ),
    info_options=(
        replace(HOST, default=""),
        USER,
    ),
    snapshot_region_scoped=False,
    extra_commands=(
        CommandSpec(
            name="bootstrap",
            help="Initialize an Incus host.",
            impl="bootstrap",
            options=(
                replace(HOST, default="localhost"),
                USER,
                NETWORK_TYPE,
                VERBOSE,
            ),
        ),
    ),
    deprecated_options=(CREATE_YES_DEPRECATION,),
)
