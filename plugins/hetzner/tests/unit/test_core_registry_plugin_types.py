"""Hetzner case of remo's known-type gate (moved from remo's
tests/unit/core/test_registry_plugin_types.py when hetzner left the
built-in set): once installed, the plugin's type is known to the registry,
but it is not a built-in."""

from __future__ import annotations

from remo_cli.core.provider_registry import builtin_descriptors
from remo_cli.core.registry import is_known_type


def test_installed_plugin_type_is_known_but_not_builtin() -> None:
    assert is_known_type("hetzner")
    assert "hetzner" not in {d.type_name for d in builtin_descriptors()}
