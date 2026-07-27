"""AWS ``ProviderDescriptor`` — pure metadata, no SDK imports (FR-024).

Registers AWS's CLI surface (option specs, extra commands, deprecation
notices) with ``core/provider_registry.py``. Deliberately does not import
``remo_cli.providers.aws`` (the heavy implementation module, which lazily
imports ``boto3``) or ``boto3`` itself — only the implementation's dotted
module path is referenced here, and it is imported lazily by
``get_provider()`` on first verb execution. This keeps the descriptor (and
therefore ``providers/builtin.py``, and therefore every CLI entry point)
importable with boto3 absent.
"""

from __future__ import annotations

from dataclasses import replace

from remo_cli.core.provider_registry import (
    LOGIN_USER,
    NAME,
    REGION,
    CommandSpec,
    ConnectionSpec,
    NameFormat,
    OptionSpec,
    ProviderDescriptor,
)

# AWS-specific options not shared with any other provider.
_INSTANCE_TYPE = OptionSpec(
    name="--type", param="instance_type", default="", help="EC2 instance type."
)
_SPOT = OptionSpec(
    name="--spot", param="use_spot", is_flag=True, default=False, help="Use spot instance."
)
_IAM_PROFILE = OptionSpec(
    name="--iam-profile", param="iam_profile", default="", help="IAM instance profile name."
)
_REMOVE_STORAGE = OptionSpec(
    name="--remove-storage",
    param="remove_storage",
    is_flag=True,
    default=False,
    help="Also remove EBS storage volume.",
)

DESCRIPTOR = ProviderDescriptor(
    type_name="aws",
    display_name="AWS",
    default_instance_name=LOGIN_USER,
    name_format=NameFormat.FLAT,
    registry_fields=(("instance_id", "instance_id"), ("region", "region")),
    # Placeholder: AWS reaches instances over SSM, not direct SSH; the
    # ProxyCommand-building proxy_hook is wired in T046.
    connection=ConnectionSpec(
        mode_field_aware=True, proxy_hook="remo_cli.providers.aws.ssh_proxy_hook"
    ),
    implementation="remo_cli.providers.aws",
    sdk_extra="aws",
    create_options=(
        _INSTANCE_TYPE,
        REGION,
        _SPOT,
        _IAM_PROFILE,
    ),
    update_options=(),
    destroy_options=(
        _REMOVE_STORAGE,
    ),
    sync_options=(
        REGION,
    ),
    info_options=(),
    snapshot_region_scoped=True,
    snapshot_async=True,
    extra_commands=(
        CommandSpec(
            name="stop",
            help="Stop an AWS EC2 instance.",
            impl="stop",
            options=(replace(NAME, default=""),),
            confirmable=True,
        ),
        CommandSpec(
            name="start",
            help="Start a stopped AWS EC2 instance.",
            impl="start",
            options=(replace(NAME, default=""),),
            confirmable=False,
        ),
        CommandSpec(
            name="reboot",
            help="Reboot an AWS EC2 instance.",
            impl="reboot",
            options=(replace(NAME, default=""),),
            confirmable=True,
        ),
    ),
)
