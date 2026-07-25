"""Foundational tests for the v2 registry file format (T010) plus value-
fidelity tests for IPv6/colon-containing values (T019, US2).

Covers: round-trip fidelity, deterministic serialization, unknown-type
preservation, newer-version rejection, tolerant-read warnings, and
validation rules V2-V6 (data-model.md §5).
"""

from __future__ import annotations

import json

import pytest

from remo_cli.core.config import get_registry_path
from remo_cli.core.registry import (
    RegistryNewerVersionError,
    RegistryValidationError,
    mutate_registry,
    read_registry,
    replace_registry,
    validate_hosts,
)
from remo_cli.models.host import KnownHost
from tests.conftest import build_v2_host_entry, write_v2_registry


def _all_type_hosts() -> list[KnownHost]:
    """One host per known type, exercising every nested field shape."""
    return [
        KnownHost(
            type="incus",
            name="nuc/dev1",
            host="dev1.incus",
            user="remo",
            instance_id="paul",
            access_mode="direct",
        ),
        KnownHost(
            type="proxmox",
            name="pve1/dev2",
            host="10.0.0.42",
            user="remo",
            instance_id="104",
            access_mode="direct",
            region="root",
        ),
        KnownHost(
            type="aws",
            name="buildbox",
            host="203.0.113.7",
            user="remo",
            instance_id="i-0abc123def456",
            access_mode="ssm",
            region="us-east-1",
        ),
        KnownHost(
            type="hetzner",
            name="dev1",
            host="198.51.100.9",
            user="remo",
            access_mode="direct",
        ),
        KnownHost(
            type="ssh",
            name="nas",
            host="nas.lan",
            user="admin",
            instance_id="2222",
            access_mode="direct",
            region="/home/paul/.ssh/id_nas",
        ),
    ]


class TestV2RoundTripFidelity:
    """Every KnownHost field survives a replace_registry -> read_registry cycle."""

    def test_all_types_round_trip_exactly(self, tmp_config_dir):
        hosts = _all_type_hosts()
        replace_registry(hosts, allow_empty=True)
        view = read_registry()
        assert view.source_format == "v2"
        assert len(view.hosts) == len(hosts)
        by_key = {(h.type, h.name): h for h in view.hosts}
        for original in hosts:
            assert by_key[(original.type, original.name)] == original


class TestDeterministicSerialization:
    """Re-serializing the same host list twice must produce byte-identical output."""

    def test_reserialize_produces_identical_bytes(self, tmp_config_dir):
        hosts = _all_type_hosts()
        replace_registry(hosts, allow_empty=True)
        text1 = get_registry_path().read_text()
        replace_registry(hosts, allow_empty=True)
        text2 = get_registry_path().read_text()
        assert text1 == text2

    def test_output_is_sorted_indented_with_trailing_newline(self, tmp_config_dir):
        hosts = _all_type_hosts()
        replace_registry(hosts, allow_empty=True)
        text = get_registry_path().read_text()
        assert text.endswith("\n")
        doc = json.loads(text)
        pairs = [(e["type"], e["name"]) for e in doc["hosts"]]
        assert pairs == sorted(pairs)


class TestUnknownTypePreservation:
    """Entries whose type is unrecognized round-trip verbatim (FR-014)."""

    def test_unknown_entry_counted_and_known_entry_still_parses(self, tmp_config_dir):
        entries = [
            build_v2_host_entry("docker", "mybox", "1.2.3.4", "remo"),
            build_v2_host_entry("incus", "nuc/dev1", "dev1.incus", "remo", host_user="paul"),
        ]
        write_v2_registry(tmp_config_dir, entries)

        view = read_registry()

        assert view.unknown_entries == 1
        assert len(view.hosts) == 1
        assert view.hosts[0].type == "incus"
        assert view.hosts[0].name == "nuc/dev1"

    def test_unknown_entry_survives_a_write_it_never_saw(self, tmp_config_dir):
        entries = [
            build_v2_host_entry("docker", "mybox", "1.2.3.4", "remo"),
            build_v2_host_entry("incus", "nuc/dev1", "dev1.incus", "remo", host_user="paul"),
        ]
        write_v2_registry(tmp_config_dir, entries)

        mutate_registry(lambda hosts: hosts)  # identity mutator

        raw = get_registry_path().read_text()
        doc = json.loads(raw)
        docker_entries = [e for e in doc["hosts"] if e["type"] == "docker"]
        assert len(docker_entries) == 1
        assert docker_entries[0]["name"] == "mybox"
        assert docker_entries[0]["host"] == "1.2.3.4"
        assert docker_entries[0]["user"] == "remo"


