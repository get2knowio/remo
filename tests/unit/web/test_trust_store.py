"""Unit tests for `web/trust_store.py` (023): per-instance trust-file slices."""

from __future__ import annotations

import pytest

from remo_cli.web.trust_store import (
    known_hosts_line_error,
    line_matches_lookup_key,
    remove_instance_host_keys,
    set_instance_host_keys,
)

_KEY_A = "10.0.0.5 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFixtureKeyMaterial0000"
_KEY_B = "10.0.0.6 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeFixtureKeyMaterial1111"
_KEY_A_PORT = "[10.0.0.5]:2222 ssh-rsa AAAAB3NzaC1yc2EFakeFixtureKeyMaterial2222"


@pytest.fixture
def trust_file(tmp_path):
    return tmp_path / "known_hosts"


class TestSetInstanceHostKeys:
    def test_creates_the_file_when_absent(self, trust_file):
        set_instance_host_keys(trust_file, "10.0.0.5", [_KEY_A])
        assert trust_file.read_text() == _KEY_A + "\n"

    def test_replaces_only_the_matching_hosts_lines(self, trust_file):
        trust_file.write_text(f"{_KEY_A}\n{_KEY_B}\n")
        replacement = _KEY_A.replace("0000", "9999")
        set_instance_host_keys(trust_file, "10.0.0.5", [replacement])
        assert trust_file.read_text() == f"{_KEY_B}\n{replacement}\n"

    def test_bracketed_port_form_does_not_match_the_bare_host(self, trust_file):
        trust_file.write_text(f"{_KEY_A}\n{_KEY_A_PORT}\n")
        set_instance_host_keys(trust_file, "[10.0.0.5]:2222", [_KEY_A_PORT])
        # The bare-host line for port 22 survives a port-2222 replacement.
        assert _KEY_A in trust_file.read_text()

    def test_empty_lines_are_dropped_from_the_replacement(self, trust_file):
        set_instance_host_keys(trust_file, "10.0.0.5", [_KEY_A, "", "  "])
        assert trust_file.read_text() == _KEY_A + "\n"


class TestRemoveInstanceHostKeys:
    def test_removes_matching_lines_and_backup(self, trust_file):
        trust_file.write_text(f"{_KEY_A}\n{_KEY_B}\n")
        remove_instance_host_keys(trust_file, "10.0.0.5")
        content = trust_file.read_text()
        assert "10.0.0.5 " not in content
        assert _KEY_B in content
        assert not (trust_file.parent / "known_hosts.old").exists()

    def test_missing_file_is_a_noop(self, trust_file):
        remove_instance_host_keys(trust_file, "10.0.0.5")
        assert not trust_file.exists()


class TestLineMatching:
    def test_comment_and_blank_lines_never_match(self):
        assert not line_matches_lookup_key("# comment", "comment")
        assert not line_matches_lookup_key("", "x")

    def test_comma_separated_hosts_field_matches_each_host(self):
        line = "a.example,10.0.0.5 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey0"
        assert line_matches_lookup_key(line, "10.0.0.5")
        assert line_matches_lookup_key(line, "a.example")
        assert not line_matches_lookup_key(line, "b.example")


class TestLineValidator:
    """known_hosts_line_error moved here verbatim; a smoke row per branch."""

    @pytest.mark.parametrize(
        ("line", "fragment"),
        [
            ("", "empty"),
            ("# c", "comment"),
            ("@weird h ssh-ed25519 AAAA", "unknown marker"),
            ("h ssh-ed25519", "fewer than 3"),
            ("h nonsense AAAABBBBCCCCDDDD", "implausible key type"),
            ("h ssh-ed25519 short", "not plausible base64"),
        ],
    )
    def test_rejections(self, line, fragment):
        assert fragment in known_hosts_line_error(line)

    def test_valid_line_passes(self):
        assert known_hosts_line_error(_KEY_A) is None
