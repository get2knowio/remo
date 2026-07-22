"""Tests for providers/added.py add() + target parsing (feature 014, US1).

Covers target parsing (default user, overrides, IPv6/bracketed rejection),
identity-colon rejection, create-writes-one-entry, provider-name collision
refusal (FR-010), and FR-006 resolvability/picker inclusion of an added host.
SSH/registry are mocked except the FR-006 test, which uses a real temp registry.
"""

from __future__ import annotations

import pytest

from remo_cli.models.host import KnownHost
from remo_cli.providers import added


# ---------------------------------------------------------------------------
# parse_ssh_target
# ---------------------------------------------------------------------------


class TestParseTarget:
    def test_host_only_defaults(self) -> None:
        assert added.parse_ssh_target("1.2.3.4") == ("remo", "1.2.3.4", 22)

    def test_user_and_host(self) -> None:
        assert added.parse_ssh_target("dev@host") == ("dev", "host", 22)

    def test_user_host_port(self) -> None:
        assert added.parse_ssh_target("dev@host:2222") == ("dev", "host", 2222)

    def test_host_port_default_user(self) -> None:
        assert added.parse_ssh_target("host:2222") == ("remo", "host", 2222)

    def test_user_override_wins(self) -> None:
        assert added.parse_ssh_target("dev@host", user_override="admin")[0] == "admin"

    def test_port_override_wins(self) -> None:
        assert added.parse_ssh_target("host:22", port_override=2022)[2] == 2022

    @pytest.mark.parametrize("bad", ["::1", "fe80::1", "user@2001:db8::1"])
    def test_ipv6_literal_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="IPv6"):
            added.parse_ssh_target(bad)

    def test_bracketed_ipv6_rejected(self) -> None:
        with pytest.raises(ValueError, match="IPv6"):
            added.parse_ssh_target("user@[2001:db8::1]:22")

    def test_non_numeric_port_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a number"):
            added.parse_ssh_target("host:nope")

    def test_port_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="range"):
            added.parse_ssh_target("host:70000")

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError):
            added.parse_ssh_target("   ")

    def test_empty_user_before_at_rejected(self) -> None:
        with pytest.raises(ValueError):
            added.parse_ssh_target("@host")

    def test_user_with_colon_rejected(self) -> None:
        # A ':' in the user would shift every later registry field on reload.
        with pytest.raises(ValueError, match="user must not contain"):
            added.parse_ssh_target("host", user_override="ev:il")

    def test_user_with_newline_rejected(self) -> None:
        # A newline would inject a second registry line.
        with pytest.raises(ValueError, match="control"):
            added.parse_ssh_target("host", user_override="ev\nil")

    def test_host_with_control_char_rejected(self) -> None:
        with pytest.raises(ValueError, match="control"):
            added.parse_ssh_target("ho\tst")


# ---------------------------------------------------------------------------
# add() — create
# ---------------------------------------------------------------------------


