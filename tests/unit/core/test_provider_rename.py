"""A renamed provider: ``legacy_type_names`` and ``deprecated_cli_aliases`` (#258).

Uses a made-up ``oldtype`` -> ``newtype`` rename so core's mechanism is proven
without depending on any real provider (AWS's ``aws`` -> ``ec2`` is its first
user, in the ec2 plugin). Every path that can carry the old type migrates once
and idempotently (Principle VII): a v2 registry file, a legacy known_hosts
line, and a push-cache merge base.
"""

from __future__ import annotations

import json

import click
import pytest
from click.testing import CliRunner

from remo_cli.core import registry
from remo_cli.core.provider_registry import (
    ConnectionSpec,
    NameFormat,
    ProviderDescriptor,
    canonical_type_name,
    temporary_registration,
)
from tests.conftest import write_legacy_registry, write_v2_registry

RENAMED = ProviderDescriptor(
    type_name="newtype",
    display_name="New Type",
    default_instance_name="",
    name_format=NameFormat.FLAT,
    registry_fields=(("instance_id", "instance_id"), ("region", "region")),
    connection=ConnectionSpec(mode_field_aware=True),
    implementation="remo_cli.providers._nonexistent_newtype_module",
    legacy_type_names=("oldtype",),
    deprecated_cli_aliases=("oldtype",),
)

OLD_ENTRY = {
    "type": "oldtype",
    "name": "box",
    "host": "203.0.113.7",
    "user": "remo",
    "access": "ssm",
    "oldtype": {"instance_id": "i-0abc", "region": "us-east-1"},
}


@pytest.fixture
def renamed():
    with temporary_registration(RENAMED):
        yield RENAMED


def _registry_doc(config_dir) -> dict:
    return json.loads((config_dir / "registry.json").read_text())


@pytest.mark.usefixtures("renamed")
class TestCanonicalize:
    def test_legacy_name_maps_to_current_type(self):
        assert canonical_type_name("oldtype") == "newtype"
        assert canonical_type_name("newtype") == "newtype"
        assert canonical_type_name("never-registered") == "never-registered"

    def test_entry_type_and_nested_key_are_renamed(self):
        renamed = registry.canonicalize_entry(OLD_ENTRY)
        assert renamed["type"] == "newtype"
        assert renamed["newtype"] == {"instance_id": "i-0abc", "region": "us-east-1"}
        assert "oldtype" not in renamed
        assert OLD_ENTRY["type"] == "oldtype"  # input untouched

    def test_second_pass_is_the_same_object(self):
        once = registry.canonicalize_entry(OLD_ENTRY)
        assert registry.canonicalize_entry(once) is once

    def test_ssh_and_unknown_types_pass_through(self):
        ssh = {"type": "ssh", "name": "n", "host": "h", "user": "u", "access": "direct"}
        other = {**ssh, "type": "madeup"}
        assert registry.canonicalize_entry(ssh) is ssh
        assert registry.canonicalize_entry(other) is other


@pytest.mark.usefixtures("renamed")
class TestRegistryMigration:
    def test_v2_entry_reads_as_current_type_with_its_fields(self, tmp_config_dir):
        write_v2_registry(tmp_config_dir, [OLD_ENTRY])
        (host,) = registry.read_registry().hosts
        assert (host.type, host.instance_id, host.region, host.access_mode) == (
            "newtype",
            "i-0abc",
            "us-east-1",
            "ssm",
        )

    def test_next_write_emits_current_type_once(self, tmp_config_dir):
        write_v2_registry(tmp_config_dir, [OLD_ENTRY])
        registry.mutate_registry(lambda hosts: hosts)
        first = _registry_doc(tmp_config_dir)
        assert [e["type"] for e in first["hosts"]] == ["newtype"]
        assert first["hosts"][0]["newtype"] == {"instance_id": "i-0abc", "region": "us-east-1"}

        registry.mutate_registry(lambda hosts: hosts)
        assert _registry_doc(tmp_config_dir) == first

    def test_legacy_known_hosts_line_migrates_to_current_type(self, tmp_config_dir):
        write_legacy_registry(tmp_config_dir, ["oldtype:box:203.0.113.7:remo:i-0abc:ssm:us-east-1"])
        (host,) = registry.read_registry().hosts
        assert (host.type, host.instance_id, host.region) == ("newtype", "i-0abc", "us-east-1")
        assert [e["type"] for e in _registry_doc(tmp_config_dir)["hosts"]] == ["newtype"]


