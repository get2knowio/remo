"""Tests for remo.core.known_hosts registry module."""

import os

import pytest

from remo_cli.core.known_hosts import (
    clear_known_hosts_by_prefix,
    clear_known_hosts_by_type,
    get_aws_region,
    get_known_hosts,
    remove_known_host,
    resolve_remo_host_by_name,
    save_known_host,
)
from remo_cli.models.host import KnownHost


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _read_registry(config_dir) -> str:
    """Read the raw known_hosts file content."""
    return (config_dir / "known_hosts").read_text()


def _write_registry(config_dir, content: str) -> None:
    """Write raw content to the known_hosts file."""
    (config_dir / "known_hosts").write_text(content)


def _make_host(type_="incus", name="myhost/dev", host="10.0.0.1", user="remo", **kwargs):
    """Convenience factory for KnownHost instances."""
    return KnownHost(type=type_, name=name, host=host, user=user, **kwargs)


# -----------------------------------------------------------------------
# save_known_host()
# -----------------------------------------------------------------------


class TestSaveKnownHost:
    """Adding and replacing entries in the registry."""

    def test_creates_file_if_missing(self, tmp_config_dir):
        """Registry file is created if it does not already exist."""
        kh_path = tmp_config_dir / "known_hosts"
        assert not kh_path.exists()
        save_known_host(_make_host())
        assert kh_path.exists()
        hosts = get_known_hosts()
        assert len(hosts) == 1
        assert hosts[0].name == "myhost/dev"

    def test_adds_entry(self, tmp_config_dir):
        """A new entry is appended to the registry."""
        save_known_host(_make_host(name="host1/c1"))
        save_known_host(_make_host(name="host2/c2"))
        hosts = get_known_hosts()
        assert len(hosts) == 2
        names = {h.name for h in hosts}
        assert names == {"host1/c1", "host2/c2"}

    def test_replaces_entry_with_same_type_and_name(self, tmp_config_dir):
        """An existing entry with the same (type, name) is replaced."""
        save_known_host(_make_host(host="10.0.0.1"))
        save_known_host(_make_host(host="10.0.0.2"))
        hosts = get_known_hosts()
        assert len(hosts) == 1
        assert hosts[0].host == "10.0.0.2"

    def test_preserves_other_entries(self, tmp_config_dir):
        """Other entries are preserved when one is replaced."""
        save_known_host(_make_host(type_="incus", name="host1/c1", host="10.0.0.1"))
        save_known_host(_make_host(type_="hetzner", name="web1", host="5.6.7.8"))
        save_known_host(_make_host(type_="incus", name="host1/c1", host="10.0.0.99"))
        hosts = get_known_hosts()
        assert len(hosts) == 2
        incus_hosts = [h for h in hosts if h.type == "incus"]
        hetzner_hosts = [h for h in hosts if h.type == "hetzner"]
        assert len(incus_hosts) == 1
        assert incus_hosts[0].host == "10.0.0.99"
        assert len(hetzner_hosts) == 1
        assert hetzner_hosts[0].host == "5.6.7.8"

    def test_preserves_unparseable_lines(self, tmp_config_dir):
        """Lines that cannot be parsed are preserved in the file."""
        _write_registry(tmp_config_dir, "# comment line\nbadline\nincus:h/c:10.0.0.1:remo\n")
        save_known_host(_make_host(type_="hetzner", name="web1", host="5.5.5.5"))
        raw = _read_registry(tmp_config_dir)
        assert "# comment line" in raw
        assert "badline" in raw
        hosts = get_known_hosts()
        assert len(hosts) == 2

    def test_preserves_empty_lines(self, tmp_config_dir):
        """Empty lines in the file are preserved."""
        _write_registry(tmp_config_dir, "incus:h/c:10.0.0.1:remo\n\n\n")
        save_known_host(_make_host(type_="hetzner", name="web1", host="5.5.5.5"))
        raw = _read_registry(tmp_config_dir)
        # There should still be empty lines present in the output.
        lines = raw.split("\n")
        empty_lines = [l for l in lines if l.strip() == ""]
        assert len(empty_lines) >= 2


# -----------------------------------------------------------------------
# remove_known_host()
# -----------------------------------------------------------------------


