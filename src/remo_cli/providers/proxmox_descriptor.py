"""Proxmox ``ProviderDescriptor`` — pure metadata, no SDK imports (FR-024).

Registers Proxmox's CLI surface (option specs, extra commands, deprecation
notices) with ``core/provider_registry.py``. Deliberately does not import
``remo_cli.providers.proxmox`` (the heavy implementation module, which shells
out via ``pct``/ansible) — only its dotted module path is referenced, and it
is imported lazily by ``get_provider()`` on first verb execution.
"""

from __future__ import annotations

from dataclasses import replace

from remo_cli.core.provider_registry import (
    CORES,
    DEVCONTAINER_RUNTIME,
    DOMAIN,
    HOST,
    MEMORY,
    USE_IP,
    VERBOSE,
    VOLUME_SIZE,
    ArgumentSpec,
    CommandSpec,
    ConnectionSpec,
    NameFormat,
    OptionSpec,
    ProviderDescriptor,
)

# The login on the *hypervisor node*, used to run host-side `pct` commands --
# NOT the account you land in inside the container (that is always `remo`,
# set at create/sync time and not configurable).
_NODE_USER = OptionSpec(
    name="--node-user",
    param="node_user",
    default="",
    help="SSH user on the Proxmox node, for host-side pct commands "
    "(default: root). Not the container login, which is always 'remo'.",
)

_NODE = OptionSpec(
    name="--node", param="node", default="", help="Proxmox cluster node name (default: --host)."
)
_BRIDGE = OptionSpec(
    name="--bridge", param="bridge", default="", help="Linux bridge to attach to (default: vmbr0)."
)
_STORAGE = OptionSpec(
    name="--storage", param="storage", default="", help="Rootfs storage (default: local-lvm)."
)
_TEMPLATE = OptionSpec(
    name="--template", param="template", default="", help="LXC template path (storage:vztmpl/<file>)."
)
_UNPRIVILEGED = OptionSpec(
    name="--unprivileged/--privileged",
    param="unprivileged",
    is_flag=True,
    default=True,
    help="Run as unprivileged container (default: unprivileged).",
)
_PURGE = OptionSpec(
    name="--purge",
    param="purge",
    is_flag=True,
    help="Also remove the container from backup/replication/HA job configs "
    "(pct destroy --purge). The rootfs is destroyed regardless.",
)

DESCRIPTOR = ProviderDescriptor(
    type_name="proxmox",
    display_name="Proxmox",
    default_instance_name="dev1",
    name_format=NameFormat.HOST_SCOPED,
    registry_fields=(("instance_id", "vmid"), ("region", "node_user")),
    connection=ConnectionSpec(),
    implementation="remo_cli.providers.proxmox",
    sdk_extra=None,
    create_options=(
        replace(HOST, required=True, default=None),
        _NODE_USER,
        _NODE,
        _BRIDGE,
        _STORAGE,
        _TEMPLATE,
        CORES,
        MEMORY,
        _UNPRIVILEGED,
        DOMAIN,
        USE_IP,
        DEVCONTAINER_RUNTIME,
    ),
    upgrade_options=(
        replace(HOST, default=""),
        _NODE_USER,
        DEVCONTAINER_RUNTIME,
    ),
    resize_dimensions=(
        VOLUME_SIZE,
        CORES,
        MEMORY,
    ),
    resize_options=(
        replace(HOST, default=""),
        _NODE_USER,
    ),
    tag_options=(
        replace(HOST, default=""),
        _NODE_USER,
    ),
    destroy_options=(
        replace(HOST, default=""),
        _NODE_USER,
        _PURGE,
    ),
    sync_options=(
        replace(HOST, required=True, default=None),
        _NODE_USER,
        USE_IP,
    ),
    info_options=(
        replace(HOST, default=""),
        _NODE_USER,
    ),
    supports_managed_marker=True,
    snapshot_region_scoped=False,
    host_commands=(
        CommandSpec(
            name="bootstrap",
            help="Verify a Proxmox node and download the default LXC template.",
            impl="bootstrap",
            target=ArgumentSpec("host", required=True),
            options=(
                _NODE_USER,
                _BRIDGE,
                _STORAGE,
                _TEMPLATE,
                VERBOSE,
            ),
        ),
    ),
)
