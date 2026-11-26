"""The ``local`` pseudo-type in registry v2 (spec 029, contracts/registry-local-entry.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remo_cli.core import registry as registry_module
from remo_cli.core.provider_registry import is_provider_type
from remo_cli.core.registry import (
    RegistryValidationError,
    canonicalize_entry,
    entry_to_known_host,
    is_known_type,
    known_host_to_entry,
    read_registry,
    replace_registry,
    validate_hosts,
)
from remo_cli.models.host import KnownHost


def _local(projects_dir: str = "") -> KnownHost:
    return KnownHost(
        type="local", name="local", host="localhost", user="paul",
        access_mode="direct", region=projects_dir,
    )


class TestTypeGate:
    def test_local_is_known_but_not_a_provider(self) -> None:
        assert is_known_type("local") is True
        assert is_provider_type("local") is False

    def test_canonicalize_leaves_it_untouched(self) -> None:
        entry = known_host_to_entry(_local("/Users/p/projects"))
        assert canonicalize_entry(entry) is entry


class TestRoundTrip:
    def test_without_projects_dir_has_no_nested_object(self) -> None:
        entry = known_host_to_entry(_local())
        assert entry == {
            "type": "local", "name": "local", "host": "localhost",
            "user": "paul", "access": "direct",
        }
        assert entry_to_known_host(entry) == _local()

    def test_with_projects_dir(self) -> None:
        host = _local("/Users/paul/projects")
        entry = known_host_to_entry(host)
        assert entry["local"] == {"projects_dir": "/Users/paul/projects"}
        parsed = entry_to_known_host(entry)
        assert parsed == host
        assert parsed.projects_dir == "/Users/paul/projects"

    @pytest.mark.parametrize("projects_dir", ["", "/Users/paul/projects"])
    def test_file_round_trip_is_byte_stable(self, tmp_config_dir: Path, projects_dir: str) -> None:
        replace_registry([_local(projects_dir)])
        first = (tmp_config_dir / "registry.json").read_bytes()
        view = read_registry()
        replace_registry(view.hosts)
        assert view.hosts == [_local(projects_dir)]
        assert (tmp_config_dir / "registry.json").read_bytes() == first

    def test_projects_dir_is_blank_for_other_types_and_display_region(self) -> None:
        assert KnownHost("incus", "a/b", "h", "u", region="/x").projects_dir == ""
        assert _local("/Users/p").display_region == ""


class TestRejections:
    def test_ssm_access_is_rejected_on_parse(self) -> None:
        entry = known_host_to_entry(_local())
        entry["access"] = "ssm"
        assert entry_to_known_host(entry) is None

    def test_relative_projects_dir_is_rejected_on_parse(self) -> None:
        entry = known_host_to_entry(_local())
        entry["local"] = {"projects_dir": "projects"}
        assert entry_to_known_host(entry) is None

    def test_bad_entries_are_skipped_with_a_warning(self, tmp_config_dir: Path) -> None:
        good = known_host_to_entry(_local())
        bad = {**good, "name": "other", "local": {"projects_dir": "relative"}}
        (tmp_config_dir / "registry.json").write_text(
            json.dumps({"version": 2, "hosts": [good, bad]})
        )
        view = read_registry()
        assert [h.name for h in view.hosts] == ["local"]
        assert any("malformed local entry 'other'" in w for w in view.warnings)

    def test_validation_rejects_ssm_and_relative_dir(self) -> None:
        with pytest.raises(RegistryValidationError, match="ssm"):
            validate_hosts([KnownHost("local", "l", "localhost", "u", access_mode="ssm")])
        with pytest.raises(RegistryValidationError, match="absolute"):
            validate_hosts([_local("rel/dir")])
        validate_hosts([_local("/abs")])


class TestOlderReader:
    def test_unknown_type_is_preserved_verbatim(
        self, tmp_config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pre-029 remo sees ``local`` as an unknown type and must not drop it."""
        entry = known_host_to_entry(_local("/Users/p/projects"))
        (tmp_config_dir / "registry.json").write_text(
            json.dumps({"version": 2, "hosts": [entry]})
        )
        monkeypatch.setattr(registry_module, "LOCAL_PSEUDO_TYPE", "not-local")
        view = read_registry()
        assert view.hosts == [] and view.unknown_entries == 1
        replace_registry(
            [KnownHost("incus", "a/b", "h", "u")], allow_empty=True
        )
        written = json.loads((tmp_config_dir / "registry.json").read_text())
        assert entry in written["hosts"]
