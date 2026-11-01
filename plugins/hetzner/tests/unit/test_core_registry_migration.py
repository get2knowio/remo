"""Hetzner rows of remo's legacy known_hosts -> registry.json v2 migration
matrix (moved from remo's tests/unit/core/test_registry_migration.py when
hetzner became a plugin).

With the plugin installed, a hetzner legacy line migrates to a typed entry:
the descriptor declares no registry fields, so no nested object is ever
emitted, and the 7-field variant's trailing fields are simply ignored.
"""

from __future__ import annotations

import json

from remo_cli.core.config import get_registry_backup_path
from remo_cli.core.registry import migrate_if_needed, read_registry
from remo_cli.models.host import KnownHost


def _write_legacy(config_dir, lines: list[str]) -> None:
    (config_dir / "known_hosts").write_text("\n".join(lines) + "\n")


def _entry(config_dir, type_: str, name: str) -> dict:
    doc = json.loads((config_dir / "registry.json").read_text())
    return next(e for e in doc["hosts"] if e["type"] == type_ and e["name"] == name)


class TestHetznerFieldVariants:
    def test_4_and_7_field_variants_have_no_nested_fields(self, tmp_config_dir):
        lines = [
            "hetzner:h1:4.4.4.1:remo",
            "hetzner:h2:4.4.4.2:remo:ignored_id:direct:ignored_region",
        ]
        _write_legacy(tmp_config_dir, lines)

        report = migrate_if_needed()

        assert report is not None
        assert report.migrated_count == len(lines)
        assert report.skipped_lines == []
        assert "hetzner" in report.migrated_types

        e = _entry(tmp_config_dir, "hetzner", "h1")
        assert e == {"type": "hetzner", "name": "h1", "host": "4.4.4.1", "user": "remo", "access": "direct"}

        e = _entry(tmp_config_dir, "hetzner", "h2")
        assert "hetzner" not in e  # extra fields silently ignored, no error
        assert "_legacy_fields" not in e
        assert e["access"] == "direct"

        assert lines[1] in get_registry_backup_path().read_text()

        view = read_registry()
        assert view.warnings == []
        assert view.unknown_entries == 0
        assert sorted(view.hosts, key=lambda h: h.name) == [
            KnownHost(type="hetzner", name="h1", host="4.4.4.1", user="remo", access_mode="direct"),
            KnownHost(type="hetzner", name="h2", host="4.4.4.2", user="remo", access_mode="direct"),
        ]
