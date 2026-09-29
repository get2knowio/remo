"""Entry-point provider discovery (spec 027 FR-001..005) with synthetic entry points.

Every scenario the spec lists for "a broken plugin never bricks the CLI" is
driven through ``discover_entry_points`` with fake entry-point objects, so the
tests need no installed distribution beyond remo itself. The in-repo fixture
plugin is exercised separately in tests/integration/test_fixture_plugin_e2e.py.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from remo_cli.core import provider_plugins, provider_registry
from remo_cli.core.provider_plugins import (
    DISABLE_ENV_VAR,
    PluginLoadRecord,
    discover_entry_points,
    discovery_was_disabled,
    plugin_load_records,
    plugins_disabled,
)
from remo_cli.core.provider_registry import (
    PROVIDER_API_VERSION,
    ConnectionSpec,
    NameFormat,
    ProviderDescriptor,
    all_descriptors,
    builtin_descriptors,
    descriptor_source,
    is_provider_type,
)

# ---------------------------------------------------------------------------
# Synthetic entry points
# ---------------------------------------------------------------------------


@dataclass
class _Dist:
    name: str
    version: str


@dataclass
class _EntryPoint:
    """The subset of importlib.metadata.EntryPoint that discovery reads."""

    name: str
    module: str
    dist: _Dist | None
    obj: object = None
    error: Exception | None = None
    value: str = field(default="")

    def __post_init__(self) -> None:
        if not self.value:
            self.value = f"{self.module}:OBJ"

    def load(self) -> object:
        if self.error is not None:
            raise self.error
        return self.obj


def _descriptor(type_name: str) -> ProviderDescriptor:
    return ProviderDescriptor(
        type_name=type_name,
        display_name=type_name.title(),
        default_instance_name=type_name,
        name_format=NameFormat.FLAT,
        registry_fields=(),
        connection=ConnectionSpec(),
        implementation=f"{type_name}_provider.provider",
    )


def _install_module(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: object) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture
def fresh_registry() -> Iterator[None]:
    """Run each test against an empty provider registry and restore the
    process-wide state afterwards, so discovery can be re-run per test."""
    provider_registry.reset_discovery_for_tests()
    try:
        yield
    finally:
        provider_registry.reset_discovery_for_tests()


@pytest.fixture
def entry_points(monkeypatch: pytest.MonkeyPatch, fresh_registry: None) -> list[_EntryPoint]:
    """A mutable list the patched ``_iter_entry_points`` returns (already sorted
    by the production sort key, exactly as importlib's result would be)."""
    points: list[_EntryPoint] = []
    monkeypatch.setattr(
        provider_plugins,
        "_iter_entry_points",
        lambda: sorted(points, key=lambda ep: (ep.name, ep.value)),
    )
    monkeypatch.delenv(DISABLE_ENV_VAR, raising=False)
    return points


def _warnings_from(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [line for line in capsys.readouterr().out.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_descriptor_entry_point_is_registered_after_builtins(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_module(monkeypatch, "acme_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    entry_points.append(
        _EntryPoint("acme", "acme_provider.descriptor", _Dist("acme-provider", "2.0"), _descriptor("acme"))
    )

    names = [d.type_name for d in all_descriptors()]

    assert names[:4] == [d.type_name for d in builtin_descriptors()]
    assert names[4:] == ["acme"]
    assert is_provider_type("acme")
    assert descriptor_source("acme") == "acme-provider 2.0"
    assert descriptor_source("incus") == "builtin"
    [record] = plugin_load_records()
    assert record == PluginLoadRecord(
        distribution="acme-provider",
        version="2.0",
        entry_point="acme",
        type_name="acme",
        status="loaded",
        reason="",
        api_version=1,
    )


def test_factory_callable_entry_point_is_called_without_arguments(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_module(monkeypatch, "acme_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    entry_points.append(
        _EntryPoint(
            "acme", "acme_provider.descriptor", _Dist("acme-provider", "2.0"), lambda: _descriptor("acme")
        )
    )

    assert is_provider_type("acme")
    assert plugin_load_records()[0].status == "loaded"


def test_plugins_load_in_entry_point_name_order(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("zeta", "alpha", "mid"):
        _install_module(monkeypatch, f"{name}_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
        entry_points.append(
            _EntryPoint(name, f"{name}_provider.descriptor", _Dist(f"{name}-provider", "1"), _descriptor(name))
        )

    plugin_names = [d.type_name for d in all_descriptors()][4:]

    assert plugin_names == ["alpha", "mid", "zeta"]


def test_no_entry_points_means_no_records_and_builtins_only(entry_points: list[_EntryPoint]) -> None:
    assert [d.type_name for d in all_descriptors()] == [d.type_name for d in builtin_descriptors()]
    assert plugin_load_records() == ()
    assert discovery_was_disabled() is False


# ---------------------------------------------------------------------------
# Robustness: a broken plugin never bricks the CLI (FR-003)
# ---------------------------------------------------------------------------


def test_load_error_is_one_warning_naming_distribution_and_entry_point(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_module(monkeypatch, "bad_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    _install_module(monkeypatch, "good_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    entry_points.append(
        _EntryPoint(
            "bad", "bad_provider.descriptor", _Dist("bad-provider", "0.1"), error=ImportError("no such sdk")
        )
    )
    entry_points.append(
        _EntryPoint("good", "good_provider.descriptor", _Dist("good-provider", "0.2"), _descriptor("good"))
    )

    names = [d.type_name for d in all_descriptors()]
    warnings = _warnings_from(capsys)

    assert "good" in names and "bad" not in names
    assert len(warnings) == 1
    assert "bad-provider 0.1" in warnings[0] and "'bad'" in warnings[0]
    assert "ImportError: no such sdk" in warnings[0]
    bad, good = plugin_load_records()
    assert bad.status == "skipped" and bad.type_name is None and "ImportError" in bad.reason
    assert good.status == "loaded"


def test_wrong_object_type_is_skipped_with_the_type_named(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_module(monkeypatch, "odd_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    entry_points.append(_EntryPoint("odd", "odd_provider.descriptor", _Dist("odd-provider", "1"), obj=42))

    all_descriptors()
    [warning] = _warnings_from(capsys)

    assert "expected a ProviderDescriptor" in warning and "got int" in warning
    assert plugin_load_records()[0].status == "skipped"


def test_factory_returning_none_is_skipped(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_module(monkeypatch, "none_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    entry_points.append(
        _EntryPoint("none", "none_provider.descriptor", _Dist("none-provider", "1"), obj=lambda: None)
    )

    all_descriptors()
    [warning] = _warnings_from(capsys)

    assert "got NoneType" in warning


def test_descriptor_validation_error_is_skipped(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_module(monkeypatch, "shout_provider.descriptor", REMO_PROVIDER_API_VERSION=1)

    def factory() -> ProviderDescriptor:
        return _descriptor("SHOUT")  # __post_init__ rejects non-lowercase type names

    entry_points.append(_EntryPoint("shout", "shout_provider.descriptor", _Dist("shout-provider", "1"), factory))

    all_descriptors()
    [warning] = _warnings_from(capsys)

    assert "ValueError" in warning and "lowercase" in warning


def test_duplicate_of_builtin_keeps_builtin_and_names_both_distributions(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_module(monkeypatch, "dup_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    entry_points.append(_EntryPoint("dup", "dup_provider.descriptor", _Dist("dup-provider", "9"), _descriptor("aws")))

    aws = provider_registry.get_descriptor("aws")
    [warning] = _warnings_from(capsys)

    assert aws.display_name == "AWS"  # the built-in, not the plugin's "Aws"
    assert descriptor_source("aws") == "builtin"
    assert "dup-provider 9" in warning and "already registered by builtin" in warning
    assert plugin_load_records()[0].status == "skipped"


def test_duplicate_between_plugins_is_first_wins_by_entry_point_name(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for ep_name, dist in (("b-second", "second-dist"), ("a-first", "first-dist")):
        _install_module(monkeypatch, f"{dist}.descriptor", REMO_PROVIDER_API_VERSION=1)
        entry_points.append(_EntryPoint(ep_name, f"{dist}.descriptor", _Dist(dist, "1"), _descriptor("shared")))

    all_descriptors()
    [warning] = _warnings_from(capsys)

    assert descriptor_source("shared") == "first-dist 1"
    assert "second-dist 1" in warning and "already registered by first-dist 1" in warning


def test_api_version_mismatch_warns_and_still_registers(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_module(monkeypatch, "old_provider.descriptor", REMO_PROVIDER_API_VERSION=PROVIDER_API_VERSION + 5)
    entry_points.append(_EntryPoint("old", "old_provider.descriptor", _Dist("old-provider", "3"), _descriptor("old")))

    assert is_provider_type("old")
    [warning] = _warnings_from(capsys)
    assert f"version {PROVIDER_API_VERSION + 5}" in warning and f"provides {PROVIDER_API_VERSION}" in warning
    assert plugin_load_records()[0].api_version == PROVIDER_API_VERSION + 5


def test_undeclared_api_version_warns_and_still_registers(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_module(monkeypatch, "mute_provider.descriptor")  # no REMO_PROVIDER_API_VERSION
    entry_points.append(_EntryPoint("mute", "mute_provider.descriptor", _Dist("mute-provider", "3"), _descriptor("mute")))

    assert is_provider_type("mute")
    [warning] = _warnings_from(capsys)
    assert "undeclared" in warning
    assert plugin_load_records()[0].api_version is None


def test_matching_api_version_emits_no_warning(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_module(monkeypatch, "ok_provider.descriptor", REMO_PROVIDER_API_VERSION=PROVIDER_API_VERSION)
    entry_points.append(_EntryPoint("ok", "ok_provider.descriptor", _Dist("ok-provider", "1"), _descriptor("ok")))

    all_descriptors()

    assert _warnings_from(capsys) == []


def test_missing_dist_metadata_is_reported_as_unknown(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_module(monkeypatch, "loose_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    entry_points.append(_EntryPoint("loose", "loose_provider.descriptor", None, _descriptor("loose")))

    all_descriptors()

    assert descriptor_source("loose") == "unknown-distribution unknown"


# ---------------------------------------------------------------------------
# Disable switch (FR-004)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "YES", " Yes "])
def test_disable_env_var_skips_discovery(
    value: str, entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_module(monkeypatch, "acme_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    entry_points.append(_EntryPoint("acme", "acme_provider.descriptor", _Dist("acme-provider", "2.0"), _descriptor("acme")))
    monkeypatch.setenv(DISABLE_ENV_VAR, value)

    assert plugins_disabled()
    assert [d.type_name for d in all_descriptors()] == [d.type_name for d in builtin_descriptors()]
    assert plugin_load_records() == ()
    assert discovery_was_disabled() is True


@pytest.mark.parametrize("value", ["0", "", "no", "false"])
def test_other_disable_values_are_ignored(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DISABLE_ENV_VAR, value)
    assert not plugins_disabled()


# ---------------------------------------------------------------------------
# Direct call surface
# ---------------------------------------------------------------------------


def test_discover_entry_points_accepts_a_custom_warn_sink(
    entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_module(monkeypatch, "bad_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    entry_points.append(_EntryPoint("bad", "bad_provider.descriptor", _Dist("bad-provider", "1"), error=RuntimeError("boom")))
    sink: list[str] = []
    provider_registry._ensure_discovered()  # builtins registered; plugin skipped via print_warning

    records = discover_entry_points(warn=sink.append)

    assert [r.status for r in records] == ["skipped"]
    assert sink and "RuntimeError: boom" in sink[0]


def test_reset_discovery_for_tests_forgets_everything(entry_points: list[_EntryPoint], monkeypatch: pytest.MonkeyPatch) -> None:
    _install_module(monkeypatch, "acme_provider.descriptor", REMO_PROVIDER_API_VERSION=1)
    entry_points.append(_EntryPoint("acme", "acme_provider.descriptor", _Dist("acme-provider", "2.0"), _descriptor("acme")))
    assert is_provider_type("acme")

    provider_registry.reset_discovery_for_tests()
    entry_points.clear()

    assert not is_provider_type("acme")
    assert plugin_load_records() == ()
