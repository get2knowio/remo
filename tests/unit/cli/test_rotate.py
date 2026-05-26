"""US5 T083: rotate-bootstrap — fresh-skip, --all, --force, partial-success exit 7."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from click.testing import CliRunner

from remo_cli.cli.rotate import _is_overdue, rotate_command
from remo_cli.core.known_hosts import save_known_host
from remo_cli.models.host import KnownHost


def test_is_overdue_when_never_rotated():
    assert _is_overdue(cadence_days=7, last_rotation=None) is True


def test_is_overdue_cadence_zero_disabled():
    assert _is_overdue(cadence_days=0, last_rotation=None) is False


def test_is_overdue_under_cadence():
    last = datetime.now(timezone.utc) - timedelta(days=3)
    assert _is_overdue(cadence_days=7, last_rotation=last) is False


def test_is_overdue_over_cadence():
    last = datetime.now(timezone.utc) - timedelta(days=8)
    assert _is_overdue(cadence_days=7, last_rotation=last) is True


def test_rotate_no_instances_due(tmp_config_dir, mocker):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    # All instances appear fresh.
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc), "tok-1"),
    )

    runner = CliRunner()
    r = runner.invoke(rotate_command, [])
    assert r.exit_code == 0
    assert "No instances are due" in r.output


def test_rotate_specific_instance_freshness_skip(tmp_config_dir, mocker, monkeypatch):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(minutes=10), "tok-1"),
    )
    mint = mocker.patch("remo_cli.providers.broker.mint_bootstrap_token")

    runner = CliRunner()
    r = runner.invoke(rotate_command, ["hetz-1"])
    assert r.exit_code == 0
    mint.assert_not_called()
    assert "Skipped" in r.output


def test_rotate_force_overrides_fresh_skip(tmp_config_dir, mocker, monkeypatch):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(minutes=10), "tok-old"),
    )
    mint = mocker.patch(
        "remo_cli.providers.broker.mint_bootstrap_token",
        return_value={"token": "new", "token_id": "tok-new"},
    )
    revoke = mocker.patch(
        "remo_cli.providers.broker.revoke_bootstrap_token", return_value=None
    )
    deliver = mocker.patch("remo_cli.cli.rotate._deliver_and_reload")

    runner = CliRunner()
    r = runner.invoke(rotate_command, ["hetz-1", "--force"])
    assert r.exit_code == 0
    mint.assert_called_once()
    deliver.assert_called_once()
    revoke.assert_called_once()


def test_rotate_partial_success_exit_7(tmp_config_dir, mocker, monkeypatch):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    save_known_host(KnownHost(type="hetzner", name="hetz-2", host="1.1.1.2", user="remo"))
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(days=8), None),
    )
    from remo_cli.providers import broker

    def _mint(*a, **kw):
        if kw.get("instance_id") == "hetz-1":
            return {"token": "t", "token_id": "tid1"}
        raise broker.BackendError("backend rate-limited")

    mocker.patch("remo_cli.providers.broker.mint_bootstrap_token", side_effect=_mint)
    mocker.patch("remo_cli.providers.broker.revoke_bootstrap_token", return_value=None)
    mocker.patch("remo_cli.cli.rotate._deliver_and_reload")

    runner = CliRunner()
    r = runner.invoke(rotate_command, ["--all"])
    assert r.exit_code == 7


def test_rotate_calls_push_and_admin_reload_for_hetzner(tmp_config_dir, mocker, monkeypatch):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(days=8), "tok-old"),
    )
    mocker.patch(
        "remo_cli.providers.broker.mint_bootstrap_token",
        return_value={"token": "fresh-secret", "token_id": "tok-new"},
    )
    mocker.patch("remo_cli.providers.broker.revoke_bootstrap_token", return_value=None)
    mocker.patch("remo_cli.providers.hetzner._hetzner_server_id", return_value=42)
    push = mocker.patch("remo_cli.providers.hetzner._push_bootstrap_token")
    reload_ = mocker.patch("remo_cli.core.broker_admin.rotate_bootstrap")

    runner = CliRunner()
    r = runner.invoke(rotate_command, ["hetz-1"])
    assert r.exit_code == 0
    push.assert_called_once_with("1.1.1.1", "fresh-secret", ssh_user="root", server_id=42)
    reload_.assert_called_once_with(ssh_host="1.1.1.1", ssh_user="root")


def test_rotate_calls_push_and_admin_reload_for_proxmox(tmp_config_dir, mocker, monkeypatch):
    save_known_host(KnownHost(
        type="proxmox", name="px-host/px-1", host="px-1", user="remo",
        instance_id="200", region="root", access_mode="direct",
    ))
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(days=8), "tok-old"),
    )
    mocker.patch(
        "remo_cli.providers.broker.mint_bootstrap_token",
        return_value={"token": "fresh-secret", "token_id": "tok-new"},
    )
    mocker.patch("remo_cli.providers.broker.revoke_bootstrap_token", return_value=None)
    mocker.patch("remo_cli.cli.rotate._record_rotation")
    push = mocker.patch("remo_cli.providers.proxmox._push_bootstrap_token_to_container")
    reload_ = mocker.patch("remo_cli.core.broker_admin.rotate_bootstrap_via_proxmox")

    runner = CliRunner()
    r = runner.invoke(rotate_command, ["px-1"])
    assert r.exit_code == 0
    push.assert_called_once_with("px-host", "root", "200", "fresh-secret")
    reload_.assert_called_once_with(
        proxmox_host="px-host", host_user="root", vmid="200"
    )


def test_rotate_delivery_failure_returns_partial_exit(tmp_config_dir, mocker, monkeypatch):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(days=8), None),
    )
    mocker.patch(
        "remo_cli.providers.broker.mint_bootstrap_token",
        return_value={"token": "fresh", "token_id": "tid"},
    )
    revoke = mocker.patch("remo_cli.providers.broker.revoke_bootstrap_token")
    mocker.patch(
        "remo_cli.cli.rotate._deliver_and_reload",
        side_effect=RuntimeError("ssh refused"),
    )

    runner = CliRunner()
    r = runner.invoke(rotate_command, ["hetz-1", "--force"])
    assert r.exit_code == 7
    # Old token must NOT be revoked when delivery failed — broker is still
    # serving with it.
    revoke.assert_not_called()
    assert "delivery to instance failed" in r.output


def test_rotate_records_metadata_after_success(tmp_config_dir, mocker, monkeypatch):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(days=8), "tok-old"),
    )
    mocker.patch(
        "remo_cli.providers.broker.mint_bootstrap_token",
        return_value={"token": "fresh", "token_id": "tok-new"},
    )
    mocker.patch("remo_cli.providers.broker.revoke_bootstrap_token", return_value=None)
    mocker.patch("remo_cli.cli.rotate._deliver_and_reload")
    mocker.patch("remo_cli.providers.hetzner._hetzner_server_id", return_value=42)
    set_label = mocker.patch("remo_cli.providers.hetzner._set_server_label")

    runner = CliRunner()
    r = runner.invoke(rotate_command, ["hetz-1", "--force"])
    assert r.exit_code == 0
    # Two label writes: last_rotation_at + bootstrap_token_id.
    keys_written = [call.args[1] for call in set_label.call_args_list]
    assert "remo_last_rotation_at" in keys_written
    assert "remo_bootstrap_token_id" in keys_written


def test_record_rotation_writes_aws_tag(mocker):
    from remo_cli.cli.rotate import _record_rotation
    host = KnownHost(
        type="aws", name="dev", host="i-abc", user="remo",
        instance_id="i-abc", region="us-west-2",
    )
    ec2 = mocker.MagicMock()
    session = mocker.MagicMock()
    session.client.return_value = ec2
    mocker.patch("remo_cli.providers.aws._boto3_session", return_value=session)

    _record_rotation(host, "ignored-on-aws")

    ec2.create_tags.assert_called_once()
    tags = ec2.create_tags.call_args.kwargs["Tags"]
    assert any(t["Key"] == "remo:last-rotation-at" for t in tags)


def test_read_rotation_metadata_aws_tags(mocker):
    from remo_cli.cli.rotate import _read_rotation_metadata
    host = KnownHost(
        type="aws", name="dev", host="i-abc", user="remo",
        instance_id="i-abc", region="us-west-2",
    )
    ec2 = mocker.MagicMock()
    ec2.describe_tags.return_value = {
        "Tags": [
            {"Key": "remo:rotation-cadence-days", "Value": "14"},
            {"Key": "remo:last-rotation-at", "Value": "2026-05-26T12:00:00+00:00"},
        ]
    }
    session = mocker.MagicMock()
    session.client.return_value = ec2
    mocker.patch("remo_cli.providers.aws._boto3_session", return_value=session)

    cadence, last, token_id = _read_rotation_metadata(host)
    assert cadence == 14
    assert last is not None
    assert last.tzinfo is not None
    assert token_id is None  # AWS token_id is derived, not stored.


def test_rotate_calls_push_and_admin_reload_for_incus(tmp_config_dir, mocker, monkeypatch):
    save_known_host(
        KnownHost(
            type="incus",
            name="incus-host/lxc-1",
            host="lxc-1",
            user="remo",
            instance_id="ubuntu",
            access_mode="direct",
        )
    )
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(days=8), "tok-old"),
    )
    mocker.patch(
        "remo_cli.providers.broker.mint_bootstrap_token",
        return_value={"token": "fresh-secret", "token_id": "tok-new"},
    )
    mocker.patch("remo_cli.providers.broker.revoke_bootstrap_token", return_value=None)
    mocker.patch("remo_cli.cli.rotate._record_rotation")
    push = mocker.patch(
        "remo_cli.providers.incus._push_bootstrap_token_to_container"
    )
    reload_ = mocker.patch(
        "remo_cli.core.broker_admin.rotate_bootstrap_via_incus"
    )

    runner = CliRunner()
    r = runner.invoke(rotate_command, ["lxc-1"])
    assert r.exit_code == 0, r.output
    push.assert_called_once_with("incus-host", "ubuntu", "lxc-1", "fresh-secret")
    reload_.assert_called_once_with(
        incus_host="incus-host", incus_host_user="ubuntu", container="lxc-1"
    )


def test_record_rotation_writes_incus_config(mocker):
    from remo_cli.cli.rotate import _record_rotation
    host = KnownHost(
        type="incus",
        name="incus-host/lxc-1",
        host="lxc-1",
        user="remo",
        instance_id="ubuntu",
        access_mode="direct",
    )

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    ssh_run = mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", return_value=_Proc()
    )

    _record_rotation(host, "tok-new")

    # Expect two writes: last_rotation_at, then bootstrap_token_id.
    assert ssh_run.call_count == 2
    calls = [call.args for call in ssh_run.call_args_list]
    # (host, user, command)
    assert calls[0][0] == "incus-host"
    assert calls[0][1] == "ubuntu"
    assert "incus config set lxc-1 user.remo.last_rotation_at" in calls[0][2]
    assert "incus config set lxc-1 user.remo.bootstrap_token_id" in calls[1][2]
    assert "tok-new" in calls[1][2]


def test_read_rotation_metadata_incus_config(mocker):
    from remo_cli.cli.rotate import _read_rotation_metadata
    host = KnownHost(
        type="incus",
        name="incus-host/lxc-1",
        host="lxc-1",
        user="remo",
        instance_id="ubuntu",
        access_mode="direct",
    )

    def _run(h, u, cmd):
        class _Proc:
            returncode = 0
            stderr = ""
            stdout = ""

        p = _Proc()
        if "rotation_cadence_days" in cmd:
            p.stdout = "14\n"
        elif "last_rotation_at" in cmd:
            p.stdout = "2026-05-26T12:00:00+00:00\n"
        elif "bootstrap_token_id" in cmd:
            p.stdout = "tok-current\n"
        return p

    mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", side_effect=_run
    )

    cadence, last, token_id = _read_rotation_metadata(host)
    assert cadence == 14
    assert last is not None
    assert last.tzinfo is not None
    assert token_id == "tok-current"


def test_read_rotation_metadata_incus_missing_keys_default(mocker):
    from remo_cli.cli.rotate import _read_rotation_metadata
    host = KnownHost(
        type="incus",
        name="incus-host/lxc-1",
        host="lxc-1",
        user="remo",
        instance_id="ubuntu",
        access_mode="direct",
    )

    class _Proc:
        returncode = 0
        stderr = ""
        stdout = ""

    mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", return_value=_Proc()
    )

    cadence, last, token_id = _read_rotation_metadata(host)
    assert cadence == 7
    assert last is None
    assert token_id is None


def test_rotate_no_backend_fails(tmp_config_dir, mocker, monkeypatch):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    monkeypatch.delenv("REMO_BROKER_BACKEND", raising=False)
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(days=10), None),
    )
    runner = CliRunner()
    r = runner.invoke(rotate_command, ["hetz-1"])
    assert r.exit_code == 7
    assert "REMO_BROKER_BACKEND" in r.output
