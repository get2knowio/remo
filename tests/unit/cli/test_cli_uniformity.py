"""SC-002: shared CLI options are identical across all four providers.

Shared options are built from the same canonical OptionSpec catalog objects
(core/provider_registry.py) by the factory, so identical flags are identical
by construction — this test locks that down and would catch any future
descriptor/factory change that lets them drift.
"""

from __future__ import annotations

import types

import click
from click.testing import CliRunner

from remo_cli.cli.providers.factory import build_provider_group
from remo_cli.core.provider_registry import all_descriptors

ALL_TYPE_NAMES = [d.type_name for d in all_descriptors()]


def _option(cmd: click.Command, param_name: str) -> click.Option:
    for p in cmd.params:
        if isinstance(p, click.Option) and p.name == param_name:
            return p
    raise AssertionError(f"{cmd.name} has no option with param name {param_name!r}")


def _shape(opt: click.Option) -> tuple[object, ...]:
    return (tuple(opt.opts), tuple(opt.secondary_opts), opt.help, type(opt.type), opt.is_flag)


def test_shared_options_identical_shape_across_providers() -> None:
    groups = {t: build_provider_group(get_descriptor_by_type(t)) for t in ALL_TYPE_NAMES}

    # (command, param) pairs that every provider's generated command shares
    # verbatim (same OptionSpec catalog object -> same shape everywhere).
    shared = [
        ("create", "volume_size"),
        ("create", "tools_only"),
        ("create", "tools_skip"),
        ("create", "verbose"),
        ("update", "volume_size"),
        ("update", "tools_only"),
        ("update", "tools_skip"),
        ("update", "verbose"),
        ("destroy", "auto_confirm"),
        ("destroy", "verbose"),
        ("sync", "auto_confirm"),
        ("sync", "dry_run"),
        ("sync", "include_all"),
    ]
    for command_name, param_name in shared:
        shapes = {t: _shape(_option(groups[t].commands[command_name], param_name)) for t in ALL_TYPE_NAMES}
        distinct = set(shapes.values())
        assert len(distinct) == 1, f"{command_name}/{param_name} differs across providers: {shapes}"


def get_descriptor_by_type(type_name: str):
    from remo_cli.core.provider_registry import get_descriptor

    return get_descriptor(type_name)


def test_name_option_same_flag_no_short_form_everywhere() -> None:
    for type_name in ALL_TYPE_NAMES:
        group = build_provider_group(get_descriptor_by_type(type_name))
        for command_name in ("create", "destroy", "update", "info"):
            opt = _option(group.commands[command_name], "name")
            assert opt.opts == ["--name"], f"{type_name} {command_name}: {opt.opts}"
            assert opt.secondary_opts == []


def test_create_help_shows_default_instance_name() -> None:
    for type_name in ALL_TYPE_NAMES:
        descriptor = get_descriptor_by_type(type_name)
        group = build_provider_group(descriptor)
        opt = _option(group.commands["create"], "name")
        if isinstance(descriptor.default_instance_name, str):
            assert descriptor.default_instance_name in (opt.help or "")
        else:
            assert "$USER" in (opt.help or "")


def test_destroy_accepts_yes_and_short_y_uniformly() -> None:
    for type_name in ALL_TYPE_NAMES:
        group = build_provider_group(get_descriptor_by_type(type_name))
        opt = _option(group.commands["destroy"], "auto_confirm")
        assert set(opt.opts) == {"--yes", "-y"}


def test_create_yes_is_rejected_as_unknown_option() -> None:
    """019-hygiene-deps-docs US5: `--yes` never had any effect on `create`
    (there is no confirmation prompt to skip) and has been removed from the
    option surface entirely. See contracts/cli-surface-delta.md V-1: exit 2,
    "No such option: --yes" — Click's standard unknown-option behavior."""
    for type_name in ALL_TYPE_NAMES:
        descriptor = get_descriptor_by_type(type_name)
        group = build_provider_group(descriptor)

        fake_module = types.ModuleType(f"fake_{type_name}")
        fake_module.create = lambda **kwargs: 0  # type: ignore[attr-defined]

        import remo_cli.core.provider_registry as pr

        pr._MODULE_CACHE[type_name] = fake_module
        try:
            runner = CliRunner()
            result = runner.invoke(group, ["create", "--yes"])
            assert result.exit_code == 2, result.output
            assert "No such option: --yes" in result.output
        finally:
            pr._MODULE_CACHE.pop(type_name, None)
