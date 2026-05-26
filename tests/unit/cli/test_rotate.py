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

    runner = CliRunner()
    r = runner.invoke(rotate_command, ["hetz-1", "--force"])
    assert r.exit_code == 0
    mint.assert_called_once()
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

    runner = CliRunner()
    r = runner.invoke(rotate_command, ["--all"])
    assert r.exit_code == 7


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
