"""Shell-completion helpers for remo CLI options.

Each public function in this module is a Click ``shell_complete=`` callback
that suggests known instance/container names from the local known-hosts
registry. They are best-effort — if the registry can't be read for any
reason, an empty list is returned and completion silently falls back to
file-name completion (Click's default behavior).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.shell_completion import CompletionItem

from remo_cli.core.known_hosts import get_known_hosts

if TYPE_CHECKING:
    import click


def _container_name(entry_name: str) -> str:
    """For host-prefixed registry names like ``lab1/dev1``, return ``dev1``."""
    _, sep, container = entry_name.partition("/")
    return container if sep else entry_name


def _safe(callback):
    """Wrap a completer so registry-read errors don't break completion."""

    def wrapper(ctx, param, incomplete):  # noqa: ANN001 - click signature
        try:
            return callback(ctx, param, incomplete)
        except Exception:
            return []

    return wrapper


@_safe
def proxmox_name(
    ctx: "click.Context", param: "click.Parameter", incomplete: str
) -> list[CompletionItem]:
    items: list[CompletionItem] = []
    for entry in get_known_hosts(type_filter="proxmox"):
        name = _container_name(entry.name)
        if name.startswith(incomplete):
            items.append(CompletionItem(name, help=entry.host or ""))
    return items


@_safe
def incus_name(
    ctx: "click.Context", param: "click.Parameter", incomplete: str
) -> list[CompletionItem]:
    items: list[CompletionItem] = []
    for entry in get_known_hosts(type_filter="incus"):
        name = _container_name(entry.name)
        if name.startswith(incomplete):
            items.append(CompletionItem(name, help=entry.host or ""))
    return items


@_safe
def aws_name(
    ctx: "click.Context", param: "click.Parameter", incomplete: str
) -> list[CompletionItem]:
    items: list[CompletionItem] = []
    for entry in get_known_hosts(type_filter="aws"):
        if entry.name.startswith(incomplete):
            items.append(CompletionItem(entry.name, help=entry.host or ""))
    return items


@_safe
def hetzner_name(
    ctx: "click.Context", param: "click.Parameter", incomplete: str
) -> list[CompletionItem]:
    items: list[CompletionItem] = []
    for entry in get_known_hosts(type_filter="hetzner"):
        if entry.name.startswith(incomplete):
            items.append(CompletionItem(entry.name, help=entry.host or ""))
    return items
