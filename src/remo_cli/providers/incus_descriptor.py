"""Incus ``ProviderDescriptor`` (pure metadata, no SDK imports — FR-024).

Registered from ``providers/builtin.py``. See ``specs/018-provider-abstraction/
data-model.md`` for the field table and ``contracts/cli-surface.md`` for the
CLI-surface contract this descriptor implements.
"""

from __future__ import annotations

from dataclasses import replace

from remo_cli.core.provider_registry import (
    CORES,
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

# The shared catalog's USER reads "SSH user for the remote host", which is
# ambiguous here: for Incus it is the login on the *Incus host*, used to run
# host-side `incus` commands -- NOT the account you land in inside the
# container (that is always `remo`, set at create/sync time and not
# configurable). Overridden so `--help` says which of the two machines it means.
HOST_USER = replace(
    USER,
    help="SSH user on the Incus host, for host-side incus commands. "
    "Not the container login, which is always 'remo'.",
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
        HOST_USER,
        DOMAIN,
        IMAGE,
        CORES,
        MEMORY,
        USE_IP,
    ),
    update_options=(
        replace(HOST, default=""),
        HOST_USER,
        CORES,
        MEMORY,
    ),
    destroy_options=(
        replace(HOST, default=""),
        HOST_USER,
        REMOVE_STORAGE,
    ),
    sync_options=(
        replace(HOST, default="localhost"),
        HOST_USER,
        USE_IP,
    ),
    info_options=(
        replace(HOST, default=""),
        HOST_USER,
    ),
    supports_managed_marker=True,
    snapshot_region_scoped=False,
    extra_commands=(
        CommandSpec(
            name="bootstrap",
            help="Initialize an Incus host.",
            impl="bootstrap",
            options=(
                replace(HOST, default="localhost"),
                HOST_USER,
                NETWORK_TYPE,
                VERBOSE,
            ),
        ),
    ),
)
