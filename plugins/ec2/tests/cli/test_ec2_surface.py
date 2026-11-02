"""FR-009 for the plugin: AWS's command surface, frozen in remo's
surface_baseline.py while it was the built-in ``aws``, survives the move to
its own distribution and the rename to ``ec2`` unchanged -- under ``remo ec2``
and under the deprecated ``remo aws`` alias alike (#258)."""

from __future__ import annotations

import click
import pytest

from remo_cli.cli.providers.factory import build_provider_group
from remo_cli.core.provider_registry import get_descriptor

# Moved verbatim from remo's tests/unit/cli/surface_baseline.py SURFACE["aws"].
SURFACE: dict[str, list[str]] = {
    "create": [
        "--name",
        "--type",
        "--region",
        "--volume-size",
        "--spot",
        "--iam-profile",
        "--only",
        "--skip",
        "-v",
        "--verbose",
    ],
    "destroy": ["--name", "--remove-storage", "--yes", "-y", "-v", "--verbose"],
    "upgrade": ["NAME", "--only", "--skip", "-v", "--verbose"],
    "resize": ["NAME", "--volume-size", "-v", "--verbose"],
    "list": [],
    "sync": ["--region", "--all", "--yes", "-y", "--dry-run"],
    "stop": ["--name", "--yes", "-y"],
    "start": ["--name"],
    "reboot": ["--name", "--yes", "-y"],
    "info": ["--name"],
    "snapshot create": ["INSTANCE", "--name", "--description", "--region"],
    "snapshot restore": ["INSTANCE", "SNAP_NAME", "--yes", "-y", "--region"],
    "snapshot delete": ["INSTANCE", "SNAP_NAME", "--yes", "-y", "--region"],
    "snapshot list": ["INSTANCE?", "--region"],
}

CONFIRMABLE_COMMANDS = ["destroy", "sync", "stop", "reboot", "snapshot restore", "snapshot delete"]


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



def _groups() -> dict[str, click.Group]:
    from remo_cli.cli.main import cli

    return {name: cli.commands[name] for name in ("ec2", "aws")}  # type: ignore[misc]


@pytest.mark.parametrize("spelling", ["ec2", "aws"])
def test_every_baseline_command_and_flag_still_exists(spelling: str) -> None:
    group = _groups()[spelling]
    missing: list[str] = []
    for command_path, expected_flags in SURFACE.items():
        actual = _declared_strings(_resolve(group, command_path))
        missing.extend(
            f"{command_path}: missing {flag!r} (has {sorted(actual)})"
            for flag in expected_flags
            if flag not in actual
        )
    assert not missing, "\n".join(missing)


def test_default_instance_name_is_still_the_login_user() -> None:
    from remo_cli.core.provider_registry import LOGIN_USER

    assert get_descriptor("ec2").default_instance_name is LOGIN_USER


def test_confirmable_commands_still_accept_yes() -> None:
    group = build_provider_group(get_descriptor("ec2"))
    for command_path in CONFIRMABLE_COMMANDS:
        assert {"--yes", "-y"} <= _declared_strings(_resolve(group, command_path)), command_path