class TestNewerVersionRejection:
    """A registry.json written by a newer format version is rejected untouched."""

    def test_rejects_and_leaves_file_untouched(self, tmp_config_dir):
        doc = {"version": 3, "hosts": []}
        registry_file = tmp_config_dir / "registry.json"
        registry_file.write_text(json.dumps(doc, indent=2) + "\n")
        before = registry_file.read_text()
        before_mtime = registry_file.stat().st_mtime

        with pytest.raises(RegistryNewerVersionError):
            read_registry()

        after = registry_file.read_text()
        assert before == after
        assert registry_file.stat().st_mtime == before_mtime


class TestTolerantReadWarnings:
    """Per-entry problems on read are warnings, never exceptions (FR-014)."""

    def test_missing_required_field_warns_but_valid_entry_survives(self, tmp_config_dir):
        entries = [
            {
                "type": "incus",
                "name": "bad/entry",
                "host": "1.2.3.4",
                # "user" missing
                "access": "direct",
            },
            build_v2_host_entry("hetzner", "web1", "5.6.7.8", "remo"),
        ]
        write_v2_registry(tmp_config_dir, entries)

        view = read_registry()

        assert view.warnings
        names = {h.name for h in view.hosts}
        assert "web1" in names
        assert "bad/entry" not in names


class TestValidationRuleRejections:
    """Validation rules V2-V6 (data-model.md §5)."""

    def test_v2_empty_required_field_rejected(self):
        with pytest.raises(RegistryValidationError):
            validate_hosts(
                [KnownHost(type="incus", name="", host="1.2.3.4", user="remo")]
            )

    def test_v2_control_character_rejected(self):
        with pytest.raises(RegistryValidationError):
            validate_hosts(
                [KnownHost(type="incus", name="bad\nname", host="1.2.3.4", user="remo")]
            )

    def test_v2_newline_in_host_rejected(self):
        with pytest.raises(RegistryValidationError):
            validate_hosts(
                [KnownHost(type="incus", name="ok", host="1.2.3.4\n", user="remo")]
            )

    def test_v3_duplicate_type_and_name_rejected(self):
        hosts = [
            KnownHost(type="incus", name="dup", host="1.1.1.1", user="remo"),
            KnownHost(type="incus", name="dup", host="2.2.2.2", user="remo"),
        ]
        with pytest.raises(RegistryValidationError):
            validate_hosts(hosts)

    def test_v3_same_name_different_type_is_allowed(self):
        hosts = [
            KnownHost(
                type="incus", name="dup", host="1.1.1.1", user="remo", access_mode="direct"
            ),
            KnownHost(
                type="hetzner", name="dup", host="2.2.2.2", user="remo", access_mode="direct"
            ),
        ]
        validate_hosts(hosts)  # must not raise

    def test_v6_ssm_only_valid_for_aws(self):
        with pytest.raises(RegistryValidationError):
            validate_hosts(
                [
                    KnownHost(
                        type="incus",
                        name="x",
                        host="1.1.1.1",
                        user="remo",
                        access_mode="ssm",
                    )
                ]
            )

    def test_v6_ssm_valid_for_aws(self):
        validate_hosts(
            [
                KnownHost(
                    type="aws",
                    name="x",
                    host="1.1.1.1",
                    user="remo",
                    instance_id="i-abc",
                    access_mode="ssm",
                )
            ]
        )  # must not raise

    @pytest.mark.parametrize("bad_port", ["0", "65536", "not-a-number"])
    def test_v5_ssh_port_out_of_range_or_non_numeric_rejected(self, bad_port):
        with pytest.raises(RegistryValidationError):
            validate_hosts(
                [
                    KnownHost(
                        type="ssh",
                        name="x",
                        host="1.1.1.1",
                        user="remo",
                        instance_id=bad_port,
                        access_mode="direct",
                    )
                ]
            )

    @pytest.mark.parametrize("good_port", ["1", "65535", "22"])
    def test_v5_ssh_port_in_range_accepted(self, good_port):
        validate_hosts(
            [
                KnownHost(
                    type="ssh",
                    name="x",
                    host="1.1.1.1",
                    user="remo",
                    instance_id=good_port,
                    access_mode="direct",
                )
            ]
        )  # must not raise

    def test_replace_registry_rejects_before_write_disk_untouched(self, tmp_config_dir):
        registry_file = tmp_config_dir / "registry.json"
        assert not registry_file.exists()

        with pytest.raises(RegistryValidationError):
            replace_registry(
                [KnownHost(type="incus", name="", host="1.1.1.1", user="remo")],
                allow_empty=True,
            )

        assert not registry_file.exists()

    def test_mutate_registry_rejects_before_write_disk_unchanged(self, tmp_config_dir):
        good = [
            KnownHost(
                type="hetzner", name="web1", host="5.5.5.5", user="remo", access_mode="direct"
            )
        ]
        replace_registry(good, allow_empty=True)
        before = get_registry_path().read_text()

        def bad_mutator(hosts: list[KnownHost]) -> list[KnownHost]:
            return [*hosts, KnownHost(type="incus", name="", host="1.1.1.1", user="remo")]

        with pytest.raises(RegistryValidationError):
            mutate_registry(bad_mutator)

        after = get_registry_path().read_text()
        assert before == after


