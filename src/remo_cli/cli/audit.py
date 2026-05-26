"""`remo audit <instance>` — pull the broker's audit log over SSH and render it."""

from __future__ import annotations

import json as _json
import sys

import click

from remo_cli.core import audit as audit_core
from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.output import print_error


def _resolve_instance(name: str) -> tuple[str, str]:
    """Return (host, ssh_user) for `name` from the known_hosts registry.

    Raises click.UsageError if not found.
    """
    for entry in get_known_hosts():
        if entry.name == name or entry.name.endswith(f"/{name}"):
            return entry.host, entry.user
    raise click.UsageError(f"instance {name!r} not found in known_hosts")


@click.command("audit")
@click.argument("instance")
@click.option("--tail", "tail_n", type=int, default=None, help="Show only the last N lines.")
@click.option(
    "--since", "since_str", default=None, help="Show only lines newer than DURATION (e.g. 1h, 30m)."
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit raw JSON-lines.")
def audit_command(
    instance: str,
    tail_n: int | None,
    since_str: str | None,
    as_json: bool,
) -> None:
    """Inspect the credential broker's per-instance audit log."""
    host, user = _resolve_instance(instance)
    since_td = None
    if since_str:
        try:
            since_td = audit_core.parse_duration(since_str)
        except ValueError as exc:
            print_error(str(exc))
            sys.exit(2)

    try:
        lines = audit_core.fetch(host, user, tail=tail_n, since=since_td)
    except audit_core.AuditError as exc:
        print_error(str(exc))
        sys.exit(8)

    if as_json:
        for ln in lines:
            click.echo(_json.dumps(ln.raw, separators=(",", ":")))
        sys.exit(0)

    click.echo(audit_core.render_table(lines))
    sys.exit(0)
