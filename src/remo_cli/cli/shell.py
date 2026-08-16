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
@click.option(
    "-p",
    "--project",
    "project",
    default=None,
    help="Skip the menu and jump straight to PROJECT under ~/projects",
)
@click.option(
    "--exec",
    "exec_cmd",
    default=None,
    help="Run COMMAND inside the project's devcontainer instead of opening a shell (requires -p)",
    metavar="COMMAND",
)
@click.option(
    "--detach",
    is_flag=True,
    default=False,
    help="Run --exec COMMAND detached on the remote and return immediately",
)
def shell(
    name: str | None,
    tunnels: tuple[str, ...],
    no_open: bool,
    no_update_check: bool,
    project: str | None,
    exec_cmd: str | None,
    detach: bool,
) -> None:
    """Connect to a remo environment (auto-detects or picker).

    With -p PROJECT, skip the server-side picker and jump straight into that
    project's session (devcontainer auto-launches if .devcontainer exists).

    With --exec COMMAND, run COMMAND inside the project's devcontainer instead
    of dropping into an interactive shell. Add --detach to fire-and-forget.

    Examples:

      remo shell -p my-app
      remo shell -p my-app --exec 'claude --remote-control'
      remo shell -p my-app --detach --exec 'claude remote-control --name my-rc'
    """
    from remo_cli.core.output import print_error  # noqa: PLC0415

    if detach and not exec_cmd:
        print_error(
            "--detach requires --exec COMMAND (e.g., "
            "'remo shell -p X --detach --exec \"claude remote-control\"')"
        )
        raise SystemExit(2)
    if exec_cmd and not project:
        print_error("--exec requires -p/--project to know where to run the command")
        raise SystemExit(2)
    if detach and tunnels:
        print_error(
            "-L port forwarding cannot be combined with --detach — the SSH "
            "session exits immediately, so the tunnel would die before you "
            "could use it. Drop one or the other."
        )
        raise SystemExit(2)
    from remo_cli.core.ssh import check_remote_version, resolve_remo_host, shell_connect  # noqa: PLC0415
    from remo_cli.core.output import confirm, print_error, print_warning  # noqa: PLC0415
    from remo_cli.core.version import get_current_version, version_is_newer  # noqa: PLC0415
    from remo_cli.providers.aws import auto_start_aws_if_stopped  # noqa: PLC0415

    host = resolve_remo_host(name)

    # Auto-start stopped AWS instances before connecting
    from remo_cli.core.errors import ProviderError  # noqa: PLC0415

    try:
        host = auto_start_aws_if_stopped(host)
    except ProviderError as e:
        print_error(str(e))
        raise SystemExit(e.exit_code) from e

    # Pre-shell remote version check.
    #
    # Applies to every host type, added SSH hosts included. Feature 014 skipped
    # type="ssh" outright on the premise that such hosts "have no remo-managed
    # tooling" — true then, false since 022 gave them `remo configure`, which
    # runs the same shared `tasks/configure_dev_tools.yml` role list every
    # provider upgrade runs and writes the very `~/.remo-version` marker this
    # check reads. A configured added host is version-managed like any other,
    # and skipping it meant an arbitrarily stale one was never surfaced.
    #
    # What survives from FR-011 is narrower and lives in the no-marker branch
    # below: an added host that was never configured is a plain SSH box by the
    # operator's choice, and still drops straight into a login shell.
    if not no_update_check:
        local_version = get_current_version()
        if local_version != "unknown":
            remote_version, remote_err = check_remote_version(host)

            should_update = False
            if remote_err is not None:
                # SSH itself failed — we can't tell whether the marker exists
                # or what version is on the box. Don't prompt to update from
                # an unknown baseline; surface the error so the user can fix
                # it (DNS, host key, auth, ...) and re-run.
                print_warning(
                    f"Could not check tools version on '{host.name}':\n"
                    f"  {remote_err}\n"
                    f"  Skipping update check; proceeding with connection."
                )
            elif remote_version is None and host.type == "ssh":
                # An added host with no marker was never `remo configure`d.
                # Prompting to configure a box the operator deliberately left
                # unmanaged would nag on every single connect, so stay silent
                # (FR-011). Once configured it has a marker and takes the
                # ordinary comparison branches below.
                pass
            elif remote_version is None:
                # No marker file on remote
                should_update = confirm(
                    f"Instance '{host.name}' has no version info. "
                    f"Run `{_upgrade_command_hint(host)}`?",
                    default=True,
                )
            elif version_is_newer(local_version, remote_version):
                # Remote is behind local
                should_update = confirm(
                    f"Instance '{host.name}' tools are v{remote_version}, "
                    f"local is v{local_version}. Run `{_upgrade_command_hint(host)}`?",
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
                from remo_cli.core.errors import ProviderError  # noqa: PLC0415

                try:
                    _run_tools_upgrade(host)
                except ProviderError as e:
                    # The playbook log has already been dumped, but the SSH
                    # connection (and any remote project picker) would scroll
                    # it offscreen immediately. Pause so the user can read it.
                    print_error(f"Tools upgrade for '{host.name}' failed: {e}")
                    if not confirm(
                        "Connect anyway?",
                        default=False,
                    ):
                        raise SystemExit(e.exit_code) from e

    shell_connect(
        host,
        list(tunnels),
        no_open,
        project=project,
        detach=detach,
        exec_cmd=exec_cmd,
    )


def _upgrade_command_hint(host) -> str:  # noqa: ANN001
    """Render the exact command that accepting the prompt will run.

    ``remo configure <name>`` for an added (type="ssh") host, and
    `remo <type> upgrade <name>` for a provider one.

    Names the precise command the accepted prompt runs (SC-003) so the
    remedy is always executable and truthful.

    Host-scoped providers need the host-user flag spelled out too: accepting
    the prompt runs ``update_entry``, which reads the host SSH user off the
    registry entry, but passing ``--host`` on the command line short-circuits
    that registry lookup and would silently fall back to the provider default
    (``""``/``root``). The flag and the attribute both come from the
    descriptor's ``registry_fields`` entry whose JSON key ends in ``_user``
    (``instance_id``/``host_user`` for Incus, ``region``/``host_user`` for
    Proxmox) — no provider literals here.
    """
    from remo_cli.core.provider_registry import (  # noqa: PLC0415
        NameFormat,
        get_descriptor,
        is_provider_type,
    )

    if host.type == "ssh":
        # Added host: `remo configure` is its upgrade verb — the same shared
        # role list, reached through ssh_configure.yml. There is no `remo ssh`
        # command group, so the provider spelling below would name a command
        # that cannot be run.
        return f"remo configure {host.name}"

    if not is_provider_type(host.type):
        # Unrecognized registry type: keep the provider spelling so the message
        # names the type that is actually wrong. _run_tools_upgrade() refuses
        # it with a PreconditionError rather than running anything.
        return f"remo {host.type} upgrade {host.name}"

    descriptor = get_descriptor(host.type)
    if descriptor.name_format is NameFormat.HOST_SCOPED and "/" in host.name:
        host_part, _, short_name = host.name.partition("/")
        cmd = f"remo {host.type} upgrade {short_name} --host {host_part}"
        for attr, json_key in descriptor.registry_fields:
            if json_key.endswith("_user"):
                user_value = getattr(host, attr, "")
                if user_value:
                    flag = "--" + json_key.replace("_", "-")
                    cmd += f" {flag} {user_value}"
                break
        return cmd
    return f"remo {host.type} upgrade {host.name}"


def _run_tools_upgrade(host) -> None:  # noqa: ANN001
    """Refresh remo's tooling on *host*, whichever kind of host it is.

    Two paths onto the same shared ``tasks/configure_dev_tools.yml`` role list:
    ``providers.added.configure()`` for an added (type="ssh") host, and the
    provider's own ``update_entry()`` for a managed one. Must stay in step with
    :func:`_upgrade_command_hint`, which promises the user exactly one of them.

    Raises :class:`~remo_cli.core.errors.ProviderError` on failure (including
    an unrecognized provider type — no more silent no-op).
    """
    from remo_cli.core.errors import PreconditionError  # noqa: PLC0415
    from remo_cli.core.output import print_info  # noqa: PLC0415
    from remo_cli.core.provider_registry import get_provider, is_provider_type  # noqa: PLC0415

    print_info(f"Upgrading instance '{host.name}'...")

    if host.type == "ssh":
        from remo_cli.providers import added  # noqa: PLC0415

        # assume_yes: the caller already confirmed the named `remo configure`
        # command. configure()'s own prompt warns about apt-upgrade and
        # passwordless sudo — consent the operator necessarily gave when this
        # host was first configured, which is what put the marker there that
        # brought us here. Provider upgrades don't re-prompt either.
        added.configure(name=host.name, assume_yes=True)
        return

    if not is_provider_type(host.type):
        raise PreconditionError(
            f"Unknown provider type '{host.type}' for '{host.name}'; cannot upgrade tools."
        )

    module = get_provider(host.type)
    module.update_entry(host)
