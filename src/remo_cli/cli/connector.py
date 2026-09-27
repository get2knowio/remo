"""remo connector — Click wiring only (specs/025-ssm-connector,
contracts/cli.md). Business logic lives in `providers/connector.py`; pure
contract helpers in `core/connector.py`. No `remo_cli.web` import."""

from __future__ import annotations

import sys

import click

from remo_cli.cli.providers.factory import provider_command


@click.group()
def connector() -> None:
    """SSM connector: enroll a host as a hybrid-activated managed node and
    attach through the remo-attach session document (docs/ssm-connector.md)."""


@connector.command("attach")
@click.argument("target")
@provider_command
def attach_cmd(target: str) -> int:
    """Invoked by the remo-attach session document; not for interactive use."""
    from remo_cli.providers.connector import attach as provider_attach  # noqa: PLC0415

    return provider_attach(target)


@connector.command("document")
@click.option(
    "--name",
    "name_only",
    is_flag=True,
    default=False,
    help="Print only the document name (remo-attach), for scripting create-document.",
)
def document_cmd(name_only: bool) -> None:
    """Print the shipped remo-attach session document, unchanged."""
    from remo_cli.providers.connector import document as provider_document  # noqa: PLC0415
    from remo_cli.providers.connector import document_name as provider_document_name  # noqa: PLC0415

    if name_only:
        click.echo(provider_document_name())
        return
    provider_document()


@connector.command("enroll")
@click.argument("name")
@click.option("--activation-id", "activation_id", required=True, help="Hybrid activation ID (not a secret).")
@click.option("--region", required=True, help="AWS region the activation was created in.")
@click.option(
    "--expose",
    "expose",
    multiple=True,
    required=True,
    metavar="HOST/PROJECT",
    help="Host/project pair to expose; repeatable. HOST may be NAME itself (self-target).",
)
@click.option(
    "--run-as-user",
    "run_as_user",
    default=None,
    metavar="USER",
    help="Dedicated non-admin run-as account (default: remo-connector).",
)
@click.option(
    "--remo-version",
    "remo_version",
    default=None,
    metavar="VERSION",
    help="Pinned remo-cli version to install on the connector (default: this CLI's version).",
)
@click.option(
    "--remo-source",
    "remo_source",
    default=None,
    metavar="SPEC",
    help="PEP 508 / git spec for remo-cli (Constitution IX Tier 1 testing); mutually "
    "exclusive with --remo-version.",
)
@click.option("--verbose", "-v", "verbose", is_flag=True, default=False, help="Stream raw ansible-playbook output.")
@provider_command
def enroll_cmd(
    name: str,
    activation_id: str,
    region: str,
    expose: tuple[str, ...],
    run_as_user: str | None,
    remo_version: str | None,
    remo_source: str | None,
    verbose: bool,
) -> int:
    """Turn the registered host NAME into a connector (idempotent).

    The activation code is read from a hidden, non-echoing prompt when
    stdin is a TTY, or as one line from stdin when piped — never from a
    flag or an environment variable.
    """
    from remo_cli.core.connector import RUN_AS_USER_DEFAULT  # noqa: PLC0415
    from remo_cli.providers.connector import enroll as provider_enroll  # noqa: PLC0415

    if remo_version and remo_source:
        raise click.UsageError("--remo-version and --remo-source are mutually exclusive")

    if sys.stdin.isatty():
        code = click.prompt("Activation code", hide_input=True)
    else:
        code = sys.stdin.readline().rstrip("\n")

    return provider_enroll(
        name,
        activation_id=activation_id,
        region=region,
        expose=expose,
        run_as_user=run_as_user or RUN_AS_USER_DEFAULT,
        remo_version=remo_version,
        remo_source=remo_source,
        code=code,
        verbose=verbose,
    )


@connector.command("status")
@click.argument("name")
@provider_command
def status_cmd(name: str) -> int:
    """Show a connector's agent state, managed-node id, region, and exposed targets."""
    from remo_cli.providers.connector import status as provider_status  # noqa: PLC0415

    return provider_status(name)


@connector.command("unenroll")
@click.argument("name")
@click.option("--purge", is_flag=True, default=False, help="Also remove the state dir, install dir, and run-as user.")
@click.option("--yes", "-y", "assume_yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
@provider_command
def unenroll_cmd(name: str, purge: bool, assume_yes: bool) -> int:
    """Stop the agent and remove NAME's local connector registration material."""
    from remo_cli.core.output import confirm  # noqa: PLC0415
    from remo_cli.providers.connector import unenroll as provider_unenroll  # noqa: PLC0415

    return provider_unenroll(name, purge=purge, assume_yes=assume_yes, confirm=confirm)
