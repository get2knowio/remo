"""remo add / remo remove — provider-neutral SSH host registration (feature 014)."""

from __future__ import annotations

import click

from remo_cli.cli.providers.factory import provider_command


@click.command("add")
@click.argument("name")
@click.argument("target")
@click.option(
    "--user",
    default=None,
    help="SSH user; overrides any user@ in TARGET (default: remo).",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="SSH port; overrides any :port in TARGET (default: 22).",
)
@click.option(
    "--identity",
    default=None,
    metavar="PATH",
    help="SSH private key path; stored and used via 'ssh -i' on connect.",
)
@click.option(
    "--verify",
    is_flag=True,
    default=False,
    help="Check SSH reachability before registering (fail-closed; no write on failure).",
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt when updating an existing added host.",
)
def add(
    name: str,
    target: str,
    user: str | None,
    port: int | None,
    identity: str | None,
    verify: bool,
    assume_yes: bool,
) -> None:
    """Register an SSH-reachable environment as NAME.

    TARGET is [user@]host[:port]. Requires only SSH access — no hypervisor or
    cloud credentials. Afterwards 'remo shell NAME' and 'remo cp' work over SSH
    like any other environment.

    \b
    Examples:
      remo add mybox user@192.0.2.10
      remo add api dev@10.0.0.9:2222 --identity ~/.ssh/api_ed25519
      remo add mybox user@192.0.2.10 --verify
    """
    from remo_cli.core.validation import validate_name, validate_port  # noqa: PLC0415
    from remo_cli.core.web_drift import emit_out_of_date_notice  # noqa: PLC0415
    from remo_cli.providers.added import add as provider_add  # noqa: PLC0415

    validate_name(name)
    if port is not None:
        validate_port(port)

    rc = provider_add(
        name=name,
        target=target,
        user=user,
        port=port,
        identity=identity,
        verify=verify,
        assume_yes=assume_yes,
    )
    if rc:
        raise SystemExit(rc)
    emit_out_of_date_notice()  # 017 US2: a registered SSH host was added


@click.command("remove")
@click.argument("name")
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt.",
)
def remove(name: str, assume_yes: bool) -> None:
    """Deregister a manually-added SSH host (NAME).

    Deletes only the local registry entry; the remote environment is never
    contacted or modified (unlike a provider 'destroy'). Refuses to act on a
    provider-managed host.
    """
    from remo_cli.core.web_drift import emit_out_of_date_notice  # noqa: PLC0415
    from remo_cli.providers.added import remove as provider_remove  # noqa: PLC0415

    rc = provider_remove(name=name, assume_yes=assume_yes)
    if rc:
        raise SystemExit(rc)
    emit_out_of_date_notice()  # 017 US2: a registered SSH host was removed


@click.command("configure")
@click.argument("name")
@click.option(
    "--only",
    "tools_only",
    multiple=True,
    metavar="TOOL",
    help="Only install/configure these tools.",
)
@click.option(
    "--skip",
    "tools_skip",
    multiple=True,
    metavar="TOOL",
    help="Skip these tools.",
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Stream raw ansible-playbook output.",
)
@provider_command
def configure(
    name: str,
    tools_only: tuple[str, ...],
    tools_skip: tuple[str, ...],
    assume_yes: bool,
    verbose: bool,
) -> None:
    """Install or refresh remo's dev tools on a host added with 'remo add'.

    Runs the same configure play the providers use, so NAME gains remo-host,
    zellij and project-launch — which is what makes it appear as a session
    target in 'remo web' instead of showing "update req.".

    Configures the account NAME is registered with, and grants it passwordless
    sudo. For a provider-managed host use 'remo <type> upgrade' instead.

    \b
    Examples:
      remo configure mybox
      remo configure mybox --skip docker --yes
    """
    from remo_cli.core.validation import validate_name  # noqa: PLC0415
    from remo_cli.core.web_drift import emit_out_of_date_notice  # noqa: PLC0415
    from remo_cli.providers.added import configure as provider_configure  # noqa: PLC0415

    validate_name(name)
    provider_configure(
        name=name,
        tools_only=tools_only,
        tools_skip=tools_skip,
        assume_yes=assume_yes,
        verbose=verbose,
    )
    # A newly configured host usually still needs its key pushed to the web
    # service; the notice is a no-op when the push cache already matches.
    emit_out_of_date_notice()
