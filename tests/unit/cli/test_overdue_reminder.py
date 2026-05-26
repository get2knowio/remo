"""US5 T083b: passive overdue-rotation reminder."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from remo_cli.core import broker_revoke
from remo_cli.core.known_hosts import save_known_host
from remo_cli.models.host import KnownHost


def test_no_known_hosts_no_reminder(tmp_config_dir):
    assert broker_revoke.overdue_reminders() == []


def test_not_overdue_no_reminder(tmp_config_dir, mocker):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(days=1), "tok"),
    )
    assert broker_revoke.overdue_reminders() == []


def test_overdue_emits_reminder(tmp_config_dir, mocker):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(days=10), "tok"),
    )
    reminders = broker_revoke.overdue_reminders()
    assert len(reminders) == 1
    assert "hetz-1" in reminders[0]
    assert "overdue" in reminders[0]


def test_cadence_zero_suppresses_reminder(tmp_config_dir, mocker):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(0, None, None),
    )
    assert broker_revoke.overdue_reminders() == []


def test_quiet_env_suppresses_reminder(tmp_config_dir, mocker, monkeypatch):
    save_known_host(KnownHost(type="hetzner", name="hetz-1", host="1.1.1.1", user="remo"))
    mocker.patch(
        "remo_cli.cli.rotate._read_rotation_metadata",
        return_value=(7, datetime.now(timezone.utc) - timedelta(days=10), None),
    )
    monkeypatch.setenv("REMO_QUIET", "1")
    assert broker_revoke.overdue_reminders() == []
