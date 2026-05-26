"""Phase 2 / T078: cadence-days persistence at provider create time."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_hetzner_create_writes_cadence_label(mocker, monkeypatch):
    from remo_cli.providers import hetzner as hetz

    monkeypatch.delenv("REMO_BROKER_BACKEND", raising=False)
    mocker.patch("remo_cli.providers.hetzner.run_playbook", return_value=0)
    mocker.patch(
        "remo_cli.providers.hetzner._query_hetzner_server_ip",
        return_value="1.2.3.4",
    )
    mocker.patch("remo_cli.providers.hetzner.save_known_host")
    mocker.patch("remo_cli.providers.hetzner._hetzner_server_id", return_value=99)
    mocker.patch("remo_cli.providers.hetzner.get_current_version", return_value="2.1.0")
    set_label = mocker.patch("remo_cli.providers.hetzner._set_server_label")

    rc = hetz.create(name="hetz-1", cadence_days=14)
    assert rc == 0
    set_label.assert_called_with(99, "remo_rotation_cadence_days", "14")


def test_hetzner_create_skips_cadence_when_flag_unset(mocker, monkeypatch):
    from remo_cli.providers import hetzner as hetz

    monkeypatch.delenv("REMO_BROKER_BACKEND", raising=False)
    mocker.patch("remo_cli.providers.hetzner.run_playbook", return_value=0)
    mocker.patch(
        "remo_cli.providers.hetzner._query_hetzner_server_ip",
        return_value="1.2.3.4",
    )
    mocker.patch("remo_cli.providers.hetzner.save_known_host")
    mocker.patch("remo_cli.providers.hetzner._hetzner_server_id", return_value=99)
    mocker.patch("remo_cli.providers.hetzner.get_current_version", return_value="2.1.0")
    set_label = mocker.patch("remo_cli.providers.hetzner._set_server_label")

    rc = hetz.create(name="hetz-1")  # no cadence_days
    assert rc == 0
    set_label.assert_not_called()


def test_aws_create_tags_cadence(mocker, monkeypatch):
    from remo_cli.providers import aws as aws_mod

    monkeypatch.delenv("REMO_BROKER_BACKEND", raising=False)
    monkeypatch.setenv("USER", "remo")
    mocker.patch("remo_cli.providers.aws.require_session_manager_plugin")
    mocker.patch("remo_cli.providers.aws.select_ssm_instance_profile", return_value="prof")
    mocker.patch("remo_cli.providers.aws.run_playbook", return_value=0)
    mocker.patch(
        "remo_cli.providers.aws._get_running_instance",
        return_value={"PublicIpAddress": "1.2.3.4", "InstanceId": "i-abc"},
    )
    mocker.patch("remo_cli.providers.aws.save_known_host")
    mocker.patch("remo_cli.providers.aws.get_current_version", return_value="2.1.0")
    ec2 = MagicMock()
    session = MagicMock()
    session.client.return_value = ec2
    mocker.patch("remo_cli.providers.aws._boto3_session", return_value=session)

    rc = aws_mod.create(name="dev", iam_profile="prof", cadence_days=21)
    assert rc == 0
    ec2.create_tags.assert_called_once()
    kwargs = ec2.create_tags.call_args.kwargs
    assert kwargs["Resources"] == ["i-abc"]
    assert kwargs["Tags"] == [
        {"Key": "remo:rotation-cadence-days", "Value": "21"}
    ]


def test_incus_create_sets_user_config_cadence(mocker):
    from remo_cli.providers import incus as incus_mod

    mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
    mocker.patch("remo_cli.providers.incus.save_known_host")
    mocker.patch("remo_cli.providers.incus.remove_known_host")
    mocker.patch("remo_cli.providers.incus.get_current_version", return_value="2.1.0")
    mocker.patch("remo_cli.providers.incus.detect_timezone", return_value="")
    ssh_run = mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host",
        return_value=MagicMock(returncode=0, stdout="", stderr=""),
    )

    rc = incus_mod.create(name="dev1", cadence_days=10)
    assert rc == 0
    # The cadence write is the only _ssh_run_on_incus_host call in this path
    # (no resize, no volume) — find it among any calls.
    found = any(
        "user.remo.rotation_cadence_days 10" in call.args[2]
        for call in ssh_run.call_args_list
    )
    assert found, f"cadence write not in SSH calls: {ssh_run.call_args_list}"


def test_proxmox_create_warns_cadence_deferred(mocker, capsys):
    from remo_cli.providers import proxmox as prox_mod

    mocker.patch("remo_cli.providers.proxmox.run_playbook", return_value=0)
    mocker.patch("remo_cli.providers.proxmox.save_known_host")
    mocker.patch("remo_cli.providers.proxmox.remove_known_host")
    mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="200")
    mocker.patch("remo_cli.providers.proxmox.get_current_version", return_value="2.1.0")
    mocker.patch("remo_cli.providers.proxmox.detect_timezone", return_value="")

    rc = prox_mod.create(name="dev1", host="prox-host", cadence_days=14)
    assert rc == 0
    out = capsys.readouterr()
    assert "not yet persisted for" in (out.out + out.err)
