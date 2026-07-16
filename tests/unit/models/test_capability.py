"""Tests for remo_cli.models.capability.RemoteCapability."""

import pytest

from remo_cli.models.capability import RemoteCapability


# -----------------------------------------------------------------------
# from_dict() happy path
# -----------------------------------------------------------------------


class TestFromDictHappyPath:
    """Parsing a well-formed capabilities --json payload."""

    def test_full_payload(self):
        """A complete payload parses into matching fields."""
        data = {
            "protocol_version": 1,
            "host_tools_version": "2.1.0",
            "projects_root": "/home/remo/projects",
            "operations": ["capabilities", "sessions.list", "sessions.attach"],
            "zellij": True,
            "docker": True,
        }
        cap = RemoteCapability.from_dict(data)
        assert cap.protocol_version == 1
        assert cap.host_tools_version == "2.1.0"
        assert cap.projects_root == "/home/remo/projects"
        assert cap.operations == ["capabilities", "sessions.list", "sessions.attach"]
        assert cap.zellij is True
        assert cap.docker is True

    def test_unknown_extra_keys_are_ignored(self):
        """Additive-compatible: unrecognized keys don't raise or leak in."""
        data = {
            "protocol_version": 1,
            "host_tools_version": "2.1.0",
            "projects_root": "/home/remo/projects",
            "operations": [],
            "zellij": False,
            "docker": False,
            "future_field": "some-new-thing",
        }
        cap = RemoteCapability.from_dict(data)
        assert cap.protocol_version == 1
        assert not hasattr(cap, "future_field")

    def test_missing_optional_fields_default_sensibly(self):
        """Missing non-version fields default rather than raising."""
        cap = RemoteCapability.from_dict({"protocol_version": 1})
        assert cap.host_tools_version == ""
        assert cap.projects_root == ""
        assert cap.operations == []
        assert cap.zellij is False
        assert cap.docker is False


# -----------------------------------------------------------------------
# from_dict() protocol_version validation
# -----------------------------------------------------------------------


class TestFromDictProtocolVersionValidation:
    """protocol_version must be a positive int; anything else raises."""

    def test_missing_protocol_version_raises(self):
        with pytest.raises(ValueError, match="protocol_version"):
            RemoteCapability.from_dict({"host_tools_version": "2.1.0"})

    def test_zero_protocol_version_raises(self):
        with pytest.raises(ValueError, match="protocol_version"):
            RemoteCapability.from_dict({"protocol_version": 0})

    def test_negative_protocol_version_raises(self):
        with pytest.raises(ValueError, match="protocol_version"):
            RemoteCapability.from_dict({"protocol_version": -1})

    def test_string_protocol_version_raises(self):
        with pytest.raises(ValueError, match="protocol_version"):
            RemoteCapability.from_dict({"protocol_version": "1"})

    def test_bool_protocol_version_raises(self):
        """bool is a subclass of int in Python; must still be rejected."""
        with pytest.raises(ValueError, match="protocol_version"):
            RemoteCapability.from_dict({"protocol_version": True})

    def test_none_protocol_version_raises(self):
        with pytest.raises(ValueError, match="protocol_version"):
            RemoteCapability.from_dict({"protocol_version": None})
