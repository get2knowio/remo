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