class TestIPv6AndColonValueFidelity:
    """T019 (US2): the whole point of the new format — legitimate values that
    corrupted the legacy colon-delimited format round-trip byte-identically."""

    def test_ipv6_host_round_trips(self, tmp_config_dir):
        host = KnownHost(
            type="hetzner", name="v6box", host="2001:db8::7", user="remo", access_mode="direct"
        )
        replace_registry([host], allow_empty=True)

        view = read_registry()
        assert view.hosts[0].host == "2001:db8::7"

        raw1 = get_registry_path().read_text()
        replace_registry([host], allow_empty=True)
        raw2 = get_registry_path().read_text()
        assert raw1 == raw2

    def test_colon_containing_identity_file_round_trips(self, tmp_config_dir):
        host = KnownHost(
            type="ssh",
            name="nas",
            host="nas.lan",
            user="admin",
            instance_id="2222",
            access_mode="direct",
            region="/home/paul/some:weird/path",
        )
        replace_registry([host], allow_empty=True)

        got = read_registry().hosts[0]
        assert got.region == "/home/paul/some:weird/path"

        doc = json.loads(get_registry_path().read_text())
        entry = doc["hosts"][0]
        assert entry["ssh"]["identity_file"] == "/home/paul/some:weird/path"

    def test_colon_containing_aws_region_round_trips(self, tmp_config_dir):
        host = KnownHost(
            type="aws",
            name="buildbox",
            host="1.2.3.4",
            user="remo",
            instance_id="i-abc",
            access_mode="ssm",
            region="us-east-1:special",
        )
        replace_registry([host], allow_empty=True)

        got = read_registry().hosts[0]
        assert got.region == "us-east-1:special"

    def test_spaces_and_special_characters_round_trip(self, tmp_config_dir):
        host = KnownHost(
            type="ssh",
            name="odd",
            host="my host (lab)",
            user="admin user",
            instance_id="2222",
            access_mode="direct",
            region="/path with spaces/id_rsa",
        )
        replace_registry([host], allow_empty=True)

        got = read_registry().hosts[0]
        assert got.host == "my host (lab)"
        assert got.user == "admin user"
        assert got.region == "/path with spaces/id_rsa"

    def test_boundary_length_name_round_trips(self, tmp_config_dir):
        name = "a" * 63
        host = KnownHost(
            type="hetzner", name=name, host="1.2.3.4", user="remo", access_mode="direct"
        )
        replace_registry([host], allow_empty=True)

        got = read_registry().hosts[0]
        assert got.name == name
        assert len(got.name) == 63


class TestEmptyAccessModeAccepted:
    """Several providers (hetzner.py's create()/sync()) construct a fresh
    KnownHost with access_mode left at its dataclass default (""), then pass it
    straight to save_known_host() -> mutate_registry() -> validate_hosts().
    An empty access_mode has always meant "direct" (matches
    known_host_to_entry()'s own `host.access_mode or "direct"` normalization),
    so validate_hosts() must accept it rather than requiring every call site to
    set access_mode explicitly."""

    def test_replace_registry_accepts_the_real_hetzner_write_shape(self, tmp_config_dir):
        host = KnownHost(type="hetzner", name="dev1", host="198.51.100.9", user="remo")
        assert host.access_mode == ""  # the exact shape providers/hetzner.py constructs

        replace_registry([host], allow_empty=True)  # must not raise
        reloaded = read_registry().hosts
        assert len(reloaded) == 1
        assert reloaded[0].access_mode == "direct"
