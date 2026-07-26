"""Tests for the generic provider-descriptor registry (018-provider-abstraction, T007).

Covers, per Constitution Principle II (enumerate branches, don't sample):
- register()/get_descriptor()/all_descriptors() happy path (T007.1).
- Duplicate type_name rejection (T007.2).
- UnknownProviderError message naming the unknown type (T007.3).
- is_provider_type() True/False branches (T007.4).
- get_provider() lazy import + memoization (T007.5).
- MissingDependencyError translation when sdk_extra is set (T007.6).
- Raw ImportError propagation when sdk_extra is None (T007.7).
- temporary_registration() cleanup, including on exception (T007.8).
- ProviderDescriptor.__post_init__ validation: type_name and duplicate
  option names within a single command (T007.9).

All tests use ``temporary_registration()`` (or restore ``_REGISTRY``/
``_MODULE_CACHE`` directly) so nothing leaks into other test modules that
run in the same process.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from remo_cli.core.errors import MissingDependencyError, PreconditionError
from remo_cli.core.provider_registry import (
    ConnectionSpec,
    NameFormat,
    OptionSpec,
    ProviderDescriptor,
    UnknownProviderError,
    all_descriptors,
    get_descriptor,
    get_provider,
    is_provider_type,
    register,
    temporary_registration,
)

pytestmark = pytest.mark.usefixtures("_no_real_builtins")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_builtins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent the lazy ``providers.builtin`` import from running.

    That module doesn't exist yet (created in a later task, T013). Every
    lookup helper (``get_descriptor``/``all_descriptors``/``is_provider_type``)
    calls ``_ensure_builtins_imported()`` first; short-circuit it so tests
    exercise only the descriptor-registry logic under test, not an import of
    a module that isn't there.
    """
    import remo_cli.core.provider_registry as pr

    monkeypatch.setattr(pr, "_builtins_imported", True)


def _make_descriptor(
    type_name: str = "fakeprovider",
    *,
    implementation: str = "remo_cli.core.provider_registry",
    sdk_extra: str | None = None,
    create_options: tuple[OptionSpec, ...] = (),
) -> ProviderDescriptor:
    return ProviderDescriptor(
        type_name=type_name,
        display_name=type_name.title(),
        default_instance_name="dev1",
        name_format=NameFormat.FLAT,
        registry_fields=(),
        connection=ConnectionSpec(),
        implementation=implementation,
        sdk_extra=sdk_extra,
        create_options=create_options,
    )


@pytest.fixture
def fake_descriptor() -> Iterator[ProviderDescriptor]:
    descriptor = _make_descriptor()
    with temporary_registration(descriptor) as registered:
        yield registered


# ---------------------------------------------------------------------------
# Registration / lookup
# ---------------------------------------------------------------------------


def test_register_findable_via_get_descriptor(fake_descriptor: ProviderDescriptor) -> None:
    assert get_descriptor("fakeprovider") is fake_descriptor


def test_register_findable_via_all_descriptors(fake_descriptor: ProviderDescriptor) -> None:
    assert fake_descriptor in all_descriptors()


def test_duplicate_type_name_raises_value_error(fake_descriptor: ProviderDescriptor) -> None:
    duplicate = _make_descriptor()
    with pytest.raises(ValueError, match="already registered"):
        register(duplicate)


# ---------------------------------------------------------------------------
# Unknown provider type
# ---------------------------------------------------------------------------


def test_get_descriptor_unknown_type_raises_unknown_provider_error() -> None:
    with pytest.raises(UnknownProviderError, match="totally-unknown-xyz"):
        get_descriptor("totally-unknown-xyz")


def test_unknown_provider_error_is_precondition_error() -> None:
    assert issubclass(UnknownProviderError, PreconditionError)


# ---------------------------------------------------------------------------
# is_provider_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type_name", ["ssh", "totally-made-up-provider"])
def test_is_provider_type_false_for_unregistered(type_name: str) -> None:
    assert is_provider_type(type_name) is False


def test_is_provider_type_true_for_temporarily_registered(fake_descriptor: ProviderDescriptor) -> None:
    assert is_provider_type(fake_descriptor.type_name) is True


# ---------------------------------------------------------------------------
# get_provider: lazy import + memoization
# ---------------------------------------------------------------------------


def test_get_provider_lazy_imports_and_memoizes(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    fake_module = types.ModuleType("fake_impl_module_for_test")
    import_calls: list[str] = []

    import remo_cli.core.provider_registry as pr

    real_import_module = pr.importlib.import_module

    def fake_import_module(name: str) -> object:
        if name == "fake_impl_module_for_test":
            import_calls.append(name)
            return fake_module
        return real_import_module(name)

    monkeypatch.setattr(pr.importlib, "import_module", fake_import_module)

    descriptor = _make_descriptor(implementation="fake_impl_module_for_test")
    with temporary_registration(descriptor):
        first = get_provider(descriptor.type_name)
        second = get_provider(descriptor.type_name)

    assert first is fake_module
    assert second is fake_module
    assert import_calls == ["fake_impl_module_for_test"]  # imported exactly once


# ---------------------------------------------------------------------------
# get_provider: MissingDependencyError vs raw ImportError
# ---------------------------------------------------------------------------


def test_get_provider_missing_sdk_extra_raises_missing_dependency_error() -> None:
    descriptor = _make_descriptor(
        implementation="remo_cli.this_module_does_not_exist_at_all",
        sdk_extra="somepkg",
    )
    with temporary_registration(descriptor):
        with pytest.raises(MissingDependencyError, match="somepkg") as excinfo:
            get_provider(descriptor.type_name)
    assert isinstance(excinfo.value.__cause__, ImportError)


def test_get_provider_without_sdk_extra_propagates_raw_import_error() -> None:
    descriptor = _make_descriptor(
        implementation="remo_cli.this_module_does_not_exist_at_all",
        sdk_extra=None,
    )
    with temporary_registration(descriptor):
        with pytest.raises(ImportError):
            get_provider(descriptor.type_name)


# ---------------------------------------------------------------------------
# temporary_registration
# ---------------------------------------------------------------------------


def test_temporary_registration_registers_and_unregisters() -> None:
    descriptor = _make_descriptor()
    assert is_provider_type(descriptor.type_name) is False
    with temporary_registration(descriptor):
        assert is_provider_type(descriptor.type_name) is True
    assert is_provider_type(descriptor.type_name) is False


def test_temporary_registration_unregisters_on_exception() -> None:
    descriptor = _make_descriptor()

    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with temporary_registration(descriptor):
            assert is_provider_type(descriptor.type_name) is True
            raise _Boom("kaboom")

    assert is_provider_type(descriptor.type_name) is False


# ---------------------------------------------------------------------------
# ProviderDescriptor.__post_init__ validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_type_name", ["", "UpperCase", "Mixed-Case"])
def test_post_init_rejects_bad_type_name(bad_type_name: str) -> None:
    with pytest.raises(ValueError, match="type_name"):
        _make_descriptor(type_name=bad_type_name)


def test_post_init_rejects_duplicate_option_names_in_same_command() -> None:
    dup_options = (
        OptionSpec(name="--host", param="host_a", default=""),
        OptionSpec(name="--host", param="host_b", default=""),
    )
    with pytest.raises(ValueError, match="duplicate option names"):
        _make_descriptor(create_options=dup_options)
