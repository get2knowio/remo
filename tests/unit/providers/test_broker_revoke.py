"""US5 T080: backend-specific revocation primitives + idempotent re-revocation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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
