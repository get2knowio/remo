"""Tests for the adoption payload builder in remo_cli.core.web_adopt (011-web-adopt, T022).

Covers build_adoption_payload full-mirror semantics (FR-008), SSM host-key
exclusion (FR-012), the empty-registry guard (FR-016), the no-private-key
guarantee (FR-007), and the is_direct_access classification.
"""

import json

import pytest

from remo_cli.core.web_adopt import (
    PAYLOAD_VERSION,
    EmptyRegistryError,
    build_adoption_payload,
    is_direct_access,
)
from remo_cli.models.host import KnownHost

# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

ALL_FIELDS = ("type", "name", "host", "user", "instance_id", "access_mode", "region")


def _make_host(type_="incus", name="myhost/dev", host="10.0.0.1", user="remo", **kwargs):
    """Convenience factory for KnownHost instances."""
    return KnownHost(type=type_, name=name, host=host, user=user, **kwargs)


def _sample_hosts() -> list[KnownHost]:
    """One host per access flavor, exercising every KnownHost field."""
    return [
        _make_host(type_="incus", name="node1/dev", host="10.0.0.1", user="remo"),
        _make_host(
            type_="aws",
            name="devbox-ssm",
            host="3.14.15.92",
            user="remo",
            instance_id="i-0abc123def",
            access_mode="ssm",
            region="us-west-2",
        ),
        _make_host(
            type_="aws",
            name="devbox-direct",
            host="3.14.15.93",
            user="remo",
            instance_id="i-0fed321cba",
            access_mode="direct",
            region="eu-central-1",
        ),
        _make_host(type_="hetzner", name="web1", host="5.6.7.8", user="remo"),
    ]


HOST_KEY_LINE = "10.0.0.1 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyMaterialForTests"


# -----------------------------------------------------------------------
# Full-mirror semantics (FR-008)
# -----------------------------------------------------------------------


class TestFullMirror:
    """The payload mirrors the complete registry, every field, version 1."""

    def test_version_is_one(self):
        payload = build_adoption_payload(_sample_hosts())
        assert payload["version"] == 1
        assert payload["version"] == PAYLOAD_VERSION

    def test_every_registry_entry_present(self):
        hosts = _sample_hosts()
        payload = build_adoption_payload(hosts)
        assert len(payload["registry"]) == len(hosts)
        assert [e["name"] for e in payload["registry"]] == [h.name for h in hosts]

    def test_all_seven_knownhost_fields_mirrored(self):
        hosts = _sample_hosts()
        payload = build_adoption_payload(hosts)
        for host, entry in zip(hosts, payload["registry"]):
            assert set(entry.keys()) == set(ALL_FIELDS)
            for field_name in ALL_FIELDS:
                assert entry[field_name] == getattr(host, field_name)

    def test_ssm_entry_fields_mirrored_verbatim(self):
        """SSM entries are excluded from host_keys but fully present in registry."""
        payload = build_adoption_payload(_sample_hosts())
        ssm_entry = next(e for e in payload["registry"] if e["name"] == "devbox-ssm")
        assert ssm_entry == {
            "type": "aws",
            "name": "devbox-ssm",
            "host": "3.14.15.92",
            "user": "remo",
            "instance_id": "i-0abc123def",
            "access_mode": "ssm",
            "region": "us-west-2",
        }

    def test_host_keys_default_to_empty_dict(self):
        payload = build_adoption_payload(_sample_hosts())
        assert payload["host_keys"] == {}

    def test_payload_has_exactly_the_contract_keys(self):
        payload = build_adoption_payload(_sample_hosts())
        assert set(payload.keys()) == {"version", "registry", "host_keys"}


# -----------------------------------------------------------------------
# host_keys filtering (FR-012 + name scoping)
# -----------------------------------------------------------------------


class TestHostKeysFiltering:
    """host_keys is scoped to direct-access registry names only."""

    def test_direct_access_keys_pass_through(self):
        hosts = _sample_hosts()
        host_keys = {"node1/dev": [HOST_KEY_LINE], "web1": [HOST_KEY_LINE]}
        payload = build_adoption_payload(hosts, host_keys)
        assert payload["host_keys"] == host_keys

    def test_ssm_entry_never_carries_host_keys_even_if_passed(self):
        """Defensive filter: an SSM mapping in the input is silently dropped."""
        hosts = _sample_hosts()
        host_keys = {
            "node1/dev": [HOST_KEY_LINE],
            "devbox-ssm": [HOST_KEY_LINE],  # must be filtered out (FR-012)
        }
        payload = build_adoption_payload(hosts, host_keys)
        assert "devbox-ssm" not in payload["host_keys"]
        assert payload["host_keys"] == {"node1/dev": [HOST_KEY_LINE]}
        # ...but the SSM entry itself is still mirrored in the registry.
        assert any(e["name"] == "devbox-ssm" for e in payload["registry"])

    def test_keys_for_names_absent_from_registry_are_dropped(self):
        hosts = _sample_hosts()
        host_keys = {"ghost-host": [HOST_KEY_LINE], "web1": [HOST_KEY_LINE]}
        payload = build_adoption_payload(hosts, host_keys)
        assert "ghost-host" not in payload["host_keys"]
        assert payload["host_keys"] == {"web1": [HOST_KEY_LINE]}

    def test_empty_key_lists_are_dropped(self):
        hosts = _sample_hosts()
        payload = build_adoption_payload(hosts, {"web1": []})
        assert payload["host_keys"] == {}

    def test_aws_direct_access_entry_may_carry_host_keys(self):
        hosts = _sample_hosts()
        payload = build_adoption_payload(hosts, {"devbox-direct": [HOST_KEY_LINE]})
        assert payload["host_keys"] == {"devbox-direct": [HOST_KEY_LINE]}