def test_uninstalled_renamed_provider_preserves_old_entry(tmp_config_dir):
    """No descriptor claims ``oldtype``: the entry survives a write unchanged."""
    write_v2_registry(tmp_config_dir, [OLD_ENTRY])
    view = registry.read_registry()
    assert view.hosts == []
    registry.mutate_registry(lambda hosts: hosts)
    assert _registry_doc(tmp_config_dir)["hosts"] == [OLD_ENTRY]


@pytest.mark.usefixtures("renamed")
def test_push_cache_merge_base_and_fingerprint_follow_the_rename():
    from remo_cli.core.web_adopt import _parse_instances, instance_fingerprint

    stale = {
        "box": {
            "fingerprint": "f" * 64,
            "host_keys": [],
            "type": "oldtype",
            "entry": OLD_ENTRY,
        }
    }
    cached = _parse_instances(stale)["box"]
    host = registry.entry_to_known_host(OLD_ENTRY)
    assert host is not None
    assert cached.type == "newtype"
    assert cached.entry == registry.canonicalize_entry(OLD_ENTRY)
    # The re-derived fingerprint is exactly what a fresh push would record,
    # so `remo web status` sees no drift from the rename alone.
    assert cached.fingerprint == instance_fingerprint(host)

    # Idempotent: a cache already carrying the new type loads unchanged.
    fresh = {"box": {**stale["box"], "type": "newtype", "entry": cached.entry,
                     "fingerprint": cached.fingerprint}}
    again = _parse_instances(fresh)["box"]
    assert (again.type, again.entry, again.fingerprint) == (
        cached.type,
        cached.entry,
        cached.fingerprint,
    )


class TestRegistrationConflicts:
    def test_legacy_name_may_not_shadow_a_registered_type(self):
        clash = ProviderDescriptor(
            type_name="clash",
            display_name="Clash",
            default_instance_name="",
            name_format=NameFormat.FLAT,
            registry_fields=(),
            connection=ConnectionSpec(),
            implementation="x",
            legacy_type_names=("incus",),
        )
        with pytest.raises(ValueError, match="'incus' is already claimed"):
            with temporary_registration(clash):
                pass

    def test_type_may_not_reuse_another_providers_legacy_name(self, renamed):
        squatter = ProviderDescriptor(
            type_name="oldtype",
            display_name="Old",
            default_instance_name="",
            name_format=NameFormat.FLAT,
            registry_fields=(),
            connection=ConnectionSpec(),
            implementation="x",
        )
        with pytest.raises(ValueError, match="already a legacy name of 'newtype'"):
            with temporary_registration(squatter):
                pass


class TestDeprecatedCliAlias:
    def _cli(self) -> click.Group:
        from remo_cli.cli.providers.factory import (
            build_deprecated_alias_groups,
            build_provider_group,
        )

        root = click.Group("remo")
        root.add_command(build_provider_group(RENAMED))
        for group in build_deprecated_alias_groups(RENAMED):
            root.add_command(group)
        return root

    def test_alias_is_hidden_and_has_the_same_verbs(self, renamed):
        root = self._cli()
        alias, current = root.commands["oldtype"], root.commands["newtype"]
        assert alias.hidden and not current.hidden
        assert sorted(alias.commands) == sorted(current.commands)  # type: ignore[attr-defined]
        assert "oldtype" not in CliRunner().invoke(root, ["--help"]).output

    def test_alias_warns_then_runs_the_verb(self, renamed):
        result = CliRunner().invoke(self._cli(), ["oldtype", "list", "--help"])
        assert result.exit_code == 0, result.output
        assert "`remo oldtype` is deprecated" in result.stderr
        assert "use `remo newtype`" in result.stderr
        assert "Usage:" in result.stdout
