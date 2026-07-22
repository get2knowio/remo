"""Tests for the manually-added SSH host registry type (feature 014).

Covers `KnownHost` serialization round-trips for the new ``ssh`` type and the
type-gated ``ssh_port`` / ``ssh_identity`` accessors — including SC-007
backward-compatible parsing of pre-existing provider lines. No I/O.
"""

from __future__ import annotations

from remo_cli.core.config import DEFAULT_SSH_PORT
from remo_cli.models.host import KnownHost


# ---------------------------------------------------------------------------
# Serialization round-trip (no format change — reuses instance_id/access_mode/region)
# ---------------------------------------------------------------------------


class TestSshSerialization:
    def test_default_port_no_identity_roundtrip(self) -> None:
        # 6-field form: ssh:name:host:user:22:direct
        h = KnownHost(
            type="ssh",
            name="box",
            host="1.2.3.4",
            user="remo",
            instance_id="22",
            access_mode="direct",
        )
        line = h.to_line()
        assert line == "ssh:box:1.2.3.4:remo:22:direct"
        back = KnownHost.from_line(line)
        assert (back.type, back.name, back.host, back.user) == (
            "ssh",
            "box",
            "1.2.3.4",
            "remo",
        )
        assert back.instance_id == "22"
        assert back.access_mode == "direct"
        assert back.region == ""

    def test_custom_port_roundtrip(self) -> None:
        h = KnownHost(
            type="ssh",
            name="api",
            host="10.0.0.9",
            user="dev",
            instance_id="2222",
            access_mode="direct",
        )
        assert h.to_line() == "ssh:api:10.0.0.9:dev:2222:direct"
        assert KnownHost.from_line(h.to_line()).ssh_port == 2222

    def test_custom_port_and_identity_roundtrip(self) -> None:
        # 7-field form: ssh:name:host:user:port:direct:identity
        h = KnownHost(
            type="ssh",
            name="api",
            host="10.0.0.9",
            user="dev",
            instance_id="2222",
            access_mode="direct",
            region="/home/dev/.ssh/box_ed25519",
        )
        line = h.to_line()
        assert line == "ssh:api:10.0.0.9:dev:2222:direct:/home/dev/.ssh/box_ed25519"
        back = KnownHost.from_line(line)
        assert back.ssh_port == 2222
        assert back.ssh_identity == "/home/dev/.ssh/box_ed25519"


# ---------------------------------------------------------------------------
# Type-gated accessors
# ---------------------------------------------------------------------------


class TestSshAccessors:
    def test_ssh_port_defaults_to_22(self) -> None:
        h = KnownHost(type="ssh", name="box", host="h", user="remo")
        assert h.ssh_port == DEFAULT_SSH_PORT == 22

    def test_ssh_port_parses_instance_id(self) -> None:
        h = KnownHost(
            type="ssh", name="box", host="h", user="remo", instance_id="2222"
        )
        assert h.ssh_port == 2222

    def test_ssh_identity_from_region(self) -> None:
        h = KnownHost(
            type="ssh", name="box", host="h", user="remo", region="/k/id_ed25519"
        )
        assert h.ssh_identity == "/k/id_ed25519"

    def test_ssh_identity_none_when_empty(self) -> None:
        h = KnownHost(type="ssh", name="box", host="h", user="remo")
        assert h.ssh_identity is None

    def test_non_ssh_types_return_neutral_values(self) -> None:
        # A proxmox host stores a numeric vmid in instance_id — it must NOT be
        # read as a port, and it has no ssh identity.
        pmx = KnownHost(
            type="proxmox",
            name="node/dev1",
            host="10.0.0.1",
            user="remo",
            instance_id="100",
            region="root",
        )
        assert pmx.ssh_port == DEFAULT_SSH_PORT  # 22, not 100
        assert pmx.ssh_identity is None

        aws = KnownHost(
            type="aws",
            name="devbox",
            host="3.4.5.6",
            user="remo",
            instance_id="i-0abc",
            access_mode="ssm",
            region="us-west-2",
        )
        assert aws.ssh_port == DEFAULT_SSH_PORT
        assert aws.ssh_identity is None


# ---------------------------------------------------------------------------
# SC-007: pre-existing provider lines still parse unchanged
# ---------------------------------------------------------------------------


class TestBackwardCompatibleParsing:
    def test_legacy_provider_lines_load(self) -> None:
        incus = KnownHost.from_line("incus:myhost/devcontainer:192.168.1.50:remo")
        assert incus.type == "incus" and incus.region == ""

        aws = KnownHost.from_line(
            "aws:devbox:3.14.15.92:remo:i-0abc123def:ssm:us-west-2"
        )
        assert aws.type == "aws"
        assert aws.instance_id == "i-0abc123def"
        assert aws.access_mode == "ssm"
        assert aws.region == "us-west-2"

        hetzner = KnownHost.from_line("hetzner:webserver:5.6.7.8:remo")
        assert hetzner.type == "hetzner" and hetzner.user == "remo"