# -----------------------------------------------------------------------
# Empty-registry guard (FR-016)
# -----------------------------------------------------------------------


class TestEmptyRegistryGuard:
    def test_empty_registry_raises(self, tmp_config_dir):
        with pytest.raises(EmptyRegistryError):
            build_adoption_payload([])

    def test_empty_registry_error_mentions_allow_empty(self, tmp_config_dir):
        with pytest.raises(EmptyRegistryError, match="--allow-empty"):
            build_adoption_payload([])

    def test_allow_empty_permits_empty_mirror(self, tmp_config_dir):
        payload = build_adoption_payload([], allow_empty=True)
        assert payload == {"version": PAYLOAD_VERSION, "registry": [], "host_keys": {}}

    def test_non_empty_registry_ignores_allow_empty_flag(self):
        hosts = _sample_hosts()
        payload = build_adoption_payload(hosts, allow_empty=True)
        assert len(payload["registry"]) == len(hosts)


# -----------------------------------------------------------------------
# FR-007: no private-key material ever leaves the workstation payload
# -----------------------------------------------------------------------


class TestNoPrivateKeyMaterial:
    """The serialized payload carries only registry metadata + public host-key lines."""

    SECRET_BODY = "b3BlbnNzaFNFQ1JFVGtleWJvZHlmb3J0ZXN0c29ubHk"

    @pytest.fixture
    def identity_dir(self, tmp_config_dir):
        """Plant a fixture private key in the test REMO_HOME web-identity area."""
        identity = tmp_config_dir / "web-identity"
        identity.mkdir()
        (identity / "id_ed25519").write_text(
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            f"{self.SECRET_BODY}\n"
            "-----END OPENSSH PRIVATE KEY-----\n"
        )
        (identity / "id_ed25519.pub").write_text(
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPublicOnlyKey remo-web@deploy-1\n"
        )
        return identity

    def test_serialized_payload_contains_no_private_key(self, identity_dir):
        hosts = _sample_hosts()
        host_keys = {"node1/dev": [HOST_KEY_LINE], "web1": [HOST_KEY_LINE]}
        serialized = json.dumps(build_adoption_payload(hosts, host_keys))

        assert "PRIVATE KEY" not in serialized
        assert self.SECRET_BODY not in serialized
        private_key_text = (identity_dir / "id_ed25519").read_text()
        for line in private_key_text.splitlines():
            assert line not in serialized

    def test_payload_carries_nothing_beyond_registry_and_provided_keys(self, identity_dir):
        hosts = _sample_hosts()
        host_keys = {"web1": [HOST_KEY_LINE]}
        payload = build_adoption_payload(hosts, host_keys)

        assert set(payload.keys()) == {"version", "registry", "host_keys"}
        for entry in payload["registry"]:
            assert set(entry.keys()) == set(ALL_FIELDS)
        assert payload["host_keys"] == {"web1": [HOST_KEY_LINE]}
        # Every host-key line in the payload is one the caller provided.
        for lines in payload["host_keys"].values():
            for line in lines:
                assert line in host_keys["web1"]


# -----------------------------------------------------------------------
# is_direct_access classification
# -----------------------------------------------------------------------


class TestIsDirectAccess:
    def test_plain_host_no_optional_fields_is_direct(self):
        assert is_direct_access(_make_host()) is True

    def test_explicit_direct_mode_with_instance_id_is_direct(self):
        host = _make_host(
            type_="aws", name="devbox", instance_id="i-0abc", access_mode="direct"
        )
        assert is_direct_access(host) is True

    def test_ssm_mode_is_not_direct(self):
        host = _make_host(type_="aws", name="devbox", instance_id="i-0abc", access_mode="ssm")
        assert is_direct_access(host) is False

    def test_instance_id_with_default_empty_mode_classifies_as_ssm(self):
        """A 5-field legacy entry (instance_id, no explicit mode) defaults to SSM."""
        host = _make_host(type_="aws", name="devbox", instance_id="i-0abc", access_mode="")
        assert is_direct_access(host) is False
