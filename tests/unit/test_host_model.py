"""Tests for remo.models.host.KnownHost dataclass."""

import pytest

from remo_cli.models.host import KnownHost


# -----------------------------------------------------------------------
# to_line() serialization
# -----------------------------------------------------------------------


class TestToLine:
    """Serialization of KnownHost to colon-delimited registry lines."""

    def test_four_field_basic_incus(self):
        """Basic 4-field line for an incus host (no optional fields)."""
        host = KnownHost(type="incus", name="myhost/dev", host="192.168.1.50", user="remo")
        assert host.to_line() == "incus:myhost/dev:192.168.1.50:remo"

    def test_four_field_basic_hetzner(self):
        """Basic 4-field line for a hetzner host."""
        host = KnownHost(type="hetzner", name="webserver", host="5.6.7.8", user="root")
        assert host.to_line() == "hetzner:webserver:5.6.7.8:root"

    def test_six_field_with_instance_id_and_access_mode(self):
        """6-field line for an AWS host with instance_id and access_mode."""
        host = KnownHost(
            type="aws",
            name="devbox",
            host="3.14.15.92",
            user="remo",
            instance_id="i-0abc123def",
            access_mode="ssm",
        )
        assert host.to_line() == "aws:devbox:3.14.15.92:remo:i-0abc123def:ssm"

    def test_seven_field_with_region(self):
        """7-field line for an AWS host with region."""
        host = KnownHost(
            type="aws",
            name="devbox",
            host="3.14.15.92",
            user="remo",
            instance_id="i-0abc123def",
            access_mode="ssm",
            region="us-west-2",
        )
        assert host.to_line() == "aws:devbox:3.14.15.92:remo:i-0abc123def:ssm:us-west-2"

    def test_instance_id_set_access_mode_empty_defaults_to_ssm(self):
        """When instance_id is set but access_mode is empty, access_mode defaults to 'ssm'."""
        host = KnownHost(
            type="aws",
            name="devbox",
            host="10.0.0.1",
            user="ec2-user",
            instance_id="i-abc123",
            access_mode="",
        )
        assert host.to_line() == "aws:devbox:10.0.0.1:ec2-user:i-abc123:ssm"

    def test_region_set_but_no_instance_id_pads_empty_fields(self):
        """When region is set but instance_id/access_mode are empty, empty fields are padded."""
        host = KnownHost(
            type="aws",
            name="devbox",
            host="10.0.0.1",
            user="ec2-user",
            instance_id="",
            access_mode="",
            region="eu-central-1",
        )
        result = host.to_line()
        assert result == "aws:devbox:10.0.0.1:ec2-user:::eu-central-1"

    def test_access_mode_set_without_instance_id(self):
        """When access_mode is set but instance_id is empty, both are serialized."""
        host = KnownHost(
            type="aws",
            name="devbox",
            host="10.0.0.1",
            user="ec2-user",
            instance_id="",
            access_mode="ssh",
        )
        assert host.to_line() == "aws:devbox:10.0.0.1:ec2-user::ssh"


# -----------------------------------------------------------------------
# from_line() deserialization
# -----------------------------------------------------------------------


class TestFromLine:
    """Deserialization of colon-delimited registry lines into KnownHost."""

    def test_four_field_line(self):
        """Parse a minimal 4-field line."""
        host = KnownHost.from_line("incus:myhost/dev:192.168.1.50:remo")
        assert host.type == "incus"
        assert host.name == "myhost/dev"
        assert host.host == "192.168.1.50"
        assert host.user == "remo"
        assert host.instance_id == ""
        assert host.access_mode == ""
        assert host.region == ""

    def test_six_field_line(self):
        """Parse a 6-field line with instance_id and access_mode."""
        host = KnownHost.from_line("aws:devbox:3.14.15.92:remo:i-0abc123def:ssm")
        assert host.type == "aws"
        assert host.name == "devbox"
        assert host.host == "3.14.15.92"
        assert host.user == "remo"
        assert host.instance_id == "i-0abc123def"
        assert host.access_mode == "ssm"
        assert host.region == ""

    def test_seven_field_line(self):
        """Parse a 7-field line with region."""
        host = KnownHost.from_line("aws:devbox:3.14.15.92:remo:i-0abc123def:ssm:us-west-2")
        assert host.type == "aws"
        assert host.name == "devbox"
        assert host.instance_id == "i-0abc123def"
        assert host.access_mode == "ssm"
        assert host.region == "us-west-2"

    def test_extra_fields_silently_ignored(self):
        """Extra fields beyond the 7th are silently ignored."""
        host = KnownHost.from_line("aws:devbox:1.2.3.4:remo:i-abc:ssm:us-east-1:extra:stuff")
        assert host.type == "aws"
        assert host.name == "devbox"
        assert host.region == "us-east-1"
        # Extra fields do not raise and are not stored.

    def test_fewer_than_four_fields_raises_value_error(self):
        """Lines with fewer than 4 fields raise ValueError."""
        with pytest.raises(ValueError, match="fewer than 4 fields"):
            KnownHost.from_line("incus:myhost:192.168.1.50")

    def test_fewer_than_four_fields_two(self):
        """Two-field line raises ValueError."""
        with pytest.raises(ValueError, match="fewer than 4 fields"):
            KnownHost.from_line("incus:myhost")

    def test_single_field_raises(self):
        """Single field raises ValueError."""
        with pytest.raises(ValueError, match="fewer than 4 fields"):
            KnownHost.from_line("incus")

    def test_empty_string_raises(self):
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="fewer than 4 fields"):
            KnownHost.from_line("")

    def test_line_with_trailing_whitespace_is_stripped(self):
        """Trailing whitespace on the line is stripped before parsing."""
        host = KnownHost.from_line("incus:myhost/dev:10.0.0.1:remo  \n")
        assert host.type == "incus"
        assert host.user == "remo"


