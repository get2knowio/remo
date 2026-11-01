"""Hetzner shapes from remo's tests/unit/core/test_registry_format.py (moved
when hetzner became a plugin): the field-less round trip, and the exact
KnownHost shape this provider's create()/sync() hand to save_known_host().
"""

from __future__ import annotations

import json

from remo_cli.core.config import get_registry_path
from remo_cli.core.registry import read_registry, replace_registry
from remo_cli.models.host import KnownHost


class TestHetznerRoundTrip:
    def test_round_trips_exactly_with_no_nested_object(self, tmp_config_dir):
        host = KnownHost(type="hetzner", name="dev1", host="198.51.100.9", user="remo", access_mode="direct")
        replace_registry([host], allow_empty=True)
        first = get_registry_path().read_text()

        view = read_registry()
        assert view.hosts == [host]
        assert view.warnings == []
        assert json.loads(first)["hosts"] == [
            {"type": "hetzner", "name": "dev1", "host": "198.51.100.9", "user": "remo", "access": "direct"}
        ]

        replace_registry(view.hosts, allow_empty=True)
        assert get_registry_path().read_text() == first

    def test_ipv6_host_round_trips(self, tmp_config_dir):
        host = KnownHost(type="hetzner", name="v6box", host="2001:db8::7", user="remo", access_mode="direct")
        replace_registry([host], allow_empty=True)

        assert read_registry().hosts[0].host == "2001:db8::7"


class TestEmptyAccessModeAccepted:
    """remo_provider_hetzner.provider's create()/sync() construct a KnownHost
    with access_mode left at its dataclass default ("")."""

    def test_replace_registry_accepts_the_real_hetzner_write_shape(self, tmp_config_dir):
        host = KnownHost(type="hetzner", name="dev1", host="198.51.100.9", user="remo")
        assert host.access_mode == ""  # the exact shape the provider constructs

        replace_registry([host], allow_empty=True)  # must not raise
        reloaded = read_registry().hosts
        assert len(reloaded) == 1
        assert reloaded[0].access_mode == "direct"
