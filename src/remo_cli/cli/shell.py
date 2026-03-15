"""remo shell command - Connect to a remote environment."""

from __future__ import annotations

import click


@click.command()
@click.argument("name", required=False, default=None)
@click.option(
    "-L",
    "tunnels",
    multiple=True,
    help="Forward port: PORT or LOCAL:REMOTE",
)
@click.option(
    "--no-open",
    is_flag=True,
    default=False,
    help="Skip auto-opening browser for tunneled ports",
)
@click.option(
    "--no-update-check",
    is_flag=True,
    default=False,
    help="Skip remote version check before connecting",
)
def shell(
    name: str | None,
    tunnels: tuple[str, ...],
    no_open: bool,
    no_update_check: bool,
) -> None:
    """Connect to a remo environment (auto-detects or picker)."""
    from remo_cli.core.ssh import check_remote_version, resolve_remo_host, shell_connect  # noqa: PLC0415
    from remo_cli.core.output import confirm, print_warning  # noqa: PLC0415
    from remo_cli.core.version import get_current_version, version_is_newer  # noqa: PLC0415
    from remo_cli.providers.aws import auto_start_aws_if_stopped  # noqa: PLC0415

    host = resolve_remo_host(name)

    # Auto-start stopped AWS instances before connecting
    host = auto_start_aws_if_stopped(host)

    # Pre-shell remote version check
    if not no_update_check:
        local_version = get_current_version()
        if local_version != "unknown":
            remote_version = check_remote_version(host)

            should_update = False
            if remote_version is None:
                # No marker file on remote
                should_update = confirm(
                    f"Instance '{host.name}' has no version info. Update tools?",
                    default=True,
                )
            elif version_is_newer(local_version, remote_version):
                # Remote is behind local
                should_update = confirm(
                    f"Instance '{host.name}' tools are v{remote_version}, "
                    f"local is v{local_version}. Update?",
                    default=True,
                )
            elif version_is_newer(remote_version, local_version):
                # Remote is ahead of local
                print_warning(
                    f"Instance '{host.name}' has newer tools (v{remote_version}) "
                    f"than your client (v{local_version}). "
                    f"Consider: uv tool upgrade remo-cli"
                )

            if should_update:
                _run_provider_update(host)

    shell_connect(host, list(tunnels), no_open)


def _run_provider_update(host) -> None:  # noqa: ANN001
    """Run the appropriate provider update for the given host."""
    from remo_cli.core.output import print_info  # noqa: PLC0415

    print_info(f"Updating instance '{host.name}'...")

    if host.type == "aws":
        from remo_cli.providers.aws import update as aws_update  # noqa: PLC0415
        aws_update(name=host.name)
    elif host.type == "hetzner":
        from remo_cli.providers.hetzner import update as hetzner_update  # noqa: PLC0415
        hetzner_update(name=host.name)
    elif host.type == "incus":
        from remo_cli.providers.incus import update as incus_update  # noqa: PLC0415
        # Incus name in known_hosts is "host/container" — extract just the container name
        container_name = host.name.split("/", maxsplit=1)[-1] if "/" in host.name else host.name
        incus_update(name=container_name)
