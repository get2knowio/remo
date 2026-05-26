"""Tests for the Node dataclass validation rules."""

import pytest

from remo_cli.models.node import Node, NodeValidationError


def _valid(**overrides):
    base = dict(
        name="ws-01",
        provider="incus",
        host="192.168.4.10",
        ssh_user="incusadmin",
        admin_sa_fnox_key="incus_ws_01_admin_sa",
        registered_at="2026-05-25T10:00:00Z",
    )
    base.update(overrides)
    return base


def test_valid_incus_node():
    node = Node(**_valid())
    assert node.name == "ws-01"
    assert node.provider == "incus"


def test_valid_proxmox_node():
    node = Node(**_valid(provider="proxmox", name="lab-prox-02"))
    assert node.provider == "proxmox"


def test_invalid_name_uppercase():
    with pytest.raises(NodeValidationError, match="invalid node name"):
        Node(**_valid(name="Workstation"))


def test_invalid_name_starts_with_digit():
    with pytest.raises(NodeValidationError, match="invalid node name"):
        Node(**_valid(name="1node"))


def test_invalid_name_too_long():
    with pytest.raises(NodeValidationError, match="invalid node name"):
        Node(**_valid(name="a" * 33))


def test_invalid_provider():
    with pytest.raises(NodeValidationError, match="invalid provider"):
        Node(**_valid(provider="aws"))


def test_empty_host():
    with pytest.raises(NodeValidationError, match="host must be non-empty"):
        Node(**_valid(host=""))


def test_empty_ssh_user():
    with pytest.raises(NodeValidationError, match="ssh_user must be non-empty"):
        Node(**_valid(ssh_user=""))


def test_invalid_admin_sa_fnox_key():
    with pytest.raises(NodeValidationError, match="invalid admin_sa_fnox_key"):
        Node(**_valid(admin_sa_fnox_key="INVALID-Key"))


def test_to_dict_round_trip():
    node = Node(**_valid())
    data = node.to_dict()
    restored = Node.from_dict(data)
    assert restored == node


def test_from_dict_missing_fields():
    with pytest.raises(NodeValidationError, match="missing fields"):
        Node.from_dict({"name": "x"})