class TestRemoveKnownHost:
    """Removing entries from the registry."""

    def test_removes_matching_entry(self, tmp_config_dir):
        """Removes the entry with matching (type, name)."""
        save_known_host(_make_host(type_="incus", name="host1/c1"))
        save_known_host(_make_host(type_="hetzner", name="web1"))
        remove_known_host("incus", "host1/c1")
        hosts = get_known_hosts()
        assert len(hosts) == 1
        assert hosts[0].type == "hetzner"

    def test_no_op_if_not_found(self, tmp_config_dir):
        """Does nothing if no entry matches the given (type, name)."""
        save_known_host(_make_host(type_="incus", name="host1/c1"))
        remove_known_host("incus", "nonexistent")
        hosts = get_known_hosts()
        assert len(hosts) == 1
        assert hosts[0].name == "host1/c1"

    def test_no_op_if_file_does_not_exist(self, tmp_config_dir):
        """Does nothing if the registry file does not exist."""
        kh_path = tmp_config_dir / "known_hosts"
        assert not kh_path.exists()
        # Should not raise.
        remove_known_host("incus", "anything")

    def test_preserves_other_entries(self, tmp_config_dir):
        """Other entries are preserved after removal."""
        save_known_host(_make_host(type_="incus", name="host1/c1"))
        save_known_host(_make_host(type_="incus", name="host1/c2"))
        save_known_host(_make_host(type_="hetzner", name="web1"))
        remove_known_host("incus", "host1/c1")
        hosts = get_known_hosts()
        assert len(hosts) == 2
        names = {h.name for h in hosts}
        assert names == {"host1/c2", "web1"}


# -----------------------------------------------------------------------
# get_known_hosts()
# -----------------------------------------------------------------------


class TestGetKnownHosts:
    """Retrieving entries from the registry."""

    def test_returns_all_hosts(self, tmp_config_dir):
        """Returns all registered hosts when no filter is applied."""
        save_known_host(_make_host(type_="incus", name="host1/c1"))
        save_known_host(_make_host(type_="hetzner", name="web1"))
        save_known_host(_make_host(type_="aws", name="devbox"))
        hosts = get_known_hosts()
        assert len(hosts) == 3

    def test_filters_by_type(self, tmp_config_dir):
        """Returns only hosts matching the specified type."""
        save_known_host(_make_host(type_="incus", name="host1/c1"))
        save_known_host(_make_host(type_="incus", name="host2/c2"))
        save_known_host(_make_host(type_="hetzner", name="web1"))
        hosts = get_known_hosts(type_filter="incus")
        assert len(hosts) == 2
        assert all(h.type == "incus" for h in hosts)

    def test_returns_empty_list_if_file_missing(self, tmp_config_dir):
        """Returns an empty list when the registry file does not exist."""
        kh_path = tmp_config_dir / "known_hosts"
        assert not kh_path.exists()
        hosts = get_known_hosts()
        assert hosts == []

    def test_skips_empty_lines(self, tmp_config_dir):
        """Empty lines in the file are silently skipped."""
        _write_registry(tmp_config_dir, "\n\nincus:h/c:10.0.0.1:remo\n\n")
        hosts = get_known_hosts()
        assert len(hosts) == 1
        assert hosts[0].name == "h/c"

    def test_skips_unparseable_lines(self, tmp_config_dir):
        """Lines that cannot be parsed are silently skipped."""
        _write_registry(
            tmp_config_dir,
            "# this is a comment\nbad:line\nincus:h/c:10.0.0.1:remo\n",
        )
        hosts = get_known_hosts()
        assert len(hosts) == 1
        assert hosts[0].name == "h/c"

    def test_filter_returns_empty_for_unknown_type(self, tmp_config_dir):
        """Filtering by a type that has no entries returns an empty list."""
        save_known_host(_make_host(type_="incus", name="host1/c1"))
        hosts = get_known_hosts(type_filter="aws")
        assert hosts == []


# -----------------------------------------------------------------------
# clear_known_hosts_by_type()
# -----------------------------------------------------------------------


class TestClearKnownHostsByType:
    """Removing all entries of a given type."""

    def test_removes_all_entries_of_type(self, tmp_config_dir):
        """Removes all entries matching the given type."""
        save_known_host(_make_host(type_="incus", name="host1/c1"))
        save_known_host(_make_host(type_="incus", name="host2/c2"))
        save_known_host(_make_host(type_="hetzner", name="web1"))
        clear_known_hosts_by_type("incus")
        hosts = get_known_hosts()
        assert len(hosts) == 1
        assert hosts[0].type == "hetzner"

    def test_preserves_other_types(self, tmp_config_dir):
        """Entries of other types are preserved."""
        save_known_host(_make_host(type_="incus", name="host1/c1"))
        save_known_host(_make_host(type_="hetzner", name="web1"))
        save_known_host(_make_host(type_="aws", name="devbox"))
        clear_known_hosts_by_type("incus")
        hosts = get_known_hosts()
        types = {h.type for h in hosts}
        assert types == {"hetzner", "aws"}

    def test_no_op_if_file_missing(self, tmp_config_dir):
        """Does nothing if the registry file does not exist."""
        kh_path = tmp_config_dir / "known_hosts"
        assert not kh_path.exists()
        clear_known_hosts_by_type("incus")
        # Should not raise or create the file.
        assert not kh_path.exists()

    def test_no_op_if_type_not_present(self, tmp_config_dir):
        """Does nothing if no entries of the given type exist."""
        save_known_host(_make_host(type_="hetzner", name="web1"))
        clear_known_hosts_by_type("aws")
        hosts = get_known_hosts()
        assert len(hosts) == 1


