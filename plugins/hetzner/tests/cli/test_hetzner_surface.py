"""FR-009 for the plugin: Hetzner's command surface, frozen in remo's
surface_baseline.py while it was a built-in, survives the move to its own
distribution unchanged. A user upgrading to the plugin types the same
commands and flags they always did."""

from __future__ import annotations

import click

from remo_cli.cli.providers.factory import build_provider_group
from remo_cli.core.provider_registry import get_descriptor

# Moved verbatim from remo's tests/unit/cli/surface_baseline.py SURFACE["hetzner"].
SURFACE: dict[str, list[str]] = {
    "create": [
        "--name",
        "--type",
        "--location",
        "--volume-size",
        "--only",
        "--skip",
        "-v",
        "--verbose",
    ],
    "destroy": [
        "--name",
        "--remove-volume",
        "--yes",
        "-y",
        "-v",
        "--verbose",
    ],
    "upgrade": ["NAME", "--only", "--skip", "-v", "--verbose"],
    "resize": ["NAME", "--volume-size", "-v", "--verbose"],
    "tag": ["NAME"],
    "list": [],
    "info": ["--name"],
    "sync": ["--all", "--yes", "-y", "--dry-run"],
    "snapshot create": ["INSTANCE", "--name", "--description"],
    "snapshot restore": ["INSTANCE", "SNAP_NAME", "--yes", "-y"],
    "snapshot delete": ["INSTANCE", "SNAP_NAME", "--yes", "-y"],
    "snapshot list": ["INSTANCE?"],
}

DEFAULT_INSTANCE_NAME = "remo"
CONFIRMABLE_COMMANDS = ["destroy", "sync", "snapshot restore", "snapshot delete"]


def _declared_strings(cmd: click.Command) -> set[str]:
    """All declaration strings (option flags/short forms, argument names) on *cmd*."""
    out: set[str] = set()
    for p in cmd.params:
        if isinstance(p, click.Argument):
            name = p.human_readable_name.upper()
            if not p.required:
                name += "?"
            out.add(name)
        elif isinstance(p, click.Option):
            out.update(p.opts)
            out.update(p.secondary_opts)
    return out


def _resolve(group: click.Group, command_path: str) -> click.Command:
    obj: click.Group | click.Command = group
    for part in command_path.split():
        assert isinstance(obj, click.Group)
        obj = obj.commands[part]
    return obj



def test_every_baseline_command_and_flag_still_exists() -> None:
    group = build_provider_group(get_descriptor("hetzner"))
    missing: list[str] = []
    for command_path, expected_flags in SURFACE.items():
        actual = _declared_strings(_resolve(group, command_path))
        missing.extend(
            f"{command_path}: missing {flag!r} (has {sorted(actual)})"
            for flag in expected_flags
            if flag not in actual
        )
    assert not missing, "\n".join(missing)


def test_default_instance_name_is_unchanged() -> None:
    assert get_descriptor("hetzner").default_instance_name == DEFAULT_INSTANCE_NAME


def test_confirmable_commands_still_accept_yes() -> None:
    group = build_provider_group(get_descriptor("hetzner"))
    for command_path in CONFIRMABLE_COMMANDS:
        assert {"--yes", "-y"} <= _declared_strings(_resolve(group, command_path)), command_path
