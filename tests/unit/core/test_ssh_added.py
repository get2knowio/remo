"""Tests for added-host (type=ssh) SSH option building in core/ssh.py (feature 014).

`build_ssh_opts` is the single shared SSH-argv builder used by BOTH `remo shell`
(shell_connect) and `remo cp` (cli/cp.py), so these opts also govern file
transfer to an added host (FR-005). For `type=="ssh"` it must emit `-o Port=`
(only when the port is non-default) and fold in the stored identity; for every
other provider type the argv must be byte-identical to before this feature.
"""

from __future__ import annotations

import pytest

from remo_cli.models.host import KnownHost


@pytest.fixture(autouse=True)
def _no_timezone(mocker):
    # Keep argv deterministic — drop the optional `SendEnv=TZ` tail.
    mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")


def _opts(host: KnownHost) -> list[str]:
    from remo_cli.core.ssh import build_ssh_opts

    opts, _target = build_ssh_opts(host)
    return opts


def _ssh(type_: str = "ssh", **kw) -> KnownHost:
    base = dict(name="box", host="1.2.3.4", user="remo", access_mode="direct")
    base.update(kw)
    return KnownHost(type=type_, **base)  # type: ignore[arg-type]


class TestSshTypePortIdentity:
    def test_default_port_emits_no_port_flag(self) -> None:
        from remo_cli.core.ssh import build_ssh_opts

        host = _ssh(instance_id="22")
        opts, target = build_ssh_opts(host)
        assert target == "remo@1.2.3.4"
        assert not any(o.startswith("Port=") for o in opts)

    def test_custom_port_emits_port_flag(self) -> None:
        opts = _opts(_ssh(instance_id="2222"))
        assert "-o" in opts and "Port=2222" in opts

    def test_stored_identity_emitted(self) -> None:
        opts = _opts(_ssh(instance_id="22", region="/k/id_ed25519"))
        assert "IdentityFile=/k/id_ed25519" in opts
        assert "IdentitiesOnly=yes" in opts

    def test_explicit_identity_file_wins_over_stored(self) -> None:
        from remo_cli.core.ssh import build_ssh_opts

        host = _ssh(instance_id="22", region="/k/stored")
        opts, _ = build_ssh_opts(host, identity_file="/k/explicit")
        assert "IdentityFile=/k/explicit" in opts
        assert "IdentityFile=/k/stored" not in opts

    def test_no_identity_emits_nothing(self) -> None:
        opts = _opts(_ssh(instance_id="22"))
        assert not any(o.startswith("IdentityFile=") for o in opts)


class TestOtherTypesUnchanged:
    def test_proxmox_vmid_not_treated_as_port(self) -> None:
        # Proxmox stores a numeric vmid in instance_id; it must NOT become a port.
        from remo_cli.core.ssh import build_ssh_opts

        pmx = KnownHost(
            type="proxmox",
            name="node/dev1",
            host="10.0.0.1",
            user="remo",
            instance_id="100",
            region="root",
        )
        opts, target = build_ssh_opts(pmx)
        assert target == "remo@10.0.0.1"
        assert not any(o.startswith("Port=") for o in opts)
        assert not any(o.startswith("IdentityFile=") for o in opts)

    def test_incus_argv_has_no_added_flags(self) -> None:
        from remo_cli.core.ssh import build_ssh_opts

        incus = KnownHost(
            type="incus",
            name="myhost/dev",
            host="192.168.1.50",
            user="remo",
        )
        opts, target = build_ssh_opts(incus)
        assert target == "remo@192.168.1.50"
        assert opts == []  # timezone patched out → byte-identical empty opts
