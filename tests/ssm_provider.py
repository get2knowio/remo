"""A test-only SSM-routed provider for core tests (#258).

AWS was the one built-in that declared ``ConnectionSpec(mode_field_aware=True,
proxy_hook=...)`` and region-scoped sync/snapshots. It is the ``remo-provider-ec2``
plugin now, and the core suite runs without it, so the core behavior those
declarations drive (``access: ssm`` handling, ProxyCommand plumbing, region
scoping) is exercised through this descriptor instead. Its hook emits the same
``aws ssm start-session`` shape the ec2 plugin does, so the strings the core
tests pin are unchanged; the ec2-specific parts (``AWS_PROFILE``, region
resolution from the registry/env) are tested in plugins/ec2/tests.

Opt in with the ``ssm_provider`` fixture (tests/conftest.py).
"""

from __future__ import annotations

from remo_cli.core.provider_registry import (
    ConnectionSpec,
    REGION,
    NameFormat,
    ProviderDescriptor,
    SshProxyPlan,
)
from remo_cli.models.host import KnownHost

SSM_TYPE = "cloudvm"


def ssm_proxy_hook(host: KnownHost) -> SshProxyPlan | None:
    if host.access_mode != "ssm":
        return None
    region = host.region or "us-west-2"
    return SshProxyPlan(
        proxy_command=(
            f"aws ssm start-session --region {region} --target %h"
            " --document-name AWS-StartSSHSession --parameters 'portNumber=%p'"
        ),
        ssh_target=f"{host.user}@{host.instance_id}",
        extra_opts=("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"),
    )


SSM_DESCRIPTOR = ProviderDescriptor(
    type_name=SSM_TYPE,
    display_name="Cloud VM",
    default_instance_name="",
    name_format=NameFormat.FLAT,
    registry_fields=(("instance_id", "instance_id"), ("region", "region")),
    connection=ConnectionSpec(mode_field_aware=True, proxy_hook="tests.ssm_provider.ssm_proxy_hook"),
    implementation="tests._nonexistent_cloudvm_module",
    region_scoped_sync=True,
    # `remo <type> sync --region`: what makes core's SyncScope require one.
    sync_options=(REGION,),
    sync_scope_description="cloudvm region {region}",
    snapshot_region_scoped=True,
    snapshot_async=True,
)