# -----------------------------------------------------------------------
# clear_known_hosts_by_prefix()
# -----------------------------------------------------------------------


class TestClearKnownHostsByPrefix:
    """Removing entries matching type + name prefix."""

    def test_removes_entries_matching_prefix(self, tmp_config_dir):
        """Removes entries where type matches and name starts with prefix."""
        save_known_host(_make_host(type_="incus", name="myhost/c1"))
        save_known_host(_make_host(type_="incus", name="myhost/c2"))
        save_known_host(_make_host(type_="incus", name="otherhost/c3"))
        clear_known_hosts_by_prefix("incus", "myhost/")
        hosts = get_known_hosts()
        assert len(hosts) == 1
        assert hosts[0].name == "otherhost/c3"

    def test_preserves_other_types(self, tmp_config_dir):
        """Entries of other types are preserved even if name matches prefix."""
        save_known_host(_make_host(type_="incus", name="myhost/c1"))
        save_known_host(_make_host(type_="hetzner", name="myhost-web"))
        clear_known_hosts_by_prefix("incus", "myhost/")
        hosts = get_known_hosts()
        assert len(hosts) == 1
        assert hosts[0].type == "hetzner"

    def test_no_op_if_file_missing(self, tmp_config_dir):
        """Does nothing if the registry file does not exist."""
        kh_path = tmp_config_dir / "known_hosts"
        assert not kh_path.exists()
        clear_known_hosts_by_prefix("incus", "myhost/")
        assert not kh_path.exists()

    def test_no_op_if_no_prefix_match(self, tmp_config_dir):
        """Does nothing if no entries match the given prefix."""
        save_known_host(_make_host(type_="incus", name="otherhost/c1"))
        clear_known_hosts_by_prefix("incus", "myhost/")
        hosts = get_known_hosts()
        assert len(hosts) == 1
        assert hosts[0].name == "otherhost/c1"

    def test_preserves_entries_not_matching_prefix(self, tmp_config_dir):
        """Entries of the same type but different prefix are preserved."""
        save_known_host(_make_host(type_="incus", name="myhost/c1"))
        save_known_host(_make_host(type_="incus", name="myhost/c2"))
        save_known_host(_make_host(type_="incus", name="otherhost/c3"))
        save_known_host(_make_host(type_="incus", name="otherhost/c4"))
        clear_known_hosts_by_prefix("incus", "myhost/")
        hosts = get_known_hosts()
        assert len(hosts) == 2
        names = {h.name for h in hosts}
        assert names == {"otherhost/c3", "otherhost/c4"}


# -----------------------------------------------------------------------
# get_aws_region()
# -----------------------------------------------------------------------


class TestGetAwsRegion:
    """AWS region resolution."""

    def test_returns_region_from_matching_host(self, tmp_config_dir, monkeypatch):
        """Returns region stored in the matching AWS host entry."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        save_known_host(
            _make_host(
                type_="aws",
                name="devbox",
                host="1.2.3.4",
                instance_id="i-abc",
                access_mode="ssm",
                region="eu-west-1",
            )
        )
        assert get_aws_region("devbox") == "eu-west-1"

    def test_falls_back_to_aws_region_env(self, tmp_config_dir, monkeypatch):
        """Falls back to AWS_REGION env var when host has no region."""
        monkeypatch.setenv("AWS_REGION", "ap-southeast-1")
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        save_known_host(
            _make_host(
                type_="aws",
                name="devbox",
                host="1.2.3.4",
                instance_id="i-abc",
                access_mode="ssm",
                region="",
            )
        )
        assert get_aws_region("devbox") == "ap-southeast-1"

    def test_falls_back_to_aws_default_region_env(self, tmp_config_dir, monkeypatch):
        """Falls back to AWS_DEFAULT_REGION when AWS_REGION is unset."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "sa-east-1")
        save_known_host(
            _make_host(
                type_="aws",
                name="devbox",
                host="1.2.3.4",
                instance_id="i-abc",
                access_mode="ssm",
            )
        )
        assert get_aws_region("devbox") == "sa-east-1"

    def test_falls_back_to_us_west_2(self, tmp_config_dir, monkeypatch):
        """Falls back to 'us-west-2' when no region info is available."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        assert get_aws_region("nonexistent") == "us-west-2"

    def test_ignores_non_matching_host(self, tmp_config_dir, monkeypatch):
        """Does not use region from a different AWS host."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        save_known_host(
            _make_host(
                type_="aws",
                name="other-box",
                host="1.2.3.4",
                instance_id="i-abc",
                access_mode="ssm",
                region="eu-west-1",
            )
        )
        assert get_aws_region("devbox") == "us-west-2"

    def test_ignores_non_aws_host(self, tmp_config_dir, monkeypatch):
        """Ignores non-aws hosts even if they have matching names."""
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        save_known_host(_make_host(type_="incus", name="devbox"))
        assert get_aws_region("devbox") == "us-west-2"

    def test_aws_region_env_priority_over_default_region(self, tmp_config_dir, monkeypatch):
        """AWS_REGION takes priority over AWS_DEFAULT_REGION."""
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-1")
        assert get_aws_region("nonexistent") == "us-east-1"


