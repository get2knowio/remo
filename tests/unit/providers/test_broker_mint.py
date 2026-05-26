"""US5 T081: per-backend mint shape + admin SA lookup via fnox."""

from __future__ import annotations

import io
import json

import pytest

from remo_cli.providers import broker


class _MockResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_aws_sm_mint_returns_per_instance_role_name():
    out = broker.mint_bootstrap_token(
        "aws-sm", instance_id="i-abc", dev_id="alice"
    )
    assert out["token"] == ""
    assert out["token_id"] == "remo-broker-instance-alice-i-abc"


def test_aws_sm_mint_sanitizes_instance_id():
    out = broker.mint_bootstrap_token(
        "aws-sm", instance_id="web/1 prod!", dev_id="alice"
    )
    # Slashes/spaces/bangs are all replaced; result stays within IAM's allowed set.
    assert out["token_id"] == "remo-broker-instance-alice-web-1-prod-"


def test_aws_sm_mint_truncates_long_instance_id():
    out = broker.mint_bootstrap_token(
        "aws-sm", instance_id="a" * 100, dev_id="alice"
    )
    # Slug capped at 32 chars to leave headroom under IAM's 64-char role-name limit.
    assert out["token_id"] == "remo-broker-instance-alice-" + ("a" * 32)


def test_onepassword_mint_does_not_leak_token_in_error(mocker):
    secret = "secret-token-value-DO-NOT-LEAK"
    mocker.patch(
        "remo_cli.providers.broker.urllib.request.urlopen",
        return_value=_MockResp({"unexpected_key": secret}),
    )
    with pytest.raises(broker.BackendError) as exc_info:
        broker.mint_bootstrap_token(
            "1password", instance_id="i-1", dev_id="alice", admin_sa="adminsa"
        )
    assert secret not in str(exc_info.value)
    # The diagnostic *should* surface key names so schema drift is debuggable.
    assert "unexpected_key" in str(exc_info.value)


def test_vault_mint_does_not_leak_token_in_error(mocker):
    secret = "secret-token-value-DO-NOT-LEAK"
    # Token present at the top level under a wrong key, and inside `auth` under
    # a wrong key — both must stay out of the exception message.
    payload = {
        "unexpected_key": secret,
        "auth": {"wrong_token_key": secret, "wrong_accessor_key": "acc"},
    }
    mocker.patch(
        "remo_cli.providers.broker.urllib.request.urlopen",
        return_value=_MockResp(payload),
    )
    with pytest.raises(broker.BackendError) as exc_info:
        broker.mint_bootstrap_token(
            "vault", instance_id="i-2", dev_id="alice", admin_sa="root",
        )
    assert secret not in str(exc_info.value)
    assert "unexpected_key" in str(exc_info.value)
    assert "wrong_token_key" in str(exc_info.value)


def test_age_git_mint_rejects():
    with pytest.raises(broker.BackendError, match="age \+ git"):
        broker.mint_bootstrap_token("age-git", instance_id="x", dev_id="alice")


def test_1password_mint_returns_token_and_id(mocker):
    mocker.patch(
        "remo_cli.providers.broker.urllib.request.urlopen",
        return_value=_MockResp({"id": "scim-123", "token": "ops_secret"}),
    )
    out = broker.mint_bootstrap_token(
        "1password", instance_id="i-1", dev_id="alice", admin_sa="adminsa"
    )
    assert out == {"token": "ops_secret", "token_id": "scim-123"}


def test_vault_mint_returns_token_and_accessor(mocker):
    payload = {
        "auth": {"client_token": "hvs.AAA", "accessor": "accessor-1"},
    }
    mocker.patch(
        "remo_cli.providers.broker.urllib.request.urlopen",
        return_value=_MockResp(payload),
    )
    out = broker.mint_bootstrap_token(
        "vault", instance_id="i-2", dev_id="alice", admin_sa="root",
        extra={"vault_addr": "http://localhost:8200"},
    )
    assert out == {"token": "hvs.AAA", "token_id": "accessor-1"}


def test_mint_reads_admin_sa_from_fnox(mocker):
    mocker.patch("remo_cli.core.fnox.is_installed", return_value=True)
    mocker.patch("remo_cli.core.fnox.get", return_value="adminsa-from-fnox")
    mocker.patch(
        "remo_cli.providers.broker.urllib.request.urlopen",
        return_value=_MockResp({"id": "scim-1", "token": "tok"}),
    )
    out = broker.mint_bootstrap_token(
        "1password", instance_id="i-3", dev_id="alice",
        admin_sa_fnox_key="op_admin_sa",
    )
    assert out["token_id"] == "scim-1"


def test_mint_fnox_lookup_failure_is_BackendError(mocker):
    mocker.patch("remo_cli.core.fnox.is_installed", return_value=True)
    from remo_cli.core import fnox as fnox_mod
    mocker.patch(
        "remo_cli.core.fnox.get",
        side_effect=fnox_mod.FnoxError("no such key"),
    )
    with pytest.raises(broker.BackendError, match="could not read admin SA"):
        broker.mint_bootstrap_token(
            "1password", instance_id="i", dev_id="alice",
            admin_sa_fnox_key="missing",
        )
