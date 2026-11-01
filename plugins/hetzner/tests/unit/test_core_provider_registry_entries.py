"""The registry shape Hetzner's save path writes (moved from remo's
tests/unit/providers/test_provider_registry_entries.py, T016)."""

from __future__ import annotations

from remo_cli.core.registry import (
    entry_to_known_host,
    known_host_to_entry,
    legacy_fields_to_entry,
)
from remo_cli.models.host import KnownHost


def _v2_round_trip(host: KnownHost) -> KnownHost | None:
    return entry_to_known_host(known_host_to_entry(host))


def _legacy_round_trip(host: KnownHost) -> KnownHost | None:
    """Simulate: today's writer emits a legacy line, migration parses it."""
    line = host.to_line()
    parsed = KnownHost.from_line(line)
    entry = legacy_fields_to_entry(
        parsed.type,
        parsed.name,
        parsed.host,
        parsed.user,
        parsed.instance_id,
        parsed.access_mode,
        parsed.region,
    )
    return entry_to_known_host(entry)


class TestHetznerSaveShape:
    """remo_provider_hetzner.provider create()/sync(): instance_id, access_mode, and
    region all default — access_mode is "" at construction, not "direct"
    explicitly. known_host_to_entry serializes "" as "access": "direct"
    (`host.access_mode or "direct"`), and a v2/legacy round-trip normalizes
    the in-memory value to the explicit "direct" (data-model.md §3: the
    legacy implicit-empty convention no longer leaks upward after a v2/
    accessor load) — so this is the one shape that is NOT byte-for-field
    identical post-round-trip, by design."""

    def _make(self) -> KnownHost:
        return KnownHost(
            type="hetzner",
            name="dev1",
            host="198.51.100.9",
            user="remo",
        )

    def _normalized(self) -> KnownHost:
        return KnownHost(
            type="hetzner",
            name="dev1",
            host="198.51.100.9",
            user="remo",
            instance_id="",
            access_mode="direct",
            region="",
        )

    def test_constructed_with_empty_access_mode(self):
        host = self._make()
        assert host.access_mode == ""
        assert host.instance_id == ""
        assert host.region == ""

    def test_v2_entry_serializes_empty_access_mode_as_direct(self):
        entry = known_host_to_entry(self._make())
        assert entry["access"] == "direct"
        assert "hetzner" not in entry

    def test_v2_round_trip_normalizes_access_mode_to_direct(self):
        assert _v2_round_trip(self._make()) == self._normalized()

    def test_legacy_round_trip_normalizes_access_mode_to_direct(self):
        assert _legacy_round_trip(self._make()) == self._normalized()
