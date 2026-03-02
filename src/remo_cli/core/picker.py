"""Interactive environment picker using InquirerPy fuzzy selection."""

from __future__ import annotations

from remo_cli.models.host import KnownHost


def pick_environment(
    hosts: list[KnownHost],
    prompt: str = "Select environment: ",
) -> KnownHost:
    """Select a KnownHost interactively from a list.

    Args:
        hosts: Registered environments to choose from.
        prompt: Text shown above the fuzzy-search input.

    Returns:
        The selected :class:`KnownHost`.

    Raises:
        SystemExit: If *hosts* is empty, or if the user cancels the prompt.
    """
    if not hosts:
        raise SystemExit(
            "No remo environments registered.\n"
            "Create one with:\n"
            "  remo aws create\n"
            "  remo hetzner create\n"
            "  remo incus create <name>"
        )

    if len(hosts) == 1:
        return hosts[0]

    from InquirerPy import inquirer  # noqa: PLC0415  (lazy import)

    choices = [
        {
            "name": f"{host.type}: {host.display_name} ({host.host})",
            "value": host,
        }
        for host in hosts
    ]

    try:
        result = inquirer.fuzzy(
            message=prompt,
            choices=choices,
        ).execute()
    except KeyboardInterrupt:
        raise SystemExit(0)

    if result is None:
        raise SystemExit(0)

    return result