# -----------------------------------------------------------------------
# display_name property
# -----------------------------------------------------------------------


class TestDisplayName:
    """Human-friendly display_name property."""

    def test_incus_with_slash_shows_container_on_host(self):
        """Incus names with '/' display as 'container (on host)'."""
        host = KnownHost(type="incus", name="myhost/devcontainer", host="10.0.0.1", user="remo")
        assert host.display_name == "devcontainer (on myhost)"

    def test_incus_without_slash_returns_name(self):
        """Incus names without '/' return the name unchanged."""
        host = KnownHost(type="incus", name="standalone", host="10.0.0.1", user="remo")
        assert host.display_name == "standalone"

    def test_non_incus_type_returns_name_unchanged(self):
        """Non-incus types always return name unchanged, even with '/'."""
        host = KnownHost(type="aws", name="foo/bar", host="10.0.0.1", user="remo")
        assert host.display_name == "foo/bar"

    def test_hetzner_returns_name(self):
        """Hetzner type returns the name directly."""
        host = KnownHost(type="hetzner", name="webserver", host="5.6.7.8", user="root")
        assert host.display_name == "webserver"


# -----------------------------------------------------------------------
# Round-trip: from_line(host.to_line()) == host
# -----------------------------------------------------------------------


class TestRoundTrip:
    """Verify that serialization/deserialization round-trips produce equivalent objects."""

    def test_round_trip_four_field(self):
        """Round-trip a basic 4-field host."""
        original = KnownHost(type="incus", name="myhost/dev", host="192.168.1.50", user="remo")
        restored = KnownHost.from_line(original.to_line())
        assert restored == original

    def test_round_trip_six_field(self):
        """Round-trip a 6-field host with instance_id and access_mode."""
        original = KnownHost(
            type="aws",
            name="devbox",
            host="3.14.15.92",
            user="remo",
            instance_id="i-0abc123def",
            access_mode="ssm",
        )
        restored = KnownHost.from_line(original.to_line())
        assert restored == original

    def test_round_trip_seven_field(self):
        """Round-trip a 7-field host with region."""
        original = KnownHost(
            type="aws",
            name="devbox",
            host="3.14.15.92",
            user="remo",
            instance_id="i-0abc123def",
            access_mode="ssm",
            region="us-west-2",
        )
        restored = KnownHost.from_line(original.to_line())
        assert restored == original

    def test_round_trip_instance_id_without_access_mode(self):
        """Round-trip when instance_id is set but access_mode is empty.

        After round-trip, access_mode becomes 'ssm' because to_line()
        defaults empty access_mode to 'ssm' when instance_id is present.
        """
        original = KnownHost(
            type="aws",
            name="devbox",
            host="1.2.3.4",
            user="ec2-user",
            instance_id="i-abc123",
            access_mode="",
        )
        restored = KnownHost.from_line(original.to_line())
        assert restored.type == original.type
        assert restored.name == original.name
        assert restored.host == original.host
        assert restored.user == original.user
        assert restored.instance_id == original.instance_id
        # access_mode defaults to "ssm" after round-trip
        assert restored.access_mode == "ssm"

    def test_round_trip_region_without_instance_id(self):
        """Round-trip when region is set but instance_id/access_mode are empty.

        After round-trip, the empty padded fields are preserved.
        """
        original = KnownHost(
            type="aws",
            name="devbox",
            host="1.2.3.4",
            user="ec2-user",
            region="eu-central-1",
        )
        restored = KnownHost.from_line(original.to_line())
        assert restored.type == original.type
        assert restored.name == original.name
        assert restored.host == original.host
        assert restored.user == original.user
        assert restored.region == original.region
        assert restored.instance_id == ""
        assert restored.access_mode == ""

    def test_round_trip_hetzner(self):
        """Round-trip a basic hetzner host."""
        original = KnownHost(type="hetzner", name="web1", host="5.6.7.8", user="root")
        restored = KnownHost.from_line(original.to_line())
        assert restored == original