class TestAddCreate:
    def test_create_writes_single_ssh_entry(self, mocker) -> None:
        mocker.patch("remo_cli.providers.added.get_known_hosts", return_value=[])
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        success = mocker.patch("remo_cli.providers.added.print_success")
        mocker.patch("remo_cli.providers.added.print_info")

        rc = added.add(name="box", target="dev@1.2.3.4:2222")

        assert rc == 0
        save.assert_called_once()
        entry = save.call_args.args[0]
        assert isinstance(entry, KnownHost)
        assert entry.type == "ssh"
        assert entry.name == "box"
        assert entry.host == "1.2.3.4"
        assert entry.user == "dev"
        assert entry.instance_id == "2222"
        assert entry.access_mode == "direct"
        assert entry.region == ""
        # Effective user reported back (FR-003)
        assert "dev" in " ".join(str(c.args[0]) for c in success.call_args_list)

    def test_default_user_applied_and_reported(self, mocker) -> None:
        mocker.patch("remo_cli.providers.added.get_known_hosts", return_value=[])
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        success = mocker.patch("remo_cli.providers.added.print_success")
        mocker.patch("remo_cli.providers.added.print_info")

        rc = added.add(name="box", target="1.2.3.4")

        assert rc == 0
        assert save.call_args.args[0].user == "remo"
        assert "remo" in " ".join(str(c.args[0]) for c in success.call_args_list)

    def test_identity_stored_in_region(self, mocker) -> None:
        mocker.patch("remo_cli.providers.added.get_known_hosts", return_value=[])
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        mocker.patch("remo_cli.providers.added.print_success")
        mocker.patch("remo_cli.providers.added.print_info")

        added.add(name="box", target="dev@host", identity="/home/dev/.ssh/id")

        assert save.call_args.args[0].region == "/home/dev/.ssh/id"

    def test_identity_with_colon_rejected_no_write(self, mocker) -> None:
        mocker.patch("remo_cli.providers.added.get_known_hosts", return_value=[])
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        err = mocker.patch("remo_cli.providers.added.print_error")

        rc = added.add(name="box", target="dev@host", identity="/bad:path/key")

        assert rc == 2
        save.assert_not_called()
        assert err.called

    def test_identity_with_newline_rejected_no_write(self, mocker) -> None:
        # A newline in the identity would inject a forged registry line.
        mocker.patch("remo_cli.providers.added.get_known_hosts", return_value=[])
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        mocker.patch("remo_cli.providers.added.print_error")

        rc = added.add(name="box", target="dev@host", identity="/k/id\nssh:evil:h:u")

        assert rc == 2
        save.assert_not_called()

    def test_malformed_target_rejected_no_write(self, mocker) -> None:
        mocker.patch("remo_cli.providers.added.get_known_hosts", return_value=[])
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        mocker.patch("remo_cli.providers.added.print_error")

        rc = added.add(name="box", target="::1")

        assert rc == 2
        save.assert_not_called()


# ---------------------------------------------------------------------------
# add() — collision (FR-010 / SC-005)
# ---------------------------------------------------------------------------


class TestAddCollision:
    def test_provider_name_collision_refused(self, mocker) -> None:
        existing = KnownHost(type="incus", name="box", host="10.0.0.1", user="remo")
        mocker.patch(
            "remo_cli.providers.added.get_known_hosts", return_value=[existing]
        )
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        err = mocker.patch("remo_cli.providers.added.print_error")

        rc = added.add(name="box", target="dev@1.2.3.4")

        assert rc == 1
        save.assert_not_called()
        assert "incus" in " ".join(str(c.args[0]) for c in err.call_args_list)

    def test_incus_container_shortname_collision_refused(self, mocker) -> None:
        # An incus entry "node/devbox" is resolvable as "devbox"; adding an ssh
        # host "devbox" would shadow it, so it must be refused (FR-010 shadow).
        existing = KnownHost(
            type="incus", name="node/devbox", host="10.0.0.1", user="remo"
        )
        mocker.patch(
            "remo_cli.providers.added.get_known_hosts", return_value=[existing]
        )
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        mocker.patch("remo_cli.providers.added.print_error")

        rc = added.add(name="devbox", target="user@1.2.3.4")

        assert rc == 1
        save.assert_not_called()


# ---------------------------------------------------------------------------
# FR-006 — an added host resolves by name and appears in the picker source
# (real temp registry so save/get/resolve exercise actual serialization)
# ---------------------------------------------------------------------------


class TestAddedHostResolvable:
    @pytest.fixture
    def temp_registry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REMO_HOME", str(tmp_path / "remo"))
        return tmp_path

    def test_resolves_by_name_and_in_get_known_hosts(
        self, temp_registry, monkeypatch
    ) -> None:
        from remo_cli.core.known_hosts import (
            get_known_hosts,
            resolve_remo_host_by_name,
        )

        rc = added.add(name="mybox", target="dev@10.0.0.9:2222")
        assert rc == 0

        # FR-006: selectable by name...
        resolved = resolve_remo_host_by_name("mybox")
        assert resolved.type == "ssh"
        assert resolved.host == "10.0.0.9"
        assert resolved.ssh_port == 2222
        # ...and present in the picker source (get_known_hosts, no filter).
        names = [h.name for h in get_known_hosts()]
        assert "mybox" in names