# -----------------------------------------------------------------------
# resolve_remo_host_by_name()
# -----------------------------------------------------------------------


class TestResolveRemoHostByName:
    """Host name resolution across all types."""

    def test_exact_match(self, tmp_config_dir):
        """Finds a host by exact name match."""
        save_known_host(_make_host(type_="hetzner", name="webserver", host="5.6.7.8"))
        result = resolve_remo_host_by_name("webserver")
        assert result.name == "webserver"
        assert result.type == "hetzner"

    def test_exact_match_incus_full_name(self, tmp_config_dir):
        """Finds an incus host by full 'host/container' name."""
        save_known_host(_make_host(type_="incus", name="myhost/dev"))
        result = resolve_remo_host_by_name("myhost/dev")
        assert result.name == "myhost/dev"
        assert result.type == "incus"

    def test_incus_short_name_match(self, tmp_config_dir):
        """Finds an incus host by the container part alone (short name)."""
        save_known_host(_make_host(type_="incus", name="myhost/devcontainer"))
        result = resolve_remo_host_by_name("devcontainer")
        assert result.name == "myhost/devcontainer"
        assert result.type == "incus"

    def test_exact_match_takes_priority_over_short_name(self, tmp_config_dir):
        """Exact name match takes priority over incus short-name match."""
        save_known_host(_make_host(type_="hetzner", name="dev", host="5.5.5.5"))
        save_known_host(_make_host(type_="incus", name="myhost/dev", host="10.0.0.1"))
        result = resolve_remo_host_by_name("dev")
        # The exact match (hetzner 'dev') should win over the incus short name.
        assert result.type == "hetzner"
        assert result.name == "dev"

    def test_system_exit_when_not_found_with_available(self, tmp_config_dir):
        """Raises SystemExit with helpful message listing available environments."""
        save_known_host(_make_host(type_="incus", name="myhost/dev"))
        save_known_host(_make_host(type_="hetzner", name="webserver"))
        with pytest.raises(SystemExit) as exc_info:
            resolve_remo_host_by_name("nonexistent")
        error_msg = str(exc_info.value)
        assert "nonexistent" in error_msg
        assert "Available environments" in error_msg

    def test_system_exit_when_registry_empty(self, tmp_config_dir):
        """Raises SystemExit with empty-registry message when no hosts exist."""
        with pytest.raises(SystemExit) as exc_info:
            resolve_remo_host_by_name("anything")
        error_msg = str(exc_info.value)
        assert "anything" in error_msg
        assert "registry is empty" in error_msg
        assert "remo add" in error_msg

    def test_system_exit_message_uses_display_name(self, tmp_config_dir):
        """The error listing uses display_name for readability."""
        save_known_host(_make_host(type_="incus", name="myhost/devcontainer"))
        with pytest.raises(SystemExit) as exc_info:
            resolve_remo_host_by_name("nonexistent")
        error_msg = str(exc_info.value)
        # display_name for incus "myhost/devcontainer" is "devcontainer (on myhost)"
        assert "devcontainer (on myhost)" in error_msg

    def test_aws_host_exact_match(self, tmp_config_dir):
        """Finds an AWS host by exact name."""
        save_known_host(
            _make_host(
                type_="aws",
                name="prod-server",
                host="3.14.15.92",
                instance_id="i-abc",
                access_mode="ssm",
                region="us-west-2",
            )
        )
        result = resolve_remo_host_by_name("prod-server")
        assert result.type == "aws"
        assert result.instance_id == "i-abc"

    def test_short_name_only_matches_incus(self, tmp_config_dir):
        """Short-name matching only works for incus type hosts."""
        # An AWS host with a name like "org/machine" won't match "machine" as short name.
        save_known_host(_make_host(type_="aws", name="org/machine", host="1.2.3.4"))
        with pytest.raises(SystemExit):
            resolve_remo_host_by_name("machine")
