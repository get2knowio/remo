"""Conformance gate for the provider abstraction (contracts/provider-protocol.md).

Parametrized over every registered built-in descriptor plus a FakeProvider
(registered only for the duration of each test via
``provider_registry.temporary_registration``). Proves:

1. Protocol Part A satisfaction (module has the expected callables).
2. Descriptor <-> implementation signature agreement for create/destroy/
   update/info/sync/extra_commands (R-B1).
3. The Protocol Part A entry-based surface never calls ``sys.exit`` (R-A1) —
   ahead of the full Phase 5 zero-tolerance gate, which covers the
   heterogeneous create/destroy/update verbs too.
4. FakeProvider's full command group mounts with zero modifications to any
   existing CLI/provider file (SC-001).
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Iterator

import click
import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.factory import build_provider_group
from remo_cli.core.provider_registry import (
    all_descriptors,
    get_descriptor,
    get_provider,
    temporary_registration,
)
from tests.unit.providers import fake_provider
from tests.unit.providers.fake_provider import DESCRIPTOR as FAKE_DESCRIPTOR

_BUILTIN_TYPE_NAMES = [d.type_name for d in all_descriptors()]
ALL_TYPE_NAMES = [*_BUILTIN_TYPE_NAMES, "fake"]

PROTOCOL_PART_A_ENTRY_VERBS = (
    "update_entry",
    "snapshot_create",
    "snapshot_restore",
    "snapshot_delete",
    "snapshot_list",
)


@pytest.fixture
def registered(request: pytest.FixtureRequest) -> Iterator[None]:
    """Ensure `fake` is registered (no-op for real providers)."""
    if request.node.callspec.params.get("type_name") == "fake":
        with temporary_registration(FAKE_DESCRIPTOR):
            yield
    else:
        yield


# ---------------------------------------------------------------------------
# 1. Protocol Part A satisfaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type_name", ALL_TYPE_NAMES)
def test_protocol_part_a_entry_verbs_present(type_name: str, registered: None) -> None:
    module = get_provider(type_name)
    for verb in PROTOCOL_PART_A_ENTRY_VERBS:
        assert callable(getattr(module, verb, None)), f"{type_name} is missing Protocol verb {verb!r}"
    assert callable(getattr(module, "probe", None) or getattr(module, "sync", None)), (
        f"{type_name} has neither probe() nor sync() (Spec-016 seam)"
    )


@pytest.mark.parametrize("type_name", ALL_TYPE_NAMES)
def test_teardown_present(type_name: str, registered: None) -> None:
    module = get_provider(type_name)
    assert callable(getattr(module, "teardown", None))


# ---------------------------------------------------------------------------
# 2. Descriptor <-> implementation signature agreement (R-B1)
# ---------------------------------------------------------------------------


def _option_param_names(cmd: click.Command) -> set[str]:
    return {
        p.name
        for p in cmd.params
        if isinstance(p, (click.Option, click.Argument)) and p.name is not None
    }


@pytest.mark.parametrize("type_name", ALL_TYPE_NAMES)
def test_descriptor_signature_conformance(type_name: str, registered: None) -> None:
    descriptor = get_descriptor(type_name)
    module = get_provider(type_name)
    group = build_provider_group(descriptor)

    verbs = ["create", "upgrade", "resize", "info", "sync"]
    if descriptor.supports_managed_marker:
        verbs.append("tag")
    for verb in verbs:
        cmd = group.commands[verb]
        impl = getattr(module, verb)
        sig_params = set(inspect.signature(impl).parameters)
        click_params = _option_param_names(cmd)
        if verb == "create":
            # --yes is accepted (deprecated) but never forwarded to impl.
            click_params.discard("auto_confirm")
        assert click_params <= sig_params, (
            f"{type_name} {verb}: CLI declares params impl doesn't accept: {click_params - sig_params}"
        )
        assert sig_params <= click_params, (
            f"{type_name} {verb}: impl requires params CLI doesn't supply: {sig_params - click_params}"
        )

    # destroy is special-cased: once a provider migrates to the shared
    # destroy template (core/lifecycle.run_destroy, T038), the CLI's
    # "destroy" command no longer calls module.destroy(**kwargs) directly —
    # it forwards only descriptor.destroy_options (plus the always-injected
    # verbose) to module.teardown(entry, verbose=..., **kwargs); name/
    # auto_confirm are consumed by the template itself and never forwarded
    # (R-A3). Providers still mid-migration (transitional window,
    # contracts/lifecycle-templates.md) keep the legacy destroy() check.
    destroy_cmd = group.commands["destroy"]
    teardown = getattr(module, "teardown", None)
    if teardown is not None:
        teardown_sig = inspect.signature(teardown).parameters
        has_var_keyword = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in teardown_sig.values()
        )
        # A bare **provider_opts (VAR_KEYWORD) absorbs any descriptor-declared
        # destroy_options without needing to be named explicitly, and is
        # itself never "required" -- exclude it both ways.
        sig_params = {
            name
            for name, p in teardown_sig.items()
            if name != "entry" and p.kind is not inspect.Parameter.VAR_KEYWORD
        }
        click_params = _option_param_names(destroy_cmd) - {"name", "auto_confirm"}
        if not has_var_keyword:
            assert click_params <= sig_params, (
                f"{type_name} destroy: CLI declares params teardown() doesn't accept: "
                f"{click_params - sig_params}"
            )
        assert sig_params <= click_params, (
            f"{type_name} destroy: teardown() requires params CLI doesn't supply: "
            f"{sig_params - click_params}"
        )
    else:
        impl = getattr(module, "destroy")
        sig_params = set(inspect.signature(impl).parameters)
        click_params = _option_param_names(destroy_cmd)
        assert click_params <= sig_params, (
            f"{type_name} destroy: CLI declares params impl doesn't accept: {click_params - sig_params}"
        )
        assert sig_params <= click_params, (
            f"{type_name} destroy: impl requires params CLI doesn't supply: {sig_params - click_params}"
        )

    for spec in descriptor.extra_commands:
        cmd = group.commands[spec.name]
        impl = getattr(module, spec.impl)
        sig_params = set(inspect.signature(impl).parameters)
        click_params = _option_param_names(cmd)
        assert click_params == sig_params, (
            f"{type_name} {spec.name}: signature mismatch (cli={click_params}, impl={sig_params})"
        )

    if descriptor.host_commands:
        host_group = group.commands["host"]
        assert isinstance(host_group, click.Group)
        for spec in descriptor.host_commands:
            cmd = host_group.commands[spec.name]
            impl = getattr(module, spec.impl)
            sig_params = set(inspect.signature(impl).parameters)
            click_params = _option_param_names(cmd)
            assert click_params == sig_params, (
                f"{type_name} host {spec.name}: signature mismatch (cli={click_params}, impl={sig_params})"
            )


# ---------------------------------------------------------------------------
# 3. No SystemExit from the entry-based Protocol surface (R-A1, partial)
# ---------------------------------------------------------------------------


def _calls_sys_exit(fn: object) -> bool:
    source = textwrap.dedent(inspect.getsource(fn))  # type: ignore[arg-type]
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "exit"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "sys"
        ):
            return True
    return False


@pytest.mark.parametrize("type_name", ALL_TYPE_NAMES)
def test_protocol_part_a_never_calls_sys_exit(type_name: str, registered: None) -> None:
    module = get_provider(type_name)
    for verb in PROTOCOL_PART_A_ENTRY_VERBS:
        fn = getattr(module, verb, None)
        if fn is None:
            continue
        assert not _calls_sys_exit(fn), f"{type_name}.{verb} must not call sys.exit (R-A1)"


# ---------------------------------------------------------------------------
# 4. FakeProvider proves SC-001: full command group, zero existing files touched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type_name", _BUILTIN_TYPE_NAMES)
def test_tag_and_host_absent_when_unsupported(type_name: str) -> None:
    descriptor = get_descriptor(type_name)
    group = build_provider_group(descriptor)
    if not descriptor.supports_managed_marker:
        assert "tag" not in group.commands
    if not descriptor.host_commands:
        assert "host" not in group.commands


def test_fake_provider_full_group_mounts_with_no_existing_files_touched() -> None:
    with temporary_registration(FAKE_DESCRIPTOR):
        descriptor = get_descriptor("fake")
        group = build_provider_group(descriptor)
        runner = CliRunner()

        result = runner.invoke(group, ["--help"])
        assert result.exit_code == 0, result.output

        for command_name in (
            "create", "destroy", "upgrade", "resize", "tag", "list", "info", "sync", "snapshot", "host",
        ):
            assert command_name in group.commands

        assert "prep" in group.commands["host"].commands

        fake_provider.reset()
        try:
            r = runner.invoke(group, ["create", "--name", "fake1"])
            assert r.exit_code == 0, r.output

            r = runner.invoke(group, ["info", "--name", "fake1"])
            assert r.exit_code == 0, r.output

            r = runner.invoke(group, ["tag", "fake1"])
            assert r.exit_code == 0, r.output

            r = runner.invoke(group, ["upgrade", "fake1"])
            assert r.exit_code == 0, r.output

            r = runner.invoke(group, ["resize", "fake1", "--volume-size", "20"])
            assert r.exit_code == 0, r.output

            r = runner.invoke(group, ["host", "prep"])
            assert r.exit_code == 0, r.output

            r = runner.invoke(group, ["destroy", "--name", "fake1", "--yes"])
            assert r.exit_code == 0, r.output
        finally:
            fake_provider.reset()
