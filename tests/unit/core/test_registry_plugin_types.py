"""Registry v2 parse side is descriptor-driven (spec 027 FR-006..009).

Built-in behavior is pinned byte-for-byte by tests/unit/core/test_registry_format.py;
these tests cover what the literal branches could not: a plugin type's entries,
a mode-field-aware plugin, an entry whose provider is not installed, and the
proxmox ``node_user`` legacy key now read through the descriptor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remo_cli.core import registry as registry_module
from remo_cli.core.provider_registry import (
    ConnectionSpec,
    NameFormat,
    ProviderDescriptor,
    temporary_registration,
)
from remo_cli.core.registry import (
    RegistryValidationError,
    entry_to_known_host,
    is_known_type,
    known_host_to_entry,
    legacy_fields_to_entry,
    read_registry,
    replace_registry,
    validate_hosts,
)
from remo_cli.models.host import KnownHost


def _plugin(type_name: str = "acme", *, mode_field_aware: bool = False, legacy: tuple = ()) -> ProviderDescriptor:
    return ProviderDescriptor(
        type_name=type_name,
        display_name=type_name.title(),
        default_instance_name=type_name,
        name_format=NameFormat.FLAT,
        registry_fields=(("instance_id", "box_id"), ("region", "zone")),
        registry_legacy_keys=legacy,
        connection=ConnectionSpec(mode_field_aware=mode_field_aware),
        implementation=f"{type_name}_provider.provider",
    )


class TestKnownTypeGate:
    def test_ssh_pseudo_type_and_builtins_are_known(self) -> None:
        assert is_known_type("ssh")
        for name in ("incus", "proxmox", "aws", "hetzner"):
            assert is_known_type(name)

    def test_unregistered_type_is_unknown_until_registered(self) -> None:
        assert not is_known_type("acme")
        with temporary_registration(_plugin("acme")):
            assert is_known_type("acme")
        assert not is_known_type("acme")

    def test_known_types_literal_is_gone(self) -> None:
        assert not hasattr(registry_module, "KNOWN_TYPES")


class TestPluginRoundTrip:
    def test_plugin_entry_round_trips_through_registry_fields(self) -> None:
        with temporary_registration(_plugin("acme")):
            host = KnownHost(
                type="acme", name="box1", host="10.0.0.9", user="remo",
                instance_id="b-1", access_mode="direct", region="z-a",
            )
            entry = known_host_to_entry(host)

            assert entry == {
                "type": "acme",
                "name": "box1",
                "host": "10.0.0.9",
                "user": "remo",
                "access": "direct",
                "acme": {"box_id": "b-1", "zone": "z-a"},
            }
            assert entry_to_known_host(entry) == host

    def test_plugin_entry_round_trips_through_the_file(self, tmp_config_dir: Path) -> None:
        with temporary_registration(_plugin("acme")):
            host = KnownHost(
                type="acme", name="box1", host="10.0.0.9", user="remo",
                instance_id="b-1", access_mode="direct", region="z-a",
            )
            replace_registry([host])
            first = (tmp_config_dir / "registry.json").read_bytes()
            view = read_registry()
            replace_registry(view.hosts)

            assert view.hosts == [host]
            assert (tmp_config_dir / "registry.json").read_bytes() == first

    def test_legacy_key_is_read_only_when_the_current_key_is_absent(self) -> None:
        with temporary_registration(_plugin("acme", legacy=(("old_zone", "region"),))):
            old = {"type": "acme", "name": "b", "host": "h", "user": "u", "access": "direct", "acme": {"old_zone": "z-old"}}
            both = {**old, "acme": {"zone": "z-new", "old_zone": "z-old"}}

            assert entry_to_known_host(old).region == "z-old"
            assert entry_to_known_host(both).region == "z-new"
            # the next write emits the current key only
            assert known_host_to_entry(entry_to_known_host(old))["acme"] == {"zone": "z-old"}

    def test_proxmox_node_user_legacy_key_still_migrates(self) -> None:
        entry = {
            "type": "proxmox",
            "name": "pve1/dev",
            "host": "10.0.0.5",
            "user": "remo",
            "access": "direct",
            "proxmox": {"vmid": "101", "node_user": "root"},
        }
        host = entry_to_known_host(entry)

        assert host is not None and host.region == "root" and host.instance_id == "101"
        assert known_host_to_entry(host)["proxmox"] == {"vmid": "101", "host_user": "root"}


class TestModeFieldAwarePlugin:
    def test_ssm_is_inferred_and_valid_for_an_aware_plugin(self) -> None:
        with temporary_registration(_plugin("cloudy", mode_field_aware=True)):
            entry = legacy_fields_to_entry("cloudy", "n", "h", "u", "i-1", "", "z")
            assert entry["access"] == "ssm"
            validate_hosts([KnownHost(type="cloudy", name="n", host="h", user="u", access_mode="ssm")])

    def test_ssm_is_rejected_for_a_non_aware_plugin_naming_the_aware_types(self) -> None:
        with temporary_registration(_plugin("plain")):
            with pytest.raises(RegistryValidationError, match=r"access 'ssm' is only valid for type 'aws'"):
                validate_hosts([KnownHost(type="plain", name="n", host="h", user="u", access_mode="ssm")])

    def test_builtin_message_is_byte_identical(self) -> None:
        with pytest.raises(RegistryValidationError) as excinfo:
            validate_hosts([KnownHost(type="incus", name="x", host="1.1.1.1", user="remo", access_mode="ssm")])
        assert str(excinfo.value) == "access 'ssm' is only valid for type 'aws' (entry: incus:x)"


class TestUninstalledProviderType:
    def test_entry_of_uninstalled_type_is_preserved_verbatim_and_warned(self, tmp_config_dir: Path) -> None:
        # Written on a machine that had the plugin; read here without it.
        with temporary_registration(_plugin("acme")):
            replace_registry(
                [
                    KnownHost(
                        type="acme", name="box1", host="10.0.0.9", user="remo",
                        instance_id="b-1", access_mode="direct", region="z-a",
                    ),
                    KnownHost(type="incus", name="pve/dev", host="10.0.0.2", user="remo", access_mode="direct"),
                ]
            )
        path = tmp_config_dir / "registry.json"
        before = json.loads(path.read_text())

        view = read_registry()
        assert [h.type for h in view.hosts] == ["incus"]
        assert any("'acme'" in w and "no installed provider" in w and "'box1'" in w for w in view.warnings)

        # a write that never saw the acme entry as a KnownHost must not drop it
        replace_registry(view.hosts)
        after = json.loads(path.read_text())
        assert [e for e in after["hosts"] if e["type"] == "acme"] == [e for e in before["hosts"] if e["type"] == "acme"]

        # and once the plugin is back, the entry reads as a KnownHost again
        with temporary_registration(_plugin("acme")):
            assert {h.type for h in read_registry().hosts} == {"acme", "incus"}
