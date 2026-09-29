"""`remo providers` — list every provider remo knows about and where it came from (027 FR-012).

Click wiring only. The facts come from ``core/provider_registry`` (descriptors
and their source) and ``core/provider_plugins`` (per-entry-point load records,
including plugins that were skipped and why), so an operator with a broken
plugin can see what failed without reading source.
"""

from __future__ import annotations

import click


@click.command()
def providers() -> None:
    """List the registered providers (built-in and `remo.providers` plugins)."""
    from remo_cli.core.provider_plugins import (  # noqa: PLC0415
        DISABLE_ENV_VAR,
        discovery_was_disabled,
        plugin_load_records,
    )
    from remo_cli.core.provider_registry import (  # noqa: PLC0415
        PROVIDER_API_VERSION,
        all_descriptors,
        descriptor_source,
    )

    rows = [
        (d.type_name, d.display_name, descriptor_source(d.type_name), "loaded")
        for d in all_descriptors()
    ]
    skipped = [r for r in plugin_load_records() if r.status == "skipped"]

    widths = [
        max(len(row[i]) for row in rows + [("TYPE", "NAME", "SOURCE", "STATUS")]) for i in range(4)
    ]
    header = ("TYPE", "NAME", "SOURCE", "STATUS")
    click.echo("  ".join(h.ljust(widths[i]) for i, h in enumerate(header)).rstrip())
    for row in rows:
        click.echo("  ".join(col.ljust(widths[i]) for i, col in enumerate(row)).rstrip())

    if skipped:
        click.echo("")
        click.echo("Skipped plugins:")
        for record in skipped:
            click.echo(f"  {record.entry_point} ({record.source}): {record.reason}")
    if discovery_was_disabled():
        click.echo("")
        click.echo(f"Plugin discovery is disabled ({DISABLE_ENV_VAR} is set).")
    click.echo("")
    click.echo(f"Provider API version: {PROVIDER_API_VERSION}")
