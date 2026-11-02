"""EC2 ``ProviderDescriptor`` — pure metadata, no SDK imports (FR-024).

The object of this distribution's ``remo.providers`` entry point
(docs/provider-plugins.md). Imported at discovery time on every remo start,
so it imports nothing beyond ``remo_cli.core.provider_registry`` — never
``remo_provider_ec2.provider`` or ``boto3``, which ``get_provider()`` imports
lazily on first verb execution.

The type was ``aws`` while it was built into remo-cli (#258): registry entries
of that type migrate through ``legacy_type_names``, and ``remo aws ...`` stays
a hidden, warning alias for one release.
"""

from __future__ import annotations

from dataclasses import replace

from remo_cli.core.provider_registry import (
    LOGIN_USER,
    NAME,
    REGION,
    VOLUME_SIZE,
    CommandSpec,
    ConnectionSpec,
    NameFormat,
    OptionSpec,
    ProviderDescriptor,
)

#: The provider API this plugin was built against (core/provider_registry.PROVIDER_API_VERSION).
REMO_PROVIDER_API_VERSION = 1

# EC2-specific options not shared with any other provider.
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
    type_name="ec2",
    display_name="AWS EC2",
    legacy_type_names=("aws",),
    deprecated_cli_aliases=("aws",),
    default_instance_name=LOGIN_USER,
    name_format=NameFormat.FLAT,
    registry_fields=(("instance_id", "instance_id"), ("region", "region")),
    # SSM-mode hosts are reached through an `aws ssm start-session`
    # ProxyCommand; a stopped instance is started before connecting.
    connection=ConnectionSpec(
        mode_field_aware=True,
        proxy_hook="remo_provider_ec2.provider.ssh_proxy_hook",
        pre_connect_hook="remo_provider_ec2.provider.auto_start_aws_if_stopped",
    ),
    implementation="remo_provider_ec2.provider",
    sdk_extra="ec2",
    region_scoped_sync=True,
    sync_scope_description="ec2 region {region}",
    create_options=(
        _INSTANCE_TYPE,
        REGION,
        _SPOT,
        _IAM_PROFILE,
    ),
    upgrade_options=(),
    resize_dimensions=(VOLUME_SIZE,),
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
