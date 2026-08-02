"""Provider save-path fixture tests (T016).

Pins the EXACT KnownHost field usage of each provider's save call site
(providers/incus.py, proxmox.py, aws.py, hetzner.py, added.py) and proves
the legacy<->v2 mapping round-trips it losslessly two ways:

1. Straight to v2 (known_host_to_entry -> entry_to_known_host) — what
   happens once these call sites are routed through the accessor.
2. Through today's legacy codec (to_line -> from_line -> legacy_fields_to_entry
   -> entry_to_known_host) — simulating "a legacy file written by today's
   code, then migrated". This is the research R5 risk pin: prove today's
   actual writer output survives migration, including the aws to_line()
   implicit-SSM back-fill quirk.
"""

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


class TestIncusSaveShape:
    """providers/incus.py create()/sync(): instance_id holds the Incus HOST's
    SSH user (-> incus.host_user); region is never set."""

    def _make(self) -> KnownHost:
        return KnownHost(
            type="incus",
            name="nuc/dev1",
            host="dev1.incus",
            user="remo",
            instance_id="paul",
            access_mode="direct",
        )

    def test_v2_round_trip(self):
        host = self._make()
        assert _v2_round_trip(host) == host

    def test_legacy_round_trip(self):
        host = self._make()
        assert _legacy_round_trip(host) == host


class TestProxmoxSaveShape:
    """providers/proxmox.py create()/sync(): instance_id holds the numeric
    VMID (-> proxmox.vmid); region holds the Proxmox NODE's SSH user
    (-> proxmox.host_user, confusingly named "region" in the old schema)."""

    def _make(self) -> KnownHost:
        return KnownHost(
            type="proxmox",
            name="pve1/dev2",
            host="10.0.0.42",
            user="remo",
            instance_id="104",
            access_mode="direct",
            region="root",
        )

    def test_v2_round_trip(self):
        host = self._make()
        assert _v2_round_trip(host) == host

    def test_legacy_round_trip(self):
        host = self._make()
        assert _legacy_round_trip(host) == host


class TestAwsSaveShape:
    """providers/aws.py create(): access_mode is always literally "ssm" on
    create (sync/other paths can set it from an instance tag, but it is
    always non-empty)."""

    def _make(self) -> KnownHost:
        return KnownHost(
            type="aws",
            name="buildbox",
            host="203.0.113.7",
            user="remo",
            instance_id="i-0abc123def456",
            access_mode="ssm",
            region="us-east-1",
        )

    def test_v2_round_trip(self):
        host = self._make()
        assert _v2_round_trip(host) == host

    def test_legacy_round_trip(self):
        host = self._make()
        assert _legacy_round_trip(host) == host


class TestHetznerSaveShape:
    """providers/hetzner.py create()/sync(): instance_id, access_mode, and
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


class TestSshAddedSaveShape:
    """providers/added.py add(): instance_id is the port AS A STRING
    (-> ssh.port as an int in v2); region is the optional identity file
    path (-> ssh.identity_file)."""

    def _make(self) -> KnownHost:
        return KnownHost(
            type="ssh",
            name="nas",
            host="nas.lan",
            user="admin",
            instance_id="2222",
            access_mode="direct",
            region="/home/paul/.ssh/id_nas",
        )

    def test_v2_round_trip(self):
        host = self._make()
        assert _v2_round_trip(host) == host

    def test_legacy_round_trip(self):
        host = self._make()
        assert _legacy_round_trip(host) == host

    def test_port_str_converts_to_v2_int(self):
        entry = known_host_to_entry(self._make())
        assert entry["ssh"]["port"] == 2222
        assert isinstance(entry["ssh"]["port"], int)


class TestAwsImplicitSsmBackfillQuirk:
    """research.md R5: KnownHost.to_line() back-fills access_mode="ssm" when
    instance_id is set and access_mode is empty. For type=aws this quirk is
    semantically CORRECT (an aws entry carrying an instance id but no
    explicit access mode should still be treated as ssm) — contrast with
    the non-aws "incus_implicit_ssm" fixture in test_registry_migration.py,
    which carries superficially similar legacy bytes but must map to
    "direct" (only aws may ever be "ssm")."""

    def _make(self) -> KnownHost:
        return KnownHost(
            type="aws",
            name="buildbox",
            host="203.0.113.7",
            user="remo",
            instance_id="i-abc",
            access_mode="",
        )

    def test_to_line_backfills_ssm(self):
        line = self._make().to_line()
        assert line == "aws:buildbox:203.0.113.7:remo:i-abc:ssm"

    def test_migrated_line_classifies_as_ssm(self):
        line = self._make().to_line()
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
        assert entry["access"] == "ssm"
        reconstructed = entry_to_known_host(entry)
        assert reconstructed is not None
        assert reconstructed.access_mode == "ssm"
