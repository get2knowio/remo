"""FR-009: every command/flag recorded in the pre-refactor CLI baseline
(surface_baseline.py, captured in T002 from the hand-written CLI modules
before they were deleted) still exists on the generated CLI."""

from __future__ import annotations

import click

from remo_cli.cli.providers.factory import build_provider_group
from remo_cli.core.provider_registry import get_descriptor
from tests.unit.cli.surface_baseline import SURFACE


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
    missing: list[str] = []
    for type_name, commands in SURFACE.items():
        group = build_provider_group(get_descriptor(type_name))
        for command_path, expected_flags in commands.items():
            cmd = _resolve(group, command_path)
            actual = _declared_strings(cmd)
            for flag in expected_flags:
                if flag not in actual:
                    missing.append(f"{type_name} {command_path}: missing {flag!r} (has {sorted(actual)})")
    assert not missing, "\n".join(missing)


def test_every_baseline_command_exists() -> None:
    missing: list[str] = []
    for type_name, commands in SURFACE.items():
        group = build_provider_group(get_descriptor(type_name))
        for command_path in commands:
            try:
                _resolve(group, command_path)
            except KeyError:
                missing.append(f"{type_name}: missing command {command_path!r}")
    assert not missing, "\n".join(missing)
