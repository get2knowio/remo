from __future__ import annotations

from remo_cli.models.capability import RemoteCapability
from remo_cli.models.host_info import HostInfo


def test_empty_and_none():
    assert HostInfo.parse("") == HostInfo()
    assert HostInfo.parse(None) == HostInfo()


def test_full_record():
    info = HostInfo.parse("host_kind=local\nmenu_command=1\ninstalled_by=5.1.0\n")
    assert info == HostInfo("local", True, "5.1.0")
    assert info.is_local_managed


def test_remote_not_local_managed():
    assert not HostInfo.parse("host_kind=remote").is_local_managed


def test_junk_and_unknown():
    info = HostInfo.parse("garbage\n=x\nfoo=bar\nhost_kind=weird\nmenu_command=yes\ninstalled_by=\n")
    assert info == HostInfo()


def test_crlf():
    assert HostInfo.parse("host_kind=local\r\nmenu_command=1\r\n") == HostInfo("local", True, None)


def test_capability_host_kind():
    base = {"protocol_version": 1}
    assert RemoteCapability.from_dict({**base, "host_kind": "local"}).host_kind == "local"
    assert RemoteCapability.from_dict({**base, "host_kind": "remote"}).host_kind == "remote"
    assert RemoteCapability.from_dict({**base, "host_kind": "odd"}).host_kind is None
    assert RemoteCapability.from_dict({**base, "host_kind": 3}).host_kind is None
    assert RemoteCapability.from_dict(base).host_kind is None
