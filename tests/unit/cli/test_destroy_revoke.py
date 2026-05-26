"""US5 T082: assert `_revoke_before_destroy` runs before delete; --force bypass."""

from __future__ import annotations

import pytest

from remo_cli.core import broker_revoke
from remo_cli.models.host import KnownHost


def _host() -> KnownHost:
    return KnownHost(type="hetzner", name="web-1", host="1.2.3.4", user="remo")


def test_no_backend_skips_revoke(monkeypatch, mocker):
    monkeypatch.delenv("REMO_BROKER_BACKEND", raising=False)
    # Should return True (= proceed) without calling the broker module.
    revoke = mocker.patch(
        "remo_cli.providers.broker.revoke_bootstrap_token", return_value=None
    )
    assert broker_revoke.revoke_before_destroy(_host()) is True
    revoke.assert_not_called()


def test_no_token_id_skips_revoke(monkeypatch, mocker):
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.core.broker_revoke._lookup_token_id", return_value=None
    )
    revoke = mocker.patch(
        "remo_cli.providers.broker.revoke_bootstrap_token", return_value=None
    )
    assert broker_revoke.revoke_before_destroy(_host()) is True
    revoke.assert_not_called()


def test_revoke_called_when_token_id_present(monkeypatch, mocker):
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.core.broker_revoke._lookup_token_id", return_value="scim-abc"
    )
    revoke = mocker.patch(
        "remo_cli.providers.broker.revoke_bootstrap_token", return_value=None
    )
    assert broker_revoke.revoke_before_destroy(_host()) is True
    revoke.assert_called_once()
    kwargs = revoke.call_args.kwargs
    assert kwargs["token_id"] == "scim-abc"


def test_revoke_failure_aborts_destroy(monkeypatch, mocker):
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.core.broker_revoke._lookup_token_id", return_value="scim-abc"
    )
    from remo_cli.providers import broker
    mocker.patch(
        "remo_cli.providers.broker.revoke_bootstrap_token",
        side_effect=broker.BackendError("rate-limited"),
    )
    assert broker_revoke.revoke_before_destroy(_host()) is False


def test_force_bypass_proceeds_on_failure(monkeypatch, mocker):
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.core.broker_revoke._lookup_token_id", return_value="scim-abc"
    )
    from remo_cli.providers import broker
    mocker.patch(
        "remo_cli.providers.broker.revoke_bootstrap_token",
        side_effect=broker.BackendError("rate-limited"),
    )
    assert broker_revoke.revoke_before_destroy(_host(), force=True) is True


# ---------------------------------------------------------------------------
# FR-020 enforcement across providers: AWS, Incus, Proxmox destroy() must
# invoke revoke_before_destroy and abort with exit code 5 on failure.
# ---------------------------------------------------------------------------


def test_aws_destroy_aborts_when_revoke_fails(monkeypatch, mocker):
    from remo_cli.providers import aws as aws_provider

    monkeypatch.setenv("USER", "tester")
    mocker.patch(
        "remo_cli.providers.aws.get_aws_region", return_value="us-west-2"
    )
    mocker.patch(
        "remo_cli.core.broker_revoke.revoke_before_destroy", return_value=False
    )
    run_pb = mocker.patch(
        "remo_cli.providers.aws.run_playbook", return_value=0
    )
    snap_list = mocker.patch(
        "remo_cli.providers.aws.snapshot_list", return_value=[]
    )

    rc = aws_provider.destroy(name="web-1", auto_confirm=True)

    assert rc == 5
    run_pb.assert_not_called()
    snap_list.assert_not_called()


def test_aws_destroy_proceeds_when_revoke_succeeds(monkeypatch, mocker):
    from remo_cli.providers import aws as aws_provider

    monkeypatch.setenv("USER", "tester")
    mocker.patch(
        "remo_cli.providers.aws.get_aws_region", return_value="us-west-2"
    )
    mocker.patch(
        "remo_cli.core.broker_revoke.revoke_before_destroy", return_value=True
    )
    mocker.patch(
        "remo_cli.providers.aws.snapshot_list", return_value=[]
    )
    mocker.patch(
        "remo_cli.providers.aws.handle_destroy_snapshot_cleanup"
    )
    mocker.patch(
        "remo_cli.providers.aws.remove_known_host"
    )
    run_pb = mocker.patch(
        "remo_cli.providers.aws.run_playbook", return_value=0
    )

    rc = aws_provider.destroy(name="web-1", auto_confirm=True)

    assert rc == 0
    run_pb.assert_called_once()


def test_incus_destroy_aborts_when_revoke_fails(mocker):
    from remo_cli.providers import incus as incus_provider

    mocker.patch(
        "remo_cli.core.broker_revoke.revoke_before_destroy", return_value=False
    )
    run_pb = mocker.patch(
        "remo_cli.providers.incus.run_playbook", return_value=0
    )

    rc = incus_provider.destroy(name="dev1", host="localhost", auto_confirm=True)

    assert rc == 5
    run_pb.assert_not_called()


def test_incus_destroy_proceeds_when_revoke_succeeds(mocker):
    from remo_cli.providers import incus as incus_provider

    mocker.patch(
        "remo_cli.core.broker_revoke.revoke_before_destroy", return_value=True
    )
    mocker.patch(
        "remo_cli.providers.incus._list_snapshots_for_container", return_value=[]
    )
    mocker.patch(
        "remo_cli.providers.incus.handle_destroy_snapshot_cleanup"
    )
    mocker.patch(
        "remo_cli.providers.incus.remove_known_host"
    )
    run_pb = mocker.patch(
        "remo_cli.providers.incus.run_playbook", return_value=0
    )

    rc = incus_provider.destroy(name="dev1", host="localhost", auto_confirm=True)

    assert rc == 0
    run_pb.assert_called_once()


def test_proxmox_destroy_aborts_when_revoke_fails(mocker):
    from remo_cli.providers import proxmox as proxmox_provider

    mocker.patch(
        "remo_cli.core.broker_revoke.revoke_before_destroy", return_value=False
    )
    run_pb = mocker.patch(
        "remo_cli.providers.proxmox.run_playbook", return_value=0
    )

    rc = proxmox_provider.destroy(
        name="dev1", host="pve.example.com", user="root", auto_confirm=True
    )

    assert rc == 5
    run_pb.assert_not_called()


def test_proxmox_destroy_proceeds_when_revoke_succeeds(mocker):
    from remo_cli.providers import proxmox as proxmox_provider

    mocker.patch(
        "remo_cli.core.broker_revoke.revoke_before_destroy", return_value=True
    )
    mocker.patch(
        "remo_cli.providers.proxmox.handle_destroy_snapshot_cleanup"
    )
    mocker.patch(
        "remo_cli.providers.proxmox.remove_known_host"
    )
    run_pb = mocker.patch(
        "remo_cli.providers.proxmox.run_playbook", return_value=0
    )

    rc = proxmox_provider.destroy(
        name="dev1", host="pve.example.com", user="root", auto_confirm=True
    )

    assert rc == 0
    run_pb.assert_called_once()
