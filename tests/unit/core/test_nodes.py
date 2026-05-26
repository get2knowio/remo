"""Tests for remo_cli.core.nodes (atomic write, perms, round-trip)."""

import os
import stat

import pytest

from remo_cli.core import nodes


def _add_workstation(**overrides):
    base = dict(
        name="ws-01",
        provider="incus",
        host="192.168.4.10",
        ssh_user="incusadmin",
        admin_sa_fnox_key="incus_ws_01_admin_sa",
        registered_at="2026-05-25T10:00:00Z",
    )
    base.update(overrides)
    return nodes.add_node(**base)


def test_empty_when_no_file(tmp_config_dir):
    assert nodes.list_nodes() == []
    assert nodes.get_node("anything") is None


def test_add_then_list_round_trip(tmp_config_dir):
    node = _add_workstation()
    listed = nodes.list_nodes()
    assert listed == [node]
    found = nodes.get_node("ws-01")
    assert found == node


def test_file_mode_is_0600_on_write(tmp_config_dir):
    _add_workstation()
    path = tmp_config_dir / "nodes.yml"
    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_refuses_wider_than_0600_on_read(tmp_config_dir):
    _add_workstation()
    path = tmp_config_dir / "nodes.yml"
    os.chmod(path, 0o644)
    with pytest.raises(nodes.NodesError, match="wider than 0600"):
        nodes.list_nodes()


def test_idempotent_re_add_same_fields(tmp_config_dir):
    n1 = _add_workstation()
    n2 = _add_workstation(registered_at="2027-01-01T00:00:00Z")
    # Same name+fields → returns existing entry (registered_at on existing is preserved)
    assert n2.registered_at == n1.registered_at


def test_conflicting_re_add_raises(tmp_config_dir):
    _add_workstation()
    with pytest.raises(nodes.NodesError, match="already registered"):
        _add_workstation(host="10.0.0.99")


def test_remove_node_present(tmp_config_dir):
    _add_workstation()
    assert nodes.remove_node("ws-01") is True
    assert nodes.list_nodes() == []


def test_remove_node_absent(tmp_config_dir):
    assert nodes.remove_node("missing") is False


def test_version_1_round_trip(tmp_config_dir):
    _add_workstation()
    raw = (tmp_config_dir / "nodes.yml").read_text()
    assert "version: 1" in raw
    assert "ws-01" in raw
