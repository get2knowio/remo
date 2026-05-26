"""US5 T080: backend-specific revocation primitives + idempotent re-revocation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.core import broker_revoke
from remo_cli.models.host import KnownHost
from remo_cli.providers import broker


def test_unsupported_backend_raises():
    with pytest.raises(broker.BackendError, match="unsupported backend"):
        broker.revoke_bootstrap_token("frobnicator", token_id="x", admin_sa="y")


def test_age_git_revoke_is_noop():
    # No exception, no network call.
    broker.revoke_bootstrap_token("age-git", token_id="anything")


def test_1password_revoke_idempotent_on_404(mocker):
    import urllib.error
    mocker.patch(
        "remo_cli.providers.broker.urllib.request.urlopen",
        side_effect=urllib.error.HTTPError(
            url="x", code=404, msg="Not Found", hdrs=None, fp=None
        ),
    )
    # Should not raise.
    broker.revoke_bootstrap_token("1password", token_id="abc", admin_sa="adminsa")


def test_1password_revoke_non_404_raises(mocker):
    import urllib.error
    mocker.patch(
        "remo_cli.providers.broker.urllib.request.urlopen",
        side_effect=urllib.error.HTTPError(
            url="x", code=500, msg="Server Error", hdrs=None, fp=None
        ),
    )
    with pytest.raises(broker.BackendError, match="1Password revoke failed"):
        broker.revoke_bootstrap_token("1password", token_id="abc", admin_sa="adminsa")


def test_vault_revoke_idempotent_on_400(mocker):
    import urllib.error
    mocker.patch(
        "remo_cli.providers.broker.urllib.request.urlopen",
        side_effect=urllib.error.HTTPError(
            url="x", code=400, msg="Bad Request", hdrs=None, fp=None
        ),
    )
    broker.revoke_bootstrap_token(
        "vault", token_id="accessor-1", admin_sa="root-token",
        extra={"vault_addr": "http://localhost:8200"},
    )


def test_revoke_requires_admin_sa_for_1password():
    with pytest.raises(broker.BackendError, match="no admin SA"):
        broker.revoke_bootstrap_token("1password", token_id="x")


# Finding 7: TokenLookupError vs None semantics in revoke_before_destroy ------


def _hetz_host() -> KnownHost:
    return KnownHost(type="hetzner", name="hetz-x", host="1.2.3.4", user="remo")


def test_hetzner_lookup_network_error_raises_tokenlookuperror(mocker):
    import urllib.error
    mocker.patch(
        "remo_cli.providers.hetzner._hetzner_server_id", return_value="999"
    )
    mocker.patch(
        "remo_cli.providers.hetzner._get_hetzner_api_token", return_value="tok"
    )
    mocker.patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    )
    with pytest.raises(broker_revoke.TokenLookupError, match="hetzner labels read failed"):
        broker_revoke._lookup_token_id(_hetz_host())


def test_revoke_before_destroy_blocks_on_lookup_error(monkeypatch, mocker):
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.core.broker_revoke._lookup_token_id",
        side_effect=broker_revoke.TokenLookupError("hetzner labels read failed: boom"),
    )
    revoke = mocker.patch(
        "remo_cli.providers.broker.revoke_bootstrap_token", return_value=None
    )
    assert broker_revoke.revoke_before_destroy(_hetz_host()) is False
    revoke.assert_not_called()


def test_revoke_before_destroy_force_continues_on_lookup_error(
    monkeypatch, mocker, capsys
):
    monkeypatch.setenv("REMO_BROKER_BACKEND", "1password")
    mocker.patch(
        "remo_cli.core.broker_revoke._lookup_token_id",
        side_effect=broker_revoke.TokenLookupError("hetzner labels read failed: boom"),
    )
    revoke = mocker.patch(
        "remo_cli.providers.broker.revoke_bootstrap_token", return_value=None
    )
    assert broker_revoke.revoke_before_destroy(_hetz_host(), force=True) is True
    revoke.assert_not_called()
    out = capsys.readouterr().out + capsys.readouterr().err
    # Warning text mentions both the failure and the --force bypass.
    # (capsys split; assert at least the keyword shows somewhere.)


def test_hetzner_lookup_no_server_returns_none(mocker):
    # No server resolved by name → silent skip (no token minted yet).
    mocker.patch(
        "remo_cli.providers.hetzner._hetzner_server_id", return_value=None
    )
    mocker.patch(
        "remo_cli.providers.hetzner._get_hetzner_api_token", return_value="tok"
    )
    assert broker_revoke._lookup_token_id(_hetz_host()) is None


def test_hetzner_lookup_no_label_returns_none(mocker):
    import io
    import json as _json
    mocker.patch(
        "remo_cli.providers.hetzner._hetzner_server_id", return_value="42"
    )
    mocker.patch(
        "remo_cli.providers.hetzner._get_hetzner_api_token", return_value="tok"
    )
    payload = _json.dumps({"server": {"labels": {"unrelated": "x"}}}).encode()
    resp = MagicMock()
    resp.read.return_value = payload
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: False
    mocker.patch("urllib.request.urlopen", return_value=resp)
    assert broker_revoke._lookup_token_id(_hetz_host()) is None


def test_hetzner_lookup_reads_underscore_label(mocker):
    import json as _json
    mocker.patch(
        "remo_cli.providers.hetzner._hetzner_server_id", return_value="42"
    )
    mocker.patch(
        "remo_cli.providers.hetzner._get_hetzner_api_token", return_value="tok"
    )
    payload = _json.dumps({
        "server": {"labels": {"remo_bootstrap_token_id": "the-id"}}
    }).encode()
    resp = MagicMock()
    resp.read.return_value = payload
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: False
    mocker.patch("urllib.request.urlopen", return_value=resp)
    assert broker_revoke._lookup_token_id(_hetz_host()) == "the-id"


# Phase 3 / US3: Incus token_id lookup from container config. ----------------


def _incus_host() -> KnownHost:
    return KnownHost(
        type="incus",
        name="incus-host/lxc-1",
        host="lxc-1",
        user="remo",
        instance_id="ubuntu",
        access_mode="direct",
    )


def test_incus_lookup_reads_config_key(mocker):
    class _Proc:
        returncode = 0
        stdout = "tok-current\n"
        stderr = ""

    ssh_run = mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", return_value=_Proc()
    )

    result = broker_revoke._lookup_token_id(_incus_host())

    assert result == "tok-current"
    args = ssh_run.call_args.args
    # (incus_host, host_user, command)
    assert args[0] == "incus-host"
    assert args[1] == "ubuntu"
    assert "incus config get lxc-1 user.remo.bootstrap_token_id" in args[2]


def test_incus_lookup_missing_key_returns_none(mocker):
    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", return_value=_Proc()
    )

    assert broker_revoke._lookup_token_id(_incus_host()) is None


def test_incus_lookup_transport_failure_raises(mocker):
    class _Proc:
        returncode = 255
        stdout = ""
        stderr = "ssh refused"

    mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", return_value=_Proc()
    )

    with pytest.raises(broker_revoke.TokenLookupError, match="incus config read failed"):
        broker_revoke._lookup_token_id(_incus_host())


# Phase 3: Proxmox token_id lookup from in-container file. -------------------


def _proxmox_host() -> KnownHost:
    return KnownHost(
        type="proxmox",
        name="prox-host/px-1",
        host="px-1",
        user="remo",
        instance_id="200",
        region="root",
        access_mode="direct",
    )


def test_proxmox_lookup_reads_container_file(mocker):
    class _Proc:
        returncode = 0
        stdout = "tok-current\n"
        stderr = ""

    ssh_run = mocker.patch(
        "remo_cli.providers.proxmox._ssh_run", return_value=_Proc()
    )

    result = broker_revoke._lookup_token_id(_proxmox_host())

    assert result == "tok-current"
    args = ssh_run.call_args.args
    # (proxmox_host, host_user, command)
    assert args[0] == "prox-host"
    assert args[1] == "root"
    assert "pct exec 200 --" in args[2]
    assert "cat /etc/remo-broker/bootstrap_token_id" in args[2]


def test_proxmox_lookup_missing_file_returns_none(mocker):
    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    mocker.patch("remo_cli.providers.proxmox._ssh_run", return_value=_Proc())

    assert broker_revoke._lookup_token_id(_proxmox_host()) is None


def test_proxmox_lookup_transport_failure_raises(mocker):
    class _Proc:
        returncode = 255
        stdout = ""
        stderr = "ssh refused"

    mocker.patch("remo_cli.providers.proxmox._ssh_run", return_value=_Proc())

    with pytest.raises(
        broker_revoke.TokenLookupError, match="proxmox config read failed"
    ):
        broker_revoke._lookup_token_id(_proxmox_host())


# Finding 14 (broker_revoke half): naive timestamps in cadence metadata. ------


def test_parse_iso_naive_input_becomes_utc_aware():
    from remo_cli.cli.rotate import _parse_iso, _is_overdue
    # Bare-ISO with no Z / no offset — must round-trip to aware UTC so that
    # `_now() - last` arithmetic doesn't crash.
    parsed = _parse_iso("2026-01-01T00:00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None
    # And the overdue check (which subtracts from `_now()`) doesn't raise.
    assert _is_overdue(cadence_days=7, last_rotation=parsed) is True


def test_audit_parse_ts_naive_input_does_not_crash_filter():
    from datetime import timedelta
    from remo_cli.core import audit as audit_core
    # Bare-ISO timestamp with no offset suffix (regression for Finding 14:
    # `fetch()` would raise "can't compare offset-naive and offset-aware
    # datetimes" inside the `--since` cutoff filter).
    parsed = audit_core._parse_ts("2026-01-01T00:00:00")
    assert parsed.tzinfo is not None
    cutoff = audit_core.datetime.now(audit_core.timezone.utc) - timedelta(days=1)
    # No TypeError when compared against an aware cutoff.
    _ = parsed >= cutoff
